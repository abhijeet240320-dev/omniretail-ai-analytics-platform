# OmniRetail AI Analytics Platform

Read-only analytics over **four real public retail datasets**, 30 tables and
106,704,583 rows, served through FastAPI, answered by an AI analyst that
refuses rather than guesses, and measured by forecasting and anomaly detection
that were actually run.

```
NO VERIFIED EVIDENCE
        ->  NO FACTUAL ANSWER
```

Every number in this repository came out of a query that ran. Nothing is
synthetic, mocked, or typed from memory.

---

## 1. The data

There is no fictional retailer here. There are four datasets from four
**unrelated companies**, downloaded from their publishers and loaded
unmodified:

| Schema | Source | Tables | Rows |
|---|---|---:|---:|
| `olist` | Brazilian e-commerce marketplace | 12 | 774,081 |
| `dataco` | Supply-chain DataCo | 4 | 267,041 |
| `dunnhumby` | US grocery panel | 9 | 39,606,850 |
| `m5` | Walmart daily sales (M5) | 5 | 66,056,611 |
| **Total** | | **30** | **106,704,583** |

Counts are from `data/processed/_load_report.json`, written by the loader that
copied the rows. See [docs/data_sources.md](docs/data_sources.md) and
[docs/data_lineage.md](docs/data_lineage.md). For the other direction - from a
number on screen back to the table and query it came from - see
[backend/config/output_lineage.yaml](backend/config/output_lineage.yaml) and
`GET /api/integrations/lineage`.

### The consequence that shapes the whole system

**These four sources cannot be joined.** No shared customer, no shared order, no
verified mapping between them, and **no foreign key anywhere in the database
that crosses a schema boundary** -- asserted by
`test_cross_source_joins_are_impossible_in_the_data`.

So there is no "OmniRetail total revenue". Four companies, four accounting
systems, four different definitions of a sale. A sum across them is not hard to
compute; it is a number with **no referent**. Every layer of this system
refuses to produce one, and says which dataset it can answer for instead.

---

## 2. What is built

```
                                  BROWSER
                    +-------------------------------+
                    |  React 18 + TypeScript (Vite) |
                    |  frontend/src/                |
                    +---------------+---------------+
                                    | X-API-Key
                    +---------------v---------------+
                    |        FastAPI  app/          |
                    |  /api/health  (open)          |
                    |  /api/status   (admin:read)   |
                    |  /api/overview (analytics:read)
                    |  /api/analytics/queries       |
                    |  /api/ai/ask   (ai:read) -----+---+
                    +---------------+---------------+   |
                                    |               |   v
             +----------------------+-------+   +---+------------------+
             |                      |       |   |  AI ANALYST        |
             v                      v       |   |  6 gates, any can  |
      +--------------+        +------------+  |  refuse:           |
      | 41 catalogued|        | 5 REQUIRED |  |  guards / evidence |
      | SQL queries  |        | CONTROLS   |  |  translate /       |
      +--------------+        +------------+  |  validator /       |
             |                      |       |  |  execute / verify  |
             |                      |       |  +---+------------------+
             |                      |       |   |
             |              +-------v-------+---v---+
             |              |  RAG (BM25) over the    |
             |              |  committed corpus:      |
             |              |  docs/*.md, sql/*.sql,  |
             |              |  config/*.yaml, *.json  |
             |              +-----------------------+
             v
      +--------------------------------------------------+
      | PostgreSQL 18.6                                   |
      |   omniretail        read/write: loader, Alembic   |
      |   omniretail_readonly  SELECT ONLY: everything    |
      |                        above                       |
      | 30 tables, 29 PK, 2 UQ, 15 FK, 0 NOT VALID       |
      +--------------------------------------------------+
             ^                                    ^
             |                                    |
   raw CSVs (never modified)          Power BI  powerbi/measures.dax
   data/raw/  (1.44 GiB, 33 files)   read-only role, 4 independent
                                     fact tables, no shared dimension
```

### The AI analyst: six gates, any of which can refuse

`POST /api/ai/ask` runs a question through:

1. **guards** - is the question answerable as asked?
2. **evidence** - does the verified corpus support an answer?
3. **translation** - is it one of the 41 catalogued queries?
4. **sql_validator** - is the statement safe to run?
5. **execute** - real rows, read-only role
6. **verification** - is every number traceable to a returned row?

A refusal is a **200 with `answered: false`** and a `stopped_at` stage. It is
not an error, because refusing is the correct behaviour for most questions
worth asking. The UI renders it as a first-class result in a filled panel, with
the reason, the rule that fired, and a citation.

The five controls this implements are specified in
[backend/app/ai/GROUNDING_CONTRACT.md](backend/app/ai/GROUNDING_CONTRACT.md) and
proven by [backend/tests/test_ai_controls.py](backend/tests/test_ai_controls.py).

---

## 3. Running it

```bash
# 0. dependencies (Windows/Git Bash paths shown)
.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
cd frontend && npm install && cd ..

# 1. database
bash backend/scripts/db/pg_ctl.sh start
.venv/Scripts/python.exe backend/scripts/db/bootstrap_db.py
.venv/Scripts/python.exe -m alembic upgrade head

# 2. API
.venv/Scripts/python.exe -m uvicorn app.main:app --app-dir backend --reload

# 3. frontend (separate shell)
cd frontend && npx vite            # http://localhost:5173
```

The dev server proxies `/api` to the backend, so the browser sees one origin.

### An API key

Every endpoint except `/api/health` needs one:

```bash
.venv/Scripts/python.exe backend/scripts/db/08_issue_api_key.py issue \
  --label "analyst" --scopes "analytics:read,ai:read"
```

Paste it into the frontend header. It is held in `sessionStorage` only -- never
`localStorage`, never in the bundle.

---

## 4. The analytics

**41 hand-written SQL queries** across the four sources, each carrying the
question it answers, each executed against the live database with its result
recorded in `data/processed/_sql_results.json`. Documented in
[docs/sql_analytics.md](docs/sql_analytics.md).

The generator refuses to write a figure that is not present in the recorded
result rows, and `test_analytics_findings.py` fails the build if a documented
number stops being true.

---

## 5. Forecasting and anomaly detection

Both were run, and both documents are generated from the measurement files.

**Forecasting** - 5 models on all 30,490 M5 series against a 28-day holdout
(train `d_num` 1..1913, test 1914..1941 - the test window matches the 28
forecast-horizon days `m5.calendar` carries past the last observation):

| model | MAE | RMSE | MASE | WMAPE | bias |
|---|---:|---:|---:|---:|---:|
| `naive` | 1.3730 | 2.8936 | 2.0521 | 0.9516 | +0.1903 |
| **`sma_7`** | **1.0683** | **2.2487** | **1.6717** | **0.7404** | -0.1062 |
| `seasonal_naive_7` | 1.2440 | 2.6769 | 1.8695 | 0.8622 | -0.1062 |
| `croston` | 1.2628 | 3.2197 | 1.7012 | 0.8752 | -0.8587 |
| `sbF` | 1.2692 | 3.2465 | 1.6966 | 0.8797 | -0.8880 |

`sma_7` wins by 1.5% -- a real but *small* margin, and the per-band table in
[docs/forecasting.md](docs/forecasting.md) matters more than the headline: no
model wins all three intermittency bands.

**Anomaly detection** - three detectors, one per source, because the three
violate three different assumptions. Olist orders by day (same-weekday rolling
mean, 28 weeks, |z| >= 3); dunnhumby basket lines (30-day rolling median,
1.4826 x MAD, |z| >= 3); M5 total daily units (56-day rolling, |z| >= 4).

**All 5 flagged M5 days are Christmas Day.** That is a detector working and a
dataset with no incidents in it, and it is the honest answer to "what went
wrong here". Every flagged point carries its baseline, threshold and score --
see [docs/anomaly_detection.md](docs/anomaly_detection.md).

---

## 6. Repository layout

```
backend/
  app/
    api/          analytics, overview, health, ai, deps (auth)
    ai/           sql_validator, retrieval, guards, nl_to_sql,
                 verification, analyst, GROUNDING_CONTRACT.md
    core/         config, security (Argon2id API keys)
    database/     readonly engine + read-only session
    models/       30 SQLAlchemy models generated from the live catalog
    schemas/      Pydantic response models
    services/     query_catalogue, analytics, forecasting,
                 anomaly_detection
  alembic/        baseline (30 tables) + app.api_keys migration
  config/         datasets, constraints, expected schemas, sql_findings
  etl/            per-source Pandas ETL
  load/           loader (issues ZERO DDL) + schema helpers
  scripts/
    data/         01_download, 02_verify, 03_profile
    db/           04_load .. 08_issue_api_key, bootstrap, pg_ctl
    docs/         generate_lineage, generate_sql_analytics,
                  generate_forecasting, generate_anomaly_detection
    ml/           01_evaluate_forecast, 02_detect_anomalies
    sql/          run_analytics
  sql/            the 41 catalogued queries, one file per source
  tests/          pytest suite
frontend/         React 18 + TypeScript + Vite + Vitest
powerbi/          measures.dax, data_model.md
docs/             generated documentation
data/
  raw/            publisher files + _checksums.json (never modified)
  processed/      ETL output + _load_report, _verify_report,
                  _sql_results, _audit_report,
                  _forecast_results, _anomaly_results
```

---

## 7. Documentation

| File | What it covers |
|---|---|
| [docs/api.md](docs/api.md) | Every endpoint, the six AI gates, auth |
| [docs/sql_analytics.md](docs/sql_analytics.md) | The 41 queries and their results |
| [docs/data_lineage.md](docs/data_lineage.md) | Raw file -> table -> column |
| [docs/data_dictionary.md](docs/data_dictionary.md) | Every column, its type, its meaning |
| [docs/data_sources.md](docs/data_sources.md) | Provenance of each dataset |
| [docs/database.md](docs/database.md) | Schema, keys, constraints |
| [docs/forecasting.md](docs/forecasting.md) | Models, metrics, per-band results |
| [docs/anomaly_detection.md](docs/anomaly_detection.md) | Detectors, baselines, findings |
| [docs/retrieval.md](docs/retrieval.md) | The RAG pipeline, and what embeddings it does not use |
| [docs/architecture.md](docs/architecture.md) | Where retrieval and the LLM sit, and what each may not do |
| [backend/app/ai/GROUNDING_CONTRACT.md](backend/app/ai/GROUNDING_CONTRACT.md) | The anti-hallucination rules the AI layer enforces |
| [backend/config/output_lineage.yaml](backend/config/output_lineage.yaml) | Every output -> table -> grain -> SQL -> endpoint -> page |

Documentation is GENERATED from machine-readable evidence wherever a number is
involved. `docs/sql_analytics.md`, `docs/forecasting.md` and
`docs/anomaly_detection.md` refuse to be written from a partial or stale run,
and tests fail if a document and its evidence file disagree.

`backend/config/output_lineage.yaml` is the other half of that: it maps every
analytical output to the table, grain, catalogued query, API endpoint and
frontend page behind it, and `backend/tests/test_output_lineage.py` checks
every field against PostgreSQL, the query catalogue, the FastAPI routes and
`routes.ts`. It is served at `GET /api/integrations/lineage`. That test earned
its place immediately - it caught three table names in the first draft that did
not exist.

---

## 8. Tests

```bash
.venv/Scripts/python.exe -m pytest backend/tests -q            # everything
.venv/Scripts/python.exe -m pytest backend/tests -q -m "not slow"   # fast tier
cd frontend && npx vitest run                                  # 61 frontend tests
```

The suite is two-tier. Fast tests validate written findings against the
committed `_sql_results.json` and need no database. `slow` tests re-execute all
41 queries against PostgreSQL and re-validate, catching the other failure: the
data changed under the documentation.

Nothing in the backend suite mocks the database. Every backend test runs against
the real 106,704,583 rows, which is why the fast tier still takes minutes - it
is minutes of database time, not test-harness time.

---

## 9. Security posture

| Control | Enforcement |
|---|---|
| Read-only database role | `omniretail_readonly` holds SELECT and nothing else, with `default_transaction_read_only = on` at role level. Verified at runtime by `/api/status`, not asserted in a README. |
| No endpoint executes caller-supplied SQL | `/api/ai/validate` checks and returns a verdict. It never runs. `/api/ai/translate` returns only catalogued statements, verbatim. |
| API keys | Argon2id (t=3, m=65536, p=4), stored hashed, per-key scopes, `X-API-Key` header only |
| Timing oracle | Found and fixed: an absent key returned 24.48x faster than a present one. The miss path now verifies a dummy hash; measured ratio 1.02. Pinned by `test_auth.py`. |
| Prompt injection | Six injection patterns are rejected by the validator and produce no SQL through the analyst |
| Errors | 500s name the exception TYPE only. The SQL and the traceback go to the log. |
| Query cost | Every read-only connection carries a `statement_timeout` (300000 ms, chosen from the slowest measured query). PostgreSQL's default is 0 - wait forever. A cancellation is a 504, not a 502, because the SQL was valid. `/api/health` reports the timeout a live connection actually has and degrades if it disagrees with the configured one. |
| Expensive counts | `include_total=false` skips the unpaginated `count(*)`, which on the m5 aggregates costs as much as the page itself. The response then says `total_rows: null` - never backfilled with `row_count`. |
| Cached data is labelled as cached | `/api/overview` reuses its result for 120 s and reports `cached`, `age_seconds` and the real `generated_at`. A partial response is never cached. |
| Test credentials cannot outlive a run | `conftest.py` revokes any active `pytest%` key at session start and fails the suite at session end if one survives. |

---

## 10. Limitations

Stated plainly, because a project that hides them is not auditable.

- **The AI analyst answers the 41 catalogued questions**, not arbitrary ones.
  `OPENAI_API_KEY` is empty and no model is installed, so the natural-language
  path is deterministic retrieval over verified statements. A question outside
  the catalogue is refused with its nearest matches. That is a deliberate trade:
  an improvised query returns rows, and rows get quoted as answers.
- **Retrieval is BM25, not embeddings.** It is deterministic, offline,
  citation-backed and testable; it is also lexical, so a question that shares no
  words with the document will not retrieve it.
- **No query cost limits.** Any of the 41 queries can be requested, including the
  M5 ones that scan 59 million rows. Fine locally; a problem for a shared
  instance.
- **API keys do not expire or rotate automatically.** `expires_at` is enforced;
  nothing sets it by default.
- **`dunnhumby.coupon_redemptions` has no primary key.** The candidate key leaves
  14 duplicate groups and none was invented, so `count(*)` is the only honest
  count.
- **No CI/CD, containers or deployment.** This is a local-first analytics stack.

---

## 11. Data integrity

`backend/scripts/db/07_data_truth_audit.py` runs read-only against the loaded
data and writes `data/processed/_audit_report.json`:

- **0 defects**
- **7 warnings**, each corroborated against the raw file - an anomaly is either
  the publisher's or ours, and the report says which
- **9 forbidden writes attempted, 9 refused by PostgreSQL**

The audit refuses to pass on unexplained anomalies, and
`test_every_source_anomaly_is_classified_not_just_flagged` fails the build if a
warning is not corroborated.