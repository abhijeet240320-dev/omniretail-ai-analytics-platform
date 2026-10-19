# Architecture

Where each component sits, what is allowed to influence an answer, and what is
refused. Every claim here is checkable against the code named beside it.

---

## 1. The answer path

```
                    ┌──────────────────────────────────────────┐
   question ───────▶│ 1. SOURCE / INTENT           guards.py   │
                    │    Which dataset? Is this a false premise?│
                    └───────────────┬──────────────────────────┘
                                    ▼
                    ┌──────────────────────────────────────────┐
                    │ 2. RETRIEVAL / CATALOG     retrieval.py   │
                    │    hybrid BM25 + LSA over the real docs   │
                    └───────────────┬──────────────────────────┘
                                    ▼
                    ┌──────────────────────────────────────────┐
                    │ 3. TRANSLATE              translator.py   │
                    │    question → a catalogue query by name    │
                    │    LLM may assist; never decides          │
                    └───────────────┬──────────────────────────┘
                                    ▼
                    ┌──────────────────────────────────────────┐
                    │ 4. SQL VALIDATOR        sql_validator.py  │
                    │    the ONLY way SQL enters the system      │
                    └───────────────┬──────────────────────────┘
                                    ▼
                    ┌──────────────────────────────────────────┐
                    │ 5. EXECUTE           read-only DB role    │
                    │    omniretail_readonly, 5 min timeout     │
                    └───────────────┬──────────────────────────┘
                                    ▼
                    ┌──────────────────────────────────────────┐
                    │ 6. VERIFY            verification.py      │
                    │    every number re-read from its own rows  │
                    └───────────────┬──────────────────────────┘
                                    ▼
                    ┌──────────────────────────────────────────┐
                    │ 7. EVIDENCE + ANSWER                      │
                    │    citations, provenance, and only then    │
                    └──────────────────────────────────────────┘
```

The live trace is in the response's `pipeline` field, not in this document.
`backend/scripts/verify/verify_ai_pipeline.py` prints it for real questions.

---

## 2. What the LLM may and may not do

This is the part most systems get wrong, so it is stated as rules rather than
aspirations. `backend/app/ai/llm.py` is the provider seam; the rules live in
`backend/app/ai/analyst.py`.

| The LLM may | The LLM may NOT |
|---|---|
| Phrase an answer from rows the database already returned | Choose the dataset |
| Suggest which catalogue query to run | Invent a table, column, or join |
| Explain a refusal in prose | Bypass `sql_validator` |
| Be absent entirely, with an honest reason | Emit SQL that skips validation |
| | Reach the database at all |

Three structural facts enforce the right-hand column, and none of them depend on
the model behaving:

1. **The LLM has no database handle.** `complete(question, context, rows_json)`
   receives text. It returns text. There is no connection object in scope.
2. **Validation happens after translation, unconditionally.** Even if the
   provider returns SQL, stage 4 runs on it and refuses on the same terms as
   hand-written SQL.
3. **Verification happens after execution, unconditionally.** `verify_prose`
   re-reads every rendered cell and every number in the prose against the rows
   that came back. A model cannot assert a figure and skip this.

The LLM deliberately does **not** appear in the `pipeline` trace. The trace
records gates that can refuse; a narration step cannot refuse, so listing it
would imply a control that does not exist. Whether the model was used is
reported separately in the `llm` field as `llm_used`, with `fallback_reason`
naming exactly why when it was not.

### Provider status right now

`GET /api/ai/provider/status` reports `available: false` because
`OPENAI_API_KEY` is empty. `NullProvider` carries every answer, the field says
so, and the reason is quoted rather than hidden. This is an external
dependency, not an unimplemented feature: the abstraction, the seam, the
fallback and the status route are built and tested
(`backend/tests/test_llm_provider.py`).

---

## 3. Where retrieval sits

Retrieval is stage 2, and it is not decoration. It supplies the evidence that
stage 7 cites and the context that constrains stage 3. `retrieval.py` runs
BM25 and, when a vector backend is present, fuses the two with reciprocal rank
fusion; the provenance of that choice travels to the client in
`RetrievalResult` so a reader can see whether a hit came from the lexical, the
vector, or both ranking.

`vector_index.py` holds the store behind a `VectorBackend` Protocol, which is
the seam that lets a neural embedder replace LSA without touching `analyst.py`.
The corpus is this repository's own documents — `GROUNDING_CONTRACT.md`,
`docs/*.md`, the SQL files. Nothing synthetic was added to make a test pass.

`docs/retrieval.md` measures the current behaviour honestly, including where
LSA does *not* help.

---

## 4. The write barrier

Four independent controls stand between a question and a changed row. Any one
of them alone would be enough for a read-only system; they are layered because
each covers the other's blind spot.

| # | Control | Where | What it stops |
|---|---|---|---|
| 1 | Role | PostgreSQL | The API's own role cannot `INSERT`; 11/11 write probes refused |
| 2 | Validator | `sql_validator.py` | Dangerous SQL is refused before it is ever sent |
| 3 | `default_transaction_read_only` | session | A write that slips past 1-2 still fails |
| 4 | `statement_timeout` = 300000 ms | session | A runaway read is cut off, not left holding a connection |

The validator is also the only entry point for SQL. `POST /api/ai/validate`
exists so an operator can test a query, and `POST /api/analytics/queries/{name}`
only runs queries already in `backend/sql/` by catalogue name. There is no
route anywhere that accepts caller-supplied SQL and runs it.

---

## 5. Request lifecycle

```
client
  │  X-API-Key: omr_...
  ▼
RateLimitMiddleware          outermost; token bucket, per process
  ▼
CORSMiddleware
  ▼
route → authenticate()       deps.py; revocation, scope, expiry in SQL
  ▼
handler
```

Order matters. The rate limiter is added last so Starlette makes it outermost:
an unauthenticated flood is bounded before it can reach the database lookup,
and `/api/health` is exempt so an orchestrator's probe cannot be throttled into
reporting the service as down.

Expiry is decided **in SQL**, not in Python. `expires_at` is a naive timestamp
in the session timezone; comparing it against `datetime.now(timezone.utc)` in
Python made expired keys work for roughly 5.5 extra hours. The fix and its
mutation test are in `backend/tests/test_api_key_expiry_timezone.py`.

---

## 6. The API surface

19 routes, mounted in `backend/app/main.py`:

| Prefix | Router | Purpose |
|---|---|---|
| `/api/health`, `/api/status` | `health` | Liveness and database posture; public |
| `/api/overview` | `overview` | Cross-source KPIs, never summed across sources |
| `/api/analytics/queries` | `analytics` | The 41 catalogue queries, by name |
| `/api/ai/*` | `ai` | Ask, ask/document, search, translate, validate, guards, provider status |
| `/api/forecasting`, `/api/anomalies` | `insights` | Model outputs with their artefact dates |
| `/api/integrations/*` | `integrations` | Power BI availability probe, output lineage |
| `/api/rate-limit` | `rate_limit` | Live limiter configuration |

`/api/health` is the only public route. Every other route requires a live,
unrevoked, unexpired key with the scope it declares — verified across 8
credential states by `backend/scripts/verify/verify_security_matrix.py`.

---

## 7. Related

- [`GROUNDING_CONTRACT.md`](../backend/app/ai/GROUNDING_CONTRACT.md) — the
  premises the guards refuse to confirm.
- [`docs/retrieval.md`](retrieval.md) — hybrid retrieval, measured.
- [`docs/api.md`](api.md) — the endpoint reference.
- [`docs/database.md`](database.md) — schema, constraints, migrations.
- [`backend/config/output_lineage.yaml`](../backend/config/output_lineage.yaml)
  — every output mapped to a real table, query, endpoint and page.