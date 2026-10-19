"""The RAG layer's contracts: citations, retrieval, refusal, and fallback.

The claims under test are the ones the master prompt makes about retrieval, and
each is written so it would fail if the behaviour regressed rather than if a
number moved. Nothing here asserts a score; a score is a tuning artefact. What is
asserted is that a citation resolves, that a refusal stays a refusal, and that
the vector path never becomes the only authority.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ai import retrieval, vector_index
from app.ai.retrieval import Chunk

REPO_ROOT = Path(__file__).resolve().parents[2]

# Questions with a known answer somewhere in the corpus, and the file that
# holds it. Deliberately not the questions the analyst is asked in the API
# tests, so retrieval is being tested and not the analyst's prompt handling.
_ANSWERABLE = (
    ("What is the M5 forecasting holdout window?", "docs/forecasting.md"),
    ("Which four datasets does this project load?", "docs/data_sources.md"),
    ("What does the read-only role refuse?", "docs/database.md"),
)


# ==============================================================================
# Citation correctness
# ==============================================================================


@pytest.mark.parametrize("question,expected_path", _ANSWERABLE, ids=[q[:28] for q, _ in _ANSWERABLE])
def test_an_answerable_question_retrieves_the_document_that_answers_it(
    question, expected_path
):
    """A retrieval that misses the right document is worse than no retrieval."""
    found = retrieval.evidence_for(question, limit=8)
    assert found["supported"], f"nothing retrieved for {question!r}"
    paths = [hit["path"] for hit in found["hits"]]
    assert expected_path in paths, (
        f"{expected_path} was not retrieved for {question!r}; got {paths}"
    )


def test_every_citation_resolves_to_a_real_line_in_a_real_file():
    """A citation is a promise a reader can go and check.

    Both halves matter: the file exists, and the cited line is inside it. A
    citation with a line number past the end of the file is a broken promise
    that still looks like a reference.
    """
    questions = [q for q, _ in _ANSWERABLE] + [
        "cross-source joins",
        "dunnhumby discount signs",
        "anomaly detection method",
    ]
    checked = 0
    for question in questions:
        for hit in retrieval.evidence_for(question, limit=5)["hits"]:
            citation = hit["citation"]
            path_text, _, line_text = citation.rpartition("#L")
            assert line_text.isdigit(), f"citation has no line number: {citation!r}"

            target = REPO_ROOT / path_text
            assert target.is_file(), f"cited file does not exist: {citation}"

            line_no = int(line_text)
            line_count = len(target.read_text(encoding="utf-8").splitlines())
            assert 1 <= line_no <= max(line_count, 1), (
                f"{citation} points past the end of a {line_count}-line file"
            )
            checked += 1

    assert checked >= 10, f"only {checked} citations checked; the corpus looks empty"


def test_retrieved_text_is_a_verbatim_quote_not_a_summary():
    """A paraphrase presented as a quote is a fabrication with a citation."""
    found = retrieval.evidence_for("M5 forecasting holdout window", limit=5)
    for hit in found["hits"]:
        if hit["path"].endswith((".md", ".sql", ".yaml")):
            body = (REPO_ROOT / hit["path"]).read_text(encoding="utf-8")
            probe = hit["text"].strip()[:70]
            if probe and not probe.startswith("..."):
                assert probe in body, (
                    f"{hit['citation']} quotes text that is not in {hit['path']}"
                )


def test_json_reports_are_indexed_as_readable_sentences():
    """A machine report that cannot be matched is a machine report nobody reads."""
    found = retrieval.evidence_for(
        "load report row counts", limit=10, kind="measurement-report"
    )
    assert found["supported"]
    assert {hit["kind"] for hit in found["hits"]} == {"measurement-report"}


# ==============================================================================
# Retrieval correctness
# ==============================================================================


def test_the_corpus_is_built_from_real_files_and_is_not_empty():
    corpus = retrieval.load_corpus()
    assert corpus.size > 100, f"corpus has only {corpus.size} chunks"

    roots = {hit.path.split("/", 1)[0] for hit in corpus.chunks}
    assert {"docs", "backend", "data"} <= roots, f"corpus is missing roots: {roots}"

    for chunk in corpus.chunks[:60]:
        assert (REPO_ROOT / chunk.path).is_file(), (
            f"corpus indexes a file that does not exist: {chunk.path}"
        )


def test_kind_filtering_returns_only_that_kind():
    hits = retrieval.search("revenue", limit=10, kind="analytics-query")
    assert hits
    assert {hit.chunk.kind for hit in hits} == {"analytics-query"}


def test_every_hit_names_the_terms_that_matched_it():
    """A lexical hit that cannot say why it matched is not auditable."""
    for hit in retrieval.search("olist order status", limit=5):
        assert hit.matched_terms, f"{hit.chunk.citation()} matched nothing in name"


def test_the_vector_index_really_is_a_vector_space():
    """Not a lookup table with a vector-shaped name."""
    store = retrieval.vector_index()
    assert store.available, store.unavailable_reason
    assert store.matrix is not None

    rows, dimensions = store.matrix.shape
    corpus = retrieval.load_corpus()
    assert rows == corpus.size, (
        f"the vector store holds {rows} vectors for {corpus.size} chunks"
    )
    assert dimensions > 1, "the index collapsed to a single dimension"
    assert store.backend is not None
    assert store.backend.name == "lsa-tfidf-svd"

    # Rows are unit length, so a dot product really is a cosine.
    norms = (store.matrix**2).sum(axis=1) ** 0.5
    assert float(abs(norms - 1.0).max()) < 1e-6, "vectors are not normalised"


def test_the_vector_index_is_deterministic():
    """Two identical queries must rank identically, or 'verified' means nothing."""
    first = retrieval.semantic_search("anomaly detection threshold", limit=5)
    second = retrieval.semantic_search("anomaly detection threshold", limit=5)
    assert [h.chunk.citation() for h in first] == [h.chunk.citation() for h in second]
    assert first, "the vector retriever returned nothing for a real question"


def test_vector_retrieval_finds_a_passage_bm25_cannot_match():
    """If the two retrievers always agree, the second one is decoration.

    The question is phrased with no query term the target passage uses, so
    exact-term matching has no way in and the latent space has to carry it.
    """
    semantic = [h.chunk.citation() for h in retrieval.semantic_search(
        "how many households are covered by demographics", limit=8
    )]
    assert semantic, "the vector retriever returned nothing"

    sources = retrieval.search(
        "how many households are covered by demographics", limit=40
    )
    lexical_paths = {h.chunk.path for h in sources}
    assert not any(path in lexical_paths for path in semantic), (
        "the vector index returned only passages BM25 already ranked; "
        "it is not adding anything"
    )


def test_hybrid_search_discloses_how_each_hit_was_found():
    found = retrieval.evidence_for("M5 forecasting holdout", limit=5)
    detail = found["retrieval"]
    assert detail["mode"] in {"hybrid-bm25+lsa", "bm25-only"}
    assert detail["lexical_hits"] >= 0
    assert detail["semantic_hits"] >= 0
    if detail["mode"] == "hybrid-bm25+lsa":
        assert detail["vector_backend"] == "lsa-tfidf-svd"
        assert detail["vector_dimensions"] > 1
        assert detail["lexical_hits"] > 0 and detail["semantic_hits"] > 0


def test_fusion_adds_recall_beyond_bm25_alone():
    """The vector path has to actually bring in passages BM25 did not find.

    What is claimed here is recall, and only recall: the fused top-5 contains
    passages BM25's own top-5 did not. Reordering is a separate claim and a
    much weaker one on a 268-chunk corpus where the two retrievers already
    agree - measured at 1 promotion in 50 earlier, which is why no test
    asserts it. `docs/retrieval.md` reports both numbers.
    """
    questions = [
        "What is the M5 forecasting holdout window?",
        "Which four datasets does this project load?",
        "What does the read-only role refuse?",
        "how many households are covered by demographics",
        "anomaly detection threshold and baseline",
        "cross-source joins are forbidden",
        "dunnhumby discount sign convention",
        "olist review grain primary key",
        "API key scopes and 403",
        "cold overview cache age",
    ]

    corpus = retrieval.load_corpus()
    position = {id(c): i for i, c in enumerate(corpus.chunks)}
    added = 0

    for question in questions:
        fused = {position[id(h.chunk)] for h in retrieval.retrieve(question, limit=5).hits}
        lexical = {position[id(h.chunk)] for h in retrieval.search(question, limit=5)}
        added += len(fused - lexical)

    assert added >= 5, (
        f"fusion added only {added} passages BM25 had not already ranked in its "
        f"top 5 across {len(questions)} questions; the vector path is not earning "
        f"its place"
    )


def test_fusion_never_invents_a_passage_neither_retriever_returned():
    """Fused results are a re-ordering of real candidates, not new material."""
    for question in (
        "What is the M5 forecasting holdout window?",
        "how many households are covered by demographics",
        "API key scopes and 403",
    ):
        result = retrieval.retrieve(question, limit=5, depth=25)
        corpus = retrieval.load_corpus()
        position = {id(c): i for i, c in enumerate(corpus.chunks)}

        pooled = {
            position[id(h.chunk)]
            for h in retrieval.search(question, limit=25)
        } | {
            position[id(h.chunk)]
            for h in retrieval.semantic_search(question, limit=25)
        }
        fused = {position[id(h.chunk)] for h in result.hits}

        assert fused <= pooled, (
            f"fusion returned a passage neither retriever had for {question!r}"
        )
        assert len(result.hits) == len({h.chunk.citation() for h in result.hits}), (
            f"fusion returned the same passage twice for {question!r}"
        )


def test_with_the_vector_index_off_fusion_returns_bm25_exactly():
    """Degrading must be lossless for the retriever that is left.

    This is the `RAG_VECTOR_ENABLED=false` path: the answer set must be
    identical to pure BM25, not merely similar, or "it falls back" would be a
    claim with no behaviour behind it.
    """
    from app.core.config import get_settings

    settings = get_settings()
    retrieval.load_corpus.cache_clear()
    retrieval.vector_index.cache_clear()
    original = settings.rag_vector_enabled
    settings.rag_vector_enabled = False
    try:
        for question, _ in _ANSWERABLE:
            degraded = retrieval.retrieve(question, limit=5)
            assert degraded.mode == "bm25-only", degraded.mode
            assert "rag_vector_enabled is false" in degraded.note

            retrieval.vector_index.cache_clear()
            retrieval.set_vector_backend(None)
            expected = retrieval.search(question, limit=5)
            settings.rag_vector_enabled = True
            retrieval.vector_index.cache_clear()
            try:
                restored = retrieval.search(question, limit=5)
            finally:
                settings.rag_vector_enabled = False
                retrieval.vector_index.cache_clear()

            assert [h.chunk.citation() for h in degraded.hits] == [
                h.chunk.citation() for h in restored
            ], f"degraded result differs from BM25 for {question!r}"
    finally:
        settings.rag_vector_enabled = original
        retrieval.vector_index.cache_clear()
        retrieval.load_corpus.cache_clear()
        retrieval.load_corpus()


# ==============================================================================
# Empty corpus, unsupported questions, hallucination prevention
# ==============================================================================


def test_an_empty_corpus_returns_no_evidence_rather_than_an_error():
    """The empty corpus is the case where a retriever is most tempted to guess."""
    retrieval.vector_index.cache_clear()
    retrieval.load_corpus.cache_clear()
    try:
        empty = vector_index.VectorIndex().build([])
        assert not empty.available
        assert empty.search("anything at all", limit=5) == []

        # And the fused path must survive it, not raise.
        original = retrieval.load_corpus
        retrieval.load_corpus = lambda: retrieval.Corpus(chunks=[])
        try:
            assert retrieval.search("revenue", limit=5) == []
            result = retrieval.retrieve("revenue", limit=5)
            assert result.hits == []
            assert result.mode == "bm25-only"
            assert result.note, "an unavailable index must say why"
        finally:
            retrieval.load_corpus = original
    finally:
        retrieval.vector_index.cache_clear()
        retrieval.load_corpus.cache_clear()
        retrieval.load_corpus()


def test_gibberish_retrieves_nothing_from_either_retriever():
    """Both paths must agree that an unknown question has no evidence.

    The probe is *derived* rather than hardcoded. An earlier version used
    "nonexistent gibberish", which passed until this file was written - those
    turned out to be real words in `docs/retrieval.md`, and matching them was
    the retriever being right. A hardcoded nonsense string is therefore a
    decaying test: it rots the first time anyone documents the word "gibberish".
    These tokens are built by asking the corpus which strings it has never seen.
    """
    probe = _absent_tokens(4)
    question = " ".join(probe)
    assert not (set(retrieval._tokenise(question)) & set(retrieval.load_corpus().doc_freq))

    found = retrieval.evidence_for(question, limit=5)
    assert found["supported"] is False, f"{probe} retrieved something"
    assert found["hits"] == []
    assert found["citations"] == []
    assert retrieval.semantic_search(question, limit=5) == []


def _absent_tokens(count: int) -> list[str]:
    """Strings that appear in no committed document, derived not hardcoded."""
    vocabulary = set(retrieval.load_corpus().doc_freq)
    out: list[str] = []
    seed = 0
    while len(out) < count:
        seed += 1
        for candidate in (
            f"q{seed}zx", f"w{seed}vn", f"p{seed}mq", f"b{seed}tr",
            f"k{seed}jh", f"t{seed}gf", f"y{seed}nd", f"c{seed}vb",
        ):
            if candidate not in vocabulary:
                out.append(candidate)
            if len(out) == count:
                break
    return out


def test_a_question_the_documents_do_not_answer_is_refused():
    """The corpus has no competitor data, so asking for market share must fail.

    Asserted on the answer rather than on the retrieved text, because what has
    to be true is that no number reaches the user - not that no passage was
    retrieved. Retrieval may well surface `revenue_by_market`, where "market"
    means a DataCo geographic market, which is a different subject entirely.
    """
    from app.ai import analyst

    answer = analyst.g_analyse("What is Olist market share against Walmart?")
    body = answer.as_dict()
    assert body["verified"] is not True, body
    assert body["rows"] == [], "a refused question must not carry result rows"
    lowered = str(body.get("answer", "")).lower()
    assert "no competitor" in lowered or "not exist" in lowered, body


def test_the_analyst_refuses_to_state_a_fact_with_no_retrieved_evidence():
    """`evidence_requirement` end to end, through the answer, not the index."""
    from app.ai import analyst

    answer = analyst.g_analyse("What is OmniRetail's net promoter score?")
    body = answer.as_dict()
    assert body["verified"] is not True, body
    assert body["citations"] == []
    assert not body["rows"], "an unverifiable answer must carry no rows"


# ==============================================================================
# Source / document boundary
# ==============================================================================


def test_retrieval_stays_inside_the_declared_corpus():
    """A retriever that can read anything on disk is a file-disclosure bug."""
    declared = {prefix for prefix, _, _ in retrieval.CORPUS_SPECS}
    assert declared == {"docs", "sql", "config", "report", "checksums", "contract"}

    corpus = retrieval.load_corpus()
    for chunk in corpus.chunks:
        assert ".." not in chunk.path, f"path escapes the corpus: {chunk.path}"
        assert Path(chunk.path).is_absolute() is False
        if chunk.kind == "documentation":
            assert chunk.path.startswith("docs/"), chunk.path
        elif chunk.kind == "analytics-query":
            assert chunk.path.startswith("backend/sql/"), chunk.path


def test_the_database_and_the_raw_csvs_are_not_in_the_corpus():
    """A CSV row has no provenance and cannot be cited, so it must not be one."""
    corpus = retrieval.load_corpus()
    for chunk in corpus.chunks:
        assert not chunk.path.endswith(".csv"), chunk.path
        assert not chunk.path.startswith("data/raw/") or chunk.path.endswith(
            "_checksums.json"
        ), chunk.path
        assert not chunk.path.endswith(".db"), chunk.path


def test_a_chunk_cannot_claim_to_be_from_a_file_it_is_not_in():
    """Path and text must agree - a chunk's text comes from its own file."""
    sample = [
        chunk for chunk in retrieval.load_corpus().chunks
        if chunk.kind in {"documentation", "policy", "analytics-query"}
    ][::17]
    assert sample, "no sampleable chunks"
    for chunk in sample:
        body = (REPO_ROOT / chunk.path).read_text(encoding="utf-8")
        probe = chunk.text.strip()[:50]
        if probe:
            assert probe in body, (
                f"chunk claims to be {chunk.citation()} but its text is not there"
            )


# ==============================================================================
# Fallback behaviour
# ==============================================================================


def test_the_system_falls_back_to_bm25_when_the_vector_index_cannot_build(
    monkeypatch,
):
    """The fallback is a behaviour, so it is tested by breaking the vector path.

    This is the path that runs if scikit-learn is ever uninstalled or the
    backend raises. Retrieval must keep working and must say it degraded.
    """
    class BrokenBackend:
        name = "broken"

        def fit(self, texts):
            raise RuntimeError("no model available")

        def transform(self, texts):
            raise RuntimeError("no model available")

    retrieval.load_corpus.cache_clear()
    retrieval.vector_index.cache_clear()
    retrieval.set_vector_backend(BrokenBackend())
    try:
        found = retrieval.evidence_for("M5 forecasting holdout window", limit=5)
        assert found["supported"] is True, "BM25 fallback did not answer"
        assert found["retrieval"]["mode"] == "bm25-only"
        assert found["retrieval"]["vector_backend"] == "none"
        assert "no model available" in found["retrieval"]["note"]
        assert found["citations"], "the fallback returned passages with no citations"
    finally:
        retrieval.set_vector_backend(None)
        retrieval.vector_index.cache_clear()
        retrieval.load_corpus.cache_clear()
        retrieval.load_corpus()


def test_the_analyst_still_answers_with_the_bm25_fallback_alone():
    """Grounding does not depend on the vector index being present."""
    from app.ai import analyst

    retrieval.vector_index.cache_clear()
    retrieval.set_vector_backend(_NullBackend())
    try:
        found = retrieval.evidence_for("M5 forecasting holdout", limit=5)
        assert found["retrieval"]["mode"] == "bm25-only"

        answer = analyst.g_analyse("Explain the M5 forecasting holdout window")
        body = answer.as_dict()
        assert body["answer"], "the analyst stopped answering without the vector index"
        assert body["citations"], "an answer with no citation is not grounded"
        assert body["verified"], body
    finally:
        retrieval.set_vector_backend(None)
        retrieval.vector_index.cache_clear()


class _NullBackend:
    name = "null"

    def fit(self, texts):
        return None

    def transform(self, texts):
        return None


def test_the_unavailable_reason_names_the_dependency():
    """A limitation a reader cannot act on is decoration."""
    reason = vector_index.missing_dependency_reason()
    if vector_index.sklearn_available():
        assert reason == ""
    else:
        assert "scikit-learn" in reason
        assert "BM25" in reason


def test_the_vector_backend_is_swappable_without_touching_callers():
    """The seam a real embedder arrives through is real."""
    import numpy as np

    seen: list[int] = []

    class RecordingBackend:
        """Stands in for a sentence-transformer wrapper: same two methods."""

        name = "recording"

        def fit(self, texts):
            seen.append(len(texts))
            self._size = len(texts)

        def transform(self, texts):
            count = self._size if len(texts) == 1 else len(texts)
            return np.zeros((count, 4), dtype="float32")

    retrieval.load_corpus.cache_clear()
    retrieval.vector_index.cache_clear()
    retrieval.set_vector_backend(RecordingBackend())
    try:
        store = retrieval.vector_index()
        assert seen, "the injected backend was never fitted"
        assert seen[0] == retrieval.load_corpus().size
        assert store.backend is not None
        assert store.backend.name == "recording"
        assert store.matrix.shape == (retrieval.load_corpus().size, 4)
    finally:
        retrieval.set_vector_backend(None)
        retrieval.vector_index.cache_clear()
        retrieval.load_corpus.cache_clear()
        retrieval.load_corpus()


def test_a_backend_that_returns_nonsense_is_unavailable_not_a_crash():
    """The seam must not become a hole."""
    class WrongShape:
        name = "wrong-shape"

        def fit(self, texts):
            return None

        def transform(self, texts):
            return None

    retrieval.load_corpus.cache_clear()
    retrieval.vector_index.cache_clear()
    retrieval.set_vector_backend(WrongShape())
    try:
        store = retrieval.vector_index()
        assert not store.available
        assert "expected a 2-D array" in store.unavailable_reason
        assert retrieval.semantic_search("anything", limit=3) == []

        found = retrieval.evidence_for("olist order status", limit=3)
        assert found["supported"], "BM25 stopped answering when the index broke"
        assert found["retrieval"]["mode"] == "bm25-only"
    finally:
        retrieval.set_vector_backend(None)
        retrieval.vector_index.cache_clear()
        retrieval.load_corpus.cache_clear()
        retrieval.load_corpus()


def test_rag_configuration_is_declared_not_hardcoded():
    """If the index can be switched off, the switch is in settings."""
    from app.core.config import get_settings

    settings = get_settings()
    assert hasattr(settings, "rag_vector_enabled")
    assert hasattr(settings, "rag_vector_dimensions")
    assert settings.rag_vector_dimensions >= 2
