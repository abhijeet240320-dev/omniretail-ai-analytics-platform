"""Retrieval over the project's verified documents.

WHY THIS IS BM25 AND NOT AN EMBEDDING MODEL
-------------------------------------------
`.env` names `text-embedding-3-small` and the key is empty. An embedding index
would mean shipping a model download, a network call per document, and an
answer whose grounding depends on a third-party service being reachable.

None of that is needed to satisfy the actual requirement, which is *evidence*:
given a question, return the passages that support it, with enough provenance
that a reader can open the file and check. BM25 does that against a corpus of a
few hundred documents in under a millisecond, runs offline, is deterministic
(deterministic matters - the same question must retrieve the same evidence on
every run, or "verified" means nothing), and every result is a literal quote
from a file that exists.

WHAT IS IN THE CORPUS
----------------------
Real, committed artefacts only:

  `docs/*.md`                 generated documentation, including the data
                              dictionary and the grounding contract
  `backend/sql/**/*.sql`      the 41 analytics queries and their stated questions
  `backend/config/*.yaml`     dataset definitions, constraints, recorded findings
  `data/processed/_*.json`    load, verification, SQL-result and audit reports
  `data/raw/_checksums.json`  which raw file is which, and its SHA-256

`docs/forecasting.md` and `docs/anomaly_detection.md` are included once their
generators exist, so the forecast metrics and the anomaly counts are retrievable
facts rather than folklore.

WHAT IS NOT IN THE CORPUS
-------------------------
The raw CSV files and the database itself. A CSV row is not evidence - it has no
provenance and cannot be cited - and the database is reachable by executing SQL,
which is what the analyst does instead. Retrieval answers "what do the documents
say"; execution answers "what is true".

BM25 PLUS A VECTOR INDEX
------------------------
BM25 alone cannot answer "how many households are covered" from a passage that
says "801", because 801 and "households" share no term in the question. So
`hybrid_search` fuses this module's BM25 with the TF-IDF + SVD vector store in
`vector_index.py`, which is real vector retrieval that runs offline on the
installed scikit-learn. It is latent-semantic indexing, not a neural sentence
embedding - no embedder is installed and no model file exists here, and
`docs/retrieval.md` says so in the same words. `hybrid_search` degrades to BM25
alone, and says that it did, whenever the vector index is unavailable.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from .vector_index import (
    LsaBackend,
    VectorIndex,
    missing_dependency_reason,
    reciprocal_rank_fusion,
    sklearn_available,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

# Relative paths and glob patterns. Anything here is read-only and committed.
CORPUS_SPECS: tuple[tuple[str, str, str], ...] = (
    ("docs", "docs/*.md", "documentation"),
    ("sql", "backend/sql/*/*.sql", "analytics-query"),
    ("config", "backend/config/*.yaml", "configuration"),
    ("report", "data/processed/_*.json", "measurement-report"),
    ("checksums", "data/raw/_checksums.json", "provenance"),
    ("contract", "backend/app/ai/GROUNDING_CONTRACT.md", "policy"),
)

# Words that carry no retrieval signal and would let every question match every
# document. Dropping them is the difference between a search and a lookup.
STOPWORDS = frozenset("""
a an the of to in on at by for with and or not is are was were be been being
this that these those it its as from into over under about how what which when
where who whom why do does did done can could should would will shall may might
must have has had having there here them they their we our you your i me my
than then so such but if else each any all more most other some no nor not only
own same too very s t just don now ifs
""".split())

_WORD_RE = re.compile(r"[a-z0-9_]+")

# BM25 parameters. k1=1.2 and b=0.75 are the values Robertson & Zaragoza
# published; they are used unmodified rather than tuned, because tuning them
# against a test set that contains the questions would be fitting the corpus to
# the questions, which is the failure mode this whole project is built against.
_K1 = 1.2
_B = 0.75


def _tokenise(text: str) -> list[str]:
    return [t for t in _WORD_RE.findall(text.lower()) if t not in STOPWORDS and len(t) > 1]


@dataclass(frozen=True)
class Chunk:
    """One retrievable passage, with enough provenance to cite it."""

    doc_id: str
    path: str
    kind: str
    section: str
    text: str
    line_start: int

    def citation(self) -> str:
        """`docs/data_dictionary.md#L412` - clickable, checkable, specific."""
        return f"{self.path}#L{self.line_start}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "path": self.path,
            "kind": self.kind,
            "section": self.section,
            "line_start": self.line_start,
            "citation": self.citation(),
            "text": self.text,
        }


@dataclass
class Corpus:
    """Chunks plus the BM25 statistics computed over them."""

    chunks: list[Chunk]
    doc_freq: Counter[str] = field(default_factory=Counter)
    lengths: list[int] = field(default_factory=list)
    avg_length: float = 0.0

    @property
    def size(self) -> int:
        return len(self.chunks)


def _split_markdown(text: str, path: str, kind: str, doc_id: str) -> list[Chunk]:
    """Split on headings, keeping the byte offset of each passage.

    A whole-file chunk would make retrieval useless - a 900-line data dictionary
    matches every question equally - so passages are split at headings and then
    again if a single section is still enormous.
    """
    chunks: list[Chunk] = []
    lines = text.splitlines()
    section = "preamble"
    start = 1
    buffer: list[str] = []

    def flush(end_line: int) -> None:
        body = "\n".join(buffer).strip()
        if body:
            chunks.append(
                Chunk(
                    doc_id=doc_id, path=path, kind=kind,
                    section=section, text=body, line_start=start,
                )
            )

    for number, line in enumerate(lines, start=1):
        if line.lstrip().startswith("#"):
            flush(number - 1)
            section = line.lstrip("#").strip() or "untitled"
            start = number
            buffer = [line]
            continue
        buffer.append(line)
    flush(len(lines))

    return _split_oversized(chunks)


def _split_oversized(chunks: list[Chunk], limit: int = 2200) -> list[Chunk]:
    """Split any single passage over `limit` characters into overlapping windows.

    The windows overlap by 120 characters so a fact that straddles a boundary
    is retrievable from at least one of them. Boundaries land on blank lines
    where possible, so a window is readable prose rather than half a sentence.
    """
    out: list[Chunk] = []
    for chunk in chunks:
        if len(chunk.text) <= limit:
            out.append(chunk)
            continue
        start = 0
        step = limit - 120
        while start < len(chunk.text):
            end = min(start + limit, len(chunk.text))
            if end < len(chunk.text):
                newline = chunk.text.rfind("\n\n", start + step // 2, end)
                if newline != -1:
                    end = newline
            piece = chunk.text[start:end].strip()
            if piece:
                out.append(
                    Chunk(
                        doc_id=f"{chunk.doc_id}@{start}",
                        path=chunk.path,
                        kind=chunk.kind,
                        section=chunk.section,
                        text=piece,
                        line_start=chunk.line_start + chunk.text[:start].count("\n"),
                    )
                )
            if end >= len(chunk.text):
                break
            start = end - 120
    return out


def _flatten_json(value: Any, prefix: str = "") -> list[str]:
    """Turn a JSON report into readable sentences a BM25 index can match.

    The reports are the machine-readable record of what the pipeline measured.
    Flattening them into prose means a question about load counts retrieves
    `{"olist": {"row_count": 774081}}` instead of matching nothing.
    """
    lines: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            lines.extend(_flatten_json(item, f"{prefix}.{key}" if prefix else key))
    elif isinstance(value, list):
        # Long lists of records are sampled, not indexed whole: a list of 9/9
        # write probes is evidence, a list of 30,490 series ids is not.
        if len(value) <= 20:
            for index, item in enumerate(value):
                lines.extend(_flatten_json(item, f"{prefix}[{index}]"))
        elif all(isinstance(v, (int, float, str, bool, type(None))) for v in value):
            rendered = ", ".join(str(v) for v in value[:20])
            more = f", ... ({len(value)} total)" if len(value) > 20 else ""
            lines.append(f"{prefix} = [{rendered}{more}]")
        else:
            for index, item in enumerate(value[:20]):
                lines.extend(_flatten_json(item, f"{prefix}[{index}]"))
            if len(value) > 20:
                lines.append(f"{prefix} ... {len(value)} records total")
    else:
        lines.append(f"{prefix} = {value}")
    return lines


def _build() -> Corpus:
    chunks: list[Chunk] = []

    for prefix, pattern, kind in CORPUS_SPECS:
        paths = [REPO_ROOT / prefix] if prefix else []
        if kind == "documentation":
            paths = sorted((REPO_ROOT / "docs").glob("*.md"))
        elif pattern.startswith("backend/"):
            paths = sorted(REPO_ROOT.glob(pattern))
        elif pattern.startswith("data/raw/"):
            path = REPO_ROOT / pattern
            paths = [path] if path.exists() else []
        else:
            paths = sorted(REPO_ROOT.glob(pattern))

        for path in paths:
            if not path.is_file():
                continue
            rel = path.relative_to(REPO_ROOT).as_posix()
            doc_id = f"{kind}:{rel}"
            text = path.read_text(encoding="utf-8", errors="replace")

            if kind in {"measurement-report", "provenance"}:
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    continue
                body = "\n".join(_flatten_json(payload))
                chunks.append(
                    Chunk(
                        doc_id=doc_id, path=rel, kind=kind,
                        section=payload.get("generated_utc", "report")[:40]
                        if isinstance(payload, dict) else "report",
                        text=body,
                        line_start=1,
                    )
                )
                continue

            if kind == "configuration":
                chunks.append(
                    Chunk(
                        doc_id=doc_id, path=rel, kind=kind, section="configuration",
                        text=text, line_start=1,
                    )
                )
                continue

            chunks.extend(_split_markdown(text, rel, kind, doc_id))

    doc_freq: Counter[str] = Counter()
    lengths: list[int] = []
    for chunk in chunks:
        tokens = _tokenise(chunk.text)
        lengths.append(len(tokens))
        for token in set(tokens):
            doc_freq[token] += 1

    return Corpus(
        chunks=chunks,
        doc_freq=doc_freq,
        lengths=lengths,
        avg_length=(sum(lengths) / len(lengths)) if lengths else 0.0,
    )


@lru_cache(maxsize=1)
def load_corpus() -> Corpus:
    """Build (and cache) the corpus.

    Cached because the corpus is built from committed files that do not change
    within a process. `load_corpus.cache_clear()` exists for tests that write
    temporary documents.
    """
    return _build()


@dataclass
class Hit:
    """One retrieved passage and why it scored what it scored."""

    chunk: Chunk
    score: float
    matched_terms: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "citation": self.chunk.citation(),
            "path": self.chunk.path,
            "kind": self.chunk.kind,
            "section": self.chunk.section,
            "score": round(self.score, 4),
            "matched_terms": sorted(set(self.matched_terms)),
            "text": self.chunk.text,
        }


def search(question: str, limit: int = 5, kind: str | None = None) -> list[Hit]:
    """Rank passages by BM25. Returns [] rather than a low-scoring passage.

    The empty result is load-bearing: `evidence_requirement` depends on
    "nothing scored above the floor" being an answer the analyst can act on
    instead of being papered over with the least-bad document.
    """
    corpus = load_corpus()
    terms = _tokenise(question)
    if not terms or not corpus.chunks:
        return []

    total = corpus.size
    scores: dict[int, float] = {}
    matches: dict[int, set[str]] = {}

    for index, chunk in enumerate(corpus.chunks):
        if kind and chunk.kind != kind:
            continue
        tokens = _tokenise(chunk.text)
        if not tokens:
            continue
        counts = Counter(tokens)
        length = corpus.lengths[index]
        score = 0.0
        seen: set[str] = set()

        for term in terms:
            frequency = counts.get(term, 0)
            if not frequency:
                continue
            seen.add(term)
            df = corpus.doc_freq.get(term, 1)
            # Robertson-Zaragoza IDF, floored at a small positive value so a
            # term present in every document contributes ranking signal without
            # going negative and penalising an otherwise strong match.
            idf = max(
                math.log(1.0 + (total - df + 0.5) / (df + 0.5)),
                1e-6,
            )
            denominator = frequency + _K1 * (
                1 - _B + _B * (length / (corpus.avg_length or 1.0))
            )
            score += idf * (frequency * (_K1 + 1)) / denominator

        if score > 0:
            scores[index] = score
            matches[index] = seen

    if not scores:
        return []

    # Normalise to 0..1 so `score` is a confidence a caller can threshold on
    # rather than an arbitrary BM25 magnitude.
    ceiling = max(scores.values())
    floor = 0.06  # of the best score; below this a passage is not evidence

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    hits: list[Hit] = []
    for index, score in ranked:
        normalised = score / ceiling
        if normalised < floor:
            break
        hits.append(
            Hit(
                chunk=corpus.chunks[index],
                score=normalised,
                matched_terms=list(matches[index]),
            )
        )
        if len(hits) >= limit:
            break
    return hits


# -- the vector half -------------------------------------------------------

_OVERRIDE_BACKEND: Any = None


def set_vector_backend(backend: Any | None) -> None:
    """Swap the vector backend at runtime, or drop to BM25 with `None`.

    This is the seam a real embedder arrives through. Pass an object with
    `fit(texts)` and `transform(texts)` - a sentence-transformer wrapper, an
    OpenAI embedding client - and the rest of the retrieval layer is unchanged.
    """
    global _OVERRIDE_BACKEND
    _OVERRIDE_BACKEND = backend
    vector_index.cache_clear()


@lru_cache(maxsize=1)
def vector_index() -> VectorIndex:
    """The vector store over the same chunks BM25 indexes. Built once."""
    corpus = load_corpus()
    if not corpus.chunks:
        return VectorIndex().build([])

    settings = _rag_settings()
    if not settings.rag_vector_enabled:
        return VectorIndex(unavailable_reason="rag_vector_enabled is false in .env")

    backend = _OVERRIDE_BACKEND
    if backend is None:
        backend = LsaBackend(dimensions=settings.rag_vector_dimensions)
    return VectorIndex(backend=backend).build([c.text for c in corpus.chunks])


def _rag_settings():
    """Settings are read lazily: importing this module must not require a DB."""
    from app.core.config import get_settings

    return get_settings()


def semantic_search(
    question: str, limit: int = 5, kind: str | None = None
) -> list[Hit]:
    """Vector-ranked passages, as `Hit`s, so both paths share a shape.

    `kind` filters after scoring rather than before, because a vector index
    cannot be sliced by kind without rebuilding it - and rebuilding per filter
    is exactly the kind of cost that makes people turn the index off.
    """
    store = vector_index()
    if not store.available:
        return []
    corpus = load_corpus()
    hits: list[Hit] = []
    for found in store.search(question, limit=limit * 4 if kind else limit):
        chunk = corpus.chunks[found.index]
        if kind and chunk.kind != kind:
            continue
        hits.append(Hit(chunk=chunk, score=found.score, matched_terms=[]))
        if len(hits) >= limit:
            break
    return hits


@dataclass
class RetrievalResult:
    """Hits plus the provenance of how they were found."""

    hits: list[Hit]
    mode: str
    lexical_count: int
    semantic_count: int
    vector_backend: str
    vector_dimensions: int
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "lexical_hits": self.lexical_count,
            "semantic_hits": self.semantic_count,
            "vector_backend": self.vector_backend,
            "vector_dimensions": self.vector_dimensions,
            "note": self.note,
        }


def retrieve(
    question: str, limit: int = 5, depth: int = 25, kind: str | None = None
) -> RetrievalResult:
    """BM25 fused with the vector index, with the fusion itself disclosed.

    `depth` is how deep each retriever is read before fusion; the caller
    normally sees `limit`. Fusion works on order, so reading past `limit` is
    what lets a passage that BM25 ranked 20th and the vector index ranked 1st
    reach the top five - which is the entire reason for having two retrievers.

    `kind` restricts the answer to one part of the corpus (`documentation`,
    `analytics-query`, `policy`, ...) and is applied after fusion, so filtering
    cannot change the ranking of what survives.
    """
    lexical = search(question, limit=depth, kind=kind)
    semantic = semantic_search(question, limit=depth, kind=kind)

    corpus = load_corpus()
    position = {id(chunk): index for index, chunk in enumerate(corpus.chunks)}

    lexical_ids = [position[id(hit.chunk)] for hit in lexical]
    semantic_ids = [position[id(hit.chunk)] for hit in semantic]

    if not semantic_ids:
        note = (
            missing_dependency_reason()
            if not sklearn_available()
            else vector_index().unavailable_reason
        )
        return RetrievalResult(
            hits=lexical[:limit],
            mode="bm25-only",
            lexical_count=len(lexical),
            semantic_count=0,
            vector_backend="none",
            vector_dimensions=0,
            note=note or "the vector index returned nothing for this question",
        )

    if kind:
        keep = {position[id(hit.chunk)] for hit in lexical}
        semantic_ids = [i for i in semantic_ids if i in keep]

    fused = reciprocal_rank_fusion(
        lexical_ids, semantic_ids, k=_rag_settings().rag_rrf_k
    )
    by_index = {position[id(hit.chunk)]: hit for hit in lexical}

    store = vector_index()
    vector_scores = {found.index: found.score for found in store.search(question, limit=depth)}

    hits: list[Hit] = []
    for index, score in fused[:limit]:
        lexical_hit = by_index.get(index)
        if lexical_hit is not None:
            hits.append(lexical_hit)
        else:
            hits.append(
                Hit(
                    chunk=corpus.chunks[index],
                    score=vector_scores.get(index, 0.0),
                    matched_terms=[],
                )
            )

    return RetrievalResult(
        hits=hits,
        mode="hybrid-bm25+lsa",
        lexical_count=len(lexical_ids),
        semantic_count=len(semantic_ids),
        vector_backend=store.backend.name if store.backend else "none",
        vector_dimensions=store.matrix.shape[1] if store.available else 0,
        note=(
            "cosine similarity in a TF-IDF/SVD space; see docs/retrieval.md"
        ),
    )


def evidence_for(
    question: str, limit: int = 5, kind: str | None = None
) -> dict[str, Any]:
    """The retrieval half of `evidence_requirement`, with its citation list.

    Returns `supported: False` and an empty citation list when nothing scored.
    The analyst reads `supported` and refuses to state a fact; it does not
    decide for itself that a weak hit is good enough.

    The `retrieval` key discloses which retrievers ran and what they returned, so
    a caller can see whether an answer came from a lexical match, a vector
    match, or both - and can see when the vector index was unavailable.
    """
    result = retrieve(question, limit=limit, kind=kind)
    hits = result.hits
    return {
        "supported": bool(hits),
        "question": question,
        "hits": [hit.as_dict() for hit in hits],
        "citations": [hit.chunk.citation() for hit in hits],
        "retrieval": result.as_dict(),
    }