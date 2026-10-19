# RETRIEVAL (RAG)

What the retrieval layer is, what it can and cannot do, and what is waiting on
an external dependency.

Generated content is marked. Everything else in this file was measured on this
repository and can be re-measured with the commands shown.

---

## 1. THE SHORT VERSION

| | |
|---|---|
| Corpus | 268 passages from 42 committed files |
| Lexical retriever | BM25 (`k1=1.2`, `b=0.75`, published values, untuned) |
| Vector retriever | TF-IDF -> truncated SVD -> L2-normalised, cosine |
| Fusion | Reciprocal rank fusion, `k=60` |
| Embeddings | **not neural** — latent semantic indexing, see §4 |
| Network at query time | none |
| Determinism | full; arpack SVD has no random seed |
| Cold index build | 2.7 s, once per process, then cached |
| Answer-time retrieval | under 10 ms |

Everything runs offline. No model is downloaded and no API is called.

---

## 2. THE PIPELINE

```
DOCUMENTS (committed files only)
    ↓  _build()
CHUNKS  — split on markdown headings, then windowed at 2200 chars
    ↓
TWO INDEPENDENT RETRIEVERS
    ├─ BM25          — term overlap, IDF-weighted, Robertson-Zaragoza
    └─ VECTOR INDEX  — TF-IDF -> SVD(128) -> normalise -> cosine
    ↓  reciprocal rank fusion, k=60
RANKED PASSAGES, each carrying path#Lline
    ↓
evidence_for()  →  {supported, hits, citations, retrieval}
    ↓
analyst         →  an answer, or a refusal when supported is false
```

The analyst reads `supported`. It does not decide for itself that a weak hit is
good enough — that decision is made once, in the retriever, against a floor.

---

## 3. THE CORPUS

Only committed files. Nothing is fetched, nothing is generated.

| Kind | Pattern | Chunks |
|---|---|---:|
| `documentation` | `docs/*.md` | 207 |
| `analytics-query` | `backend/sql/*/*.sql` | 37 |
| `policy` | `backend/app/ai/GROUNDING_CONTRACT.md` | 13 |
| `measurement-report` | `data/processed/_*.json` | 6 |
| `configuration` | `backend/config/*.yaml` | 4 |
| `provenance` | `data/raw/_checksums.json` | 1 |

JSON reports are flattened into readable sentences before indexing, so
`{"olist": {"row_count": 774081}}` is retrievable by the words "olist row count"
instead of being invisible to a lexical search.

**Deliberately not in the corpus:**

- **The raw CSVs.** A row in a CSV has no provenance and cannot be cited. The
  database is reachable by executing SQL, which is what the analyst does
  instead. Retrieval answers *what the documents say*; execution answers *what
  is true*.
- **The `policies/` corpus, which is empty.** `policies/README.md` is the only
  file in it. No policy document has been written, because writing one would be
  manufacturing evidence. When a real policy corpus exists it joins by adding a
  `(prefix, pattern, kind)` row to `CORPUS_SPECS`; nothing else changes.

`backend/tests/test_rag.py::test_retrieval_stays_inside_the_declared_corpus`
asserts the corpus contains no `.csv`, no `.db`, and no path outside `docs/`,
`backend/` and the report files, so the boundary cannot quietly widen.

---

## 4. WHY LSA AND NOT A SENTENCE EMBEDDER

This is the honest part, and it is stated here rather than in a limitations
appendix because it changes what the system can answer.

**What is installed:**

| Package | State |
|---|---|
| `scikit-learn` 1.9.1 | installed |
| `numpy` 2.5.3 | installed |
| `sentence-transformers` | **not installed** |
| `torch` | **not installed** |
| `transformers` | **not installed** |
| `chromadb` | **not installed** |
| `faiss` | **not installed** |
| `OPENAI_API_KEY` | **empty** |

No model file exists on disk. A neural embedder would need a download this
project is not authorised to make, and every embedding would then cost a network
call — which would mean an answer whose grounding depends on a third party
being reachable. So the vector space is built from the corpus itself:

1. **TF-IDF** over the 268 chunks, using this project's own tokeniser so the
   stopword list and token rules match BM25 exactly. Two retrievers that
   tokenise differently are answering two different questions.
2. **Truncated SVD** onto 128 latent dimensions (arpack — no seed, so no
   solver variance).
3. **L2 normalisation**, so a dot product *is* a cosine and the score has a
   geometric meaning.

**What LSA does that BM25 cannot:** it relates words that co-occur rather than
words that match. A question about "how many households are covered" retrieves a
passage that says **801**, with no shared term between the question and the
number. BM25 cannot do that; the passage has to be found some other way.

**What LSA cannot do, and will not pretend to:** it learns only from these 268
passages. It cannot represent a relationship the corpus never shows, it has no
world knowledge, and it will rank a synonym highly and a novel concept not at
all. It is a compression of the corpus, not a model of the world. That is a real
limitation with a real cost, and it is the reason §6 exists.

`app/ai/vector_index.py::describe()` reports all of this by inspecting the
installed packages rather than by reading this file, so the two cannot drift.

---

## 5. WHAT FUSING ACTUALLY BUYS — MEASURED, NOT ASSUMED

Two measurements, because the second one is unflattering and both belong here.

**Recall: fusion adds.** Over ten probe questions, the fused top-5 contains
**8 passages that BM25's own top-5 did not contain**. The vector path brings in
documents the lexical path never ranked.

**Ordering: fusion barely changes it.** Counting passages that fusion promoted
above their rank in *both* input lists gives **1 promotion in 50**. On a
268-chunk corpus the two retrievers largely agree on which passages are
relevant and mostly disagree about the order of passages that do not matter.

That is the honest trade: **recall improves, precision does not.** Nothing in
this project claims fusion made answers better than BM25 alone. What it does is
stop a passage BM25 scored below its floor from being unreachable when the
latent space considers it clearly relevant.

Reproduce both numbers:

```bash
PYTHONPATH=backend .venv/Scripts/python.exe -c "
from app.ai import retrieval
c = retrieval.load_corpus(); pos = {id(x): i for i, x in enumerate(c.chunks)}
qs = ['What is the M5 forecasting holdout window?',
      'Which four datasets does this project load?',
      'What does the read-only role refuse?',
      'how many households are covered by demographics',
      'anomaly detection threshold and baseline',
      'cross-source joins are forbidden',
      'dunnhumby discount sign convention',
      'olist review grain primary key',
      'API key scopes and 403',
      'cold overview cache age']
adds = sum(len({pos[id(h.chunk)] for h in retrieval.retrieve(q, limit=5).hits}
             - {pos[id(h.chunk)] for h in retrieval.search(q, limit=5)}) for q in qs)
print('passages added beyond BM25 top-5:', adds)"
```

---

## 6. WHEN A REAL EMBEDDER ARRIVES

Nothing above the vector index has to change. `set_vector_backend()` takes any
object with `fit(texts)` and `transform(texts)`:

```python
from app.ai import retrieval

class SentenceTransformerBackend:
    name = "all-MiniLM-L6-v2"

    def __init__(self):
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(self.name)

    def fit(self, texts):          # nothing to fit; the model is already trained
        self.model.encode(texts, normalize_embeddings=True)

    def transform(self, texts):
        return self.model.encode(texts, normalize_embeddings=True)

retrieval.set_vector_backend(SentenceTransformerBackend())
```

That is the whole change. The cosine search, the relevance floor, the fusion,
the citation format and the analyst are untouched. A backend that raises, or
returns the wrong shape, is caught by `VectorIndex.build` and the system falls
back to BM25 with the reason disclosed in `retrieval.note` — this is tested, not
assumed:

`test_a_backend_that_returns_nonsense_is_unavailable_not_a_crash`

**Configuration** (`.env`, all optional):

| Key | Default | Meaning |
|---|---|---|
| `RAG_VECTOR_ENABLED` | `true` | `false` leaves BM25 as the only retriever |
| `RAG_VECTOR_DIMENSIONS` | `128` | reduced automatically to what the corpus allows |
| `RAG_RRF_K` | `60` | the value from the RRF paper; not tuned |

`rag_vector_enabled=false` is a supported, tested configuration. Retrieval
degrades, `retrieval.mode` reports `bm25-only`, and answers stay grounded.

---

## 7. WHAT RETRIEVAL WILL NOT DO

- **It will not answer a question the corpus does not cover.** Gibberish maps
  to the zero vector and to no BM25 terms, so both retrievers return nothing.
  `test_gibberish_retrieves_nothing_from_either_retriever` asserts it.
- **It will not return its least-bad document.** Below the relevance floor the
  answer is no evidence, which is what makes `evidence_requirement` enforceable.
- **It will not paraphrase.** Every retrieved passage is a verbatim span of a
  committed file. `test_retrieved_text_is_a_verbatim_quote_not_a_summary`
  asserts the quoted text is still there.
- **It will not cite a line that does not exist.** Every citation resolves to a
  real file and a line inside it —
  `test_every_citation_resolves_to_a_real_line_in_a_real_file` checks both
  halves for every hit of every probe question.
- **It will not read the database or the filesystem beyond the corpus.** The
  corpus boundary is asserted, not assumed.

---

## 8. RELATED

- `backend/app/ai/GROUNDING_CONTRACT.md` — the rule retrieval exists to serve.
- `backend/app/ai/retrieval.py` — corpus, chunking, BM25, fusion.
- `backend/app/ai/vector_index.py` — the vector store and the swap seam.
- `backend/tests/test_rag.py` — 29 tests over all of the above.
- `GET /api/ai/search` — returns `retrieval` provenance per request.
- `docs/architecture.md` — where retrieval sits in the answer pipeline.
