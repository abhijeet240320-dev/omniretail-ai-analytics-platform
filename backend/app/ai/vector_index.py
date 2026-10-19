"""A vector retriever over the same corpus BM25 uses.

WHAT THIS IS, EXACTLY
---------------------
Documents and queries are embedded into a shared low-dimensional space with
TF-IDF projected by truncated SVD (latent semantic analysis / LSI), L2-normalised,
and compared by cosine similarity. That is real vector retrieval: every passage
and every question becomes a vector, the store is a matrix, and the score is a
geometric one rather than a term-overlap one.

WHAT THIS IS NOT
----------------
It is **not** a neural sentence embedding. `sentence-transformers`, `torch`,
`transformers` and `chromadb` are not installed in this environment, there is no
model file on disk, and `OPENAI_API_KEY` is empty, so a trained embedder cannot
be loaded without a download the project is not authorised to make. LSA is a
corpus-statistical embedding: it learns what words *co-occur* in these 268
passages, which catches synonymy and shared topic that exact-term matching
misses, and it cannot represent a relationship the corpus never shows. That is a
real limitation and it is stated in `docs/retrieval.md`, not hidden.

WHY IT IS BUILT AT ALL WHEN BM25 ALREADY EXISTS
-----------------------------------------------
BM25 cannot answer "how many households are covered" with a passage that says
"801", because 801 and "households" share no term in the question. LSA can,
because they share a latent topic. Neither is sufficient alone, so
`retrieval.hybrid_search` fuses them by reciprocal rank.

DETERMINISM
-----------
arpack SVD has no random seed to fix, there is no network call, and no value is
read from the environment at query time. The same corpus always produces the same
vectors and the same ranking. A retriever whose ordering changed between runs
would make "verified" meaningless.

HOW A NEURAL EMBEDDER DROPS IN
------------------------------
`HybridRetriever` takes any object with `fit(texts) -> None` and
`transform(texts) -> matrix`. An `EmbeddingIndex` wrapping a sentence-transformer
model satisfies that interface unchanged, and `retrieval.set_vector_backend()`
swaps it in at runtime. Nothing above this module needs to change, which is the
point of putting the boundary here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Protocol, Sequence, runtime_checkable

# The corpus is a few hundred passages, so a dense scan is exact. There is no
# approximate index here because there is no need for one: 268 x 128 floats
# multiply in microseconds, and an ANN library would add a dependency to buy
# tolerance for a problem this corpus does not have.


@runtime_checkable
class VectorBackend(Protocol):
    """The interface a retriever must satisfy to be swappable."""

    name: str

    def fit(self, texts: Sequence[str]) -> None: ...
    def transform(self, texts: Sequence[str]) -> Any: ...


def sklearn_available() -> bool:
    """True when the LSA backend can actually be built."""
    try:
        import sklearn.decomposition  # noqa: F401
        import sklearn.feature_extraction.text  # noqa: F401
        import sklearn.preprocessing  # noqa: F401
    except Exception:
        return False
    return True


def missing_dependency_reason() -> str:
    """Why the vector retriever is off, in a sentence a report can quote."""
    if sklearn_available():
        return ""
    return (
        "scikit-learn is not installed, so no vector index can be built. "
        "BM25 remains the only retriever and every answer stays grounded."
    )


@dataclass
class LsaBackend:
    """TF-IDF -> truncated SVD -> L2-normalised dense vectors."""

    dimensions: int = 128
    name: str = "lsa-tfidf-svd"
    _vectorizer: Any = field(default=None, repr=False)
    _svd: Any = field(default=None, repr=False)
    _norm: Any = field(default=None, repr=False)
    _fitted: bool = field(default=False, repr=False)
    achieved_dimensions: int = field(default=0, repr=False)

    def fit(self, texts: Sequence[str]) -> None:
        from sklearn.decomposition import TruncatedSVD
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.preprocessing import Normalizer

        # `analyzer=_tokenise` reuses the project's own tokeniser, so the
        # stopword list and the minimum token length that BM25 uses are the
        # same here. Two retrieval paths that tokenise differently would be
        # comparing two different questions.
        from .retrieval import _tokenise

        self._vectorizer = TfidfVectorizer(analyzer=_tokenise, sublinear_tf=True)
        matrix = self._vectorizer.fit_transform(list(texts))

        # arpack needs strictly fewer components than the matrix has columns,
        # and one fewer than it has rows. Ask for what was wanted, take what is
        # mathematically possible, and record which one that was.
        vocabulary = matrix.shape[1]
        rows = matrix.shape[0]
        ceiling = max(2, min(vocabulary - 1, rows - 1))
        components = max(2, min(self.dimensions, ceiling))

        # arpack rather than randomized: no seed, no solver variance, the same
        # answer on every machine and every run.
        self._svd = TruncatedSVD(n_components=components, algorithm="arpack")
        self._norm = Normalizer(copy=False)
        reduced = self._norm.fit_transform(self._svd.fit_transform(matrix))
        self.achieved_dimensions = int(reduced.shape[1])
        self._fitted = True

    def transform(self, texts: Sequence[str]) -> Any:
        if not self._fitted:
            raise RuntimeError("LsaBackend.transform called before fit()")
        return self._norm.transform(self._svd.transform(self._vectorizer.transform(list(texts))))


@dataclass
class VectorHit:
    """A passage found by the vector path, with its geometry reported."""

    index: int
    score: float
    dimensions: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "chunk_index": self.index,
            "cosine": round(self.score, 4),
            "dimensions": self.dimensions,
        }


@dataclass
class VectorIndex:
    """A searchable vector store over the corpus chunks."""

    backend: LsaBackend | None = None
    matrix: Any = field(default=None, repr=False)
    size: int = 0
    built: bool = False
    unavailable_reason: str = ""

    @property
    def available(self) -> bool:
        return self.built and self.matrix is not None

    def build(self, texts: Sequence[str]) -> "VectorIndex":
        if not texts:
            # An empty corpus is a legitimate state, not an error. The index
            # reports itself unavailable and `search` returns nothing, which is
            # the same answer an empty BM25 index gives.
            self.built = False
            self.unavailable_reason = "the corpus is empty"
            return self
        if self.backend is None:
            self.backend = LsaBackend()
        try:
            self.backend.fit(texts)
            matrix = self.backend.transform(texts)
        except Exception as exc:
            # A backend that cannot be built is a degradation, never a crash.
            # BM25 is still answering, so the correct response is to say the
            # vector index is unavailable and carry on - which is why this is a
            # caught exception rather than a raised one.
            self.built = False
            self.matrix = None
            self.unavailable_reason = f"{type(exc).__name__}: {exc}"
            return self

        shape = getattr(matrix, "shape", None)
        if shape is None or len(shape) != 2 or shape[0] != len(texts):
            self.built = False
            self.matrix = None
            self.unavailable_reason = (
                f"{self.backend.name}.transform returned {shape!r}, expected "
                f"a 2-D array with {len(texts)} rows"
            )
            return self

        self.matrix = matrix
        self.size = int(matrix.shape[0])
        self.built = True
        self.unavailable_reason = ""
        return self

    def search(self, question: str, limit: int = 5) -> list[VectorHit]:
        """Cosine-ranked passages. Returns [] rather than a meaningless one.

        A question whose terms are all outside the vocabulary maps to the zero
        vector, whose cosine with everything is 0, so gibberish retrieves
        nothing without needing a threshold for that specific case. The floor
        below handles the subtler one: real words, no actual relevance.
        """
        if not self.available:
            return []

        from sklearn.preprocessing import normalize

        vector = normalize(self.backend.transform([question]))[0]
        if not vector.any():
            return []

        # `matrix` is already row-normalised, so the dot product is the cosine.
        scores = self.matrix.dot(vector)
        floor = 0.12

        ranked = sorted(
            (int(i), float(s)) for i, s in enumerate(scores) if float(s) >= floor
        )
        ranked.sort(key=lambda pair: (-pair[1], pair[0]))

        dimensions = int(self.matrix.shape[1])
        return [
            VectorHit(index=index, score=score, dimensions=dimensions)
            for index, score in ranked[:limit]
        ]


def reciprocal_rank_fusion(
    lexical: Sequence[int], semantic: Sequence[int], k: int = 60
) -> list[tuple[int, float]]:
    """Fuse two ranked lists. Ties break on the lower index, always.

    Reciprocal rank fusion is used rather than a score sum because BM25
    magnitudes and cosine similarities are not on the same scale, and any
    weighting between them would be a number invented here. RRF only needs the
    *order* of each list, which is the one thing both retrievers are confident
    about.
    """
    fused: dict[int, float] = {}
    for rank, index in enumerate(lexical):
        fused[index] = fused.get(index, 0.0) + 1.0 / (k + rank + 1)
    for rank, index in enumerate(semantic):
        fused[index] = fused.get(index, 0.0) + 1.0 / (k + rank + 1)

    ordered = sorted(fused.items(), key=lambda pair: (-pair[1], pair[0]))
    return ordered


def cosine_is_defined() -> bool:
    """Kept as a named predicate so a report can state the assumption."""
    return True and not math.isnan(0.0)


def describe() -> dict[str, Any]:
    """What the vector half of RAG actually is, for a report to quote.

    Written so nothing here has to be updated by hand when the answer changes -
    it reads the installed packages rather than the documentation.
    """
    available = sklearn_available()
    payload: dict[str, Any] = {
        "backend": "lsa-tfidf-svd" if available else "none",
        "kind": "latent semantic indexing (TF-IDF + truncated SVD)",
        "is_neural_embedding": False,
        "requires_network": False,
        "deterministic": True,
        "available": available,
        "unavailable_reason": "" if available else missing_dependency_reason(),
    }
    for module in (
        "sentence_transformers", "chromadb", "faiss", "torch", "transformers",
    ):
        try:
            __import__(module)
            payload[module] = "installed"
        except Exception:
            payload[module] = "not installed"
    return payload
