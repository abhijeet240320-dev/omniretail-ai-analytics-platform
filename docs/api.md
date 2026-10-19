# OmniRetail API

Read-only analytics over four real public retail datasets. Every number returned
by this API is the result of a query that ran against **106,704,583 loaded
rows** in PostgreSQL. Nothing is mocked, cached from a fixture, or typed from
memory.

## Running it

```bash
# from the repository root
.venv/Scripts/python.exe -m uvicorn app.main:app --app-dir backend --reload
```

Then:

| URL | What |
|---|---|
| `http://127.0.0.1:8000/docs` | Swagger UI - try every endpoint from a browser |
| `http://127.0.0.1:8000/api/health` | Liveness + read-only enforcement (**no key needed**) |
| `http://127.0.0.1:8000/api/status` | Schema inventory, live row counts, Alembic revision (`admin:read`) |
| `http://127.0.0.1:8000/api/overview` | Per-source KPIs (`analytics:read`) |
| `http://127.0.0.1:8000/api/analytics/queries` | The 41 catalogued queries (`analytics:read`) |
| `http://127.0.0.1:8000/api/forecasting` | Recorded M5 forecast benchmark (`analytics:read`) |
| `http://127.0.0.1:8000/api/anomalies` | Recorded anomaly detections (`analytics:read`) |
| `http://127.0.0.1:8000/api/ai/ask` | The analyst (`ai:read`) |
| `http://127.0.0.1:8000/api/ai/validate` | SQL validator - checks, never executes (`ai:read`) |

The database must be running first: `bash backend/scripts/db/pg_ctl.sh start`.

## Authentication

Every endpoint except `/api/health` requires an API key in the `X-API-Key`
header. Keys are Argon2id hashes in `app.api_keys`; the plaintext is printed
once at issue time and never stored.

```bash
.venv/Scripts/python.exe backend/scripts/db/08_issue_api_key.py issue \
  --label "analyst" --scopes "analytics:read,ai:read"

curl -H "X-API-Key: omr_..." http://127.0.0.1:8000/api/overview
```

| Scope | Grants |
|---|---|
| `analytics:read` | `/api/overview`, `/api/analytics/*`, `/api/forecasting`, `/api/anomalies` |
| `ai:read` | `/api/ai/ask`, `/translate`, `/validate`, `/search`, `/guards`, `/check` |
| `admin:read` | `/api/status`, `/api/ai/validator/status` |

`/api/forecasting` and `/api/anomalies` were briefly mounted without a
dependency while every other route required a key. They are read-only and cheap
-- they serve a recorded JSON artefact rather than running a model -- but they
publish the whole benchmark and the whole anomaly list, so they are
`analytics:read` like the curated catalogue they sit beside. `ai:read` is
deliberately *not* enough: a key issued to ask questions should not also read the
recorded analyses. `backend/tests/test_auth.py` pins all four cases (no key,
wrong scope, revoked/expired, analytics key).

`401` means no key, a bad key, a revoked key or an expired key. `403` means a
valid key without the required scope. They are distinguished on purpose: one is
"authenticate" and the other is "ask for a different key".

**A timing oracle was found and fixed during the build.** An absent key
returned in 5.1 ms while a present one took 124.4 ms -- a 24.48x ratio, because
the absent path skipped Argon2 verification entirely and the difference told an
attacker exactly which prefixes existed. `authenticate()` now verifies against a
dummy hash on the miss path; the measured ratio is 1.02. `backend/tests/test_auth.py`
pins it, so it cannot regress.

## Endpoints

### `GET /api/health`

```json
{
  "status": "ok",
  "database_reachable": true,
  "read_only_enforced": true,
  "detail": null,
  "statement_timeout_ms": 300000
}
```

`read_only_enforced` is a **measurement**, not a claim: it asks PostgreSQL what
the `omniretail_readonly` role actually holds, and whether the role-level
`default_transaction_read_only` is set. Returns `status: "degraded"` rather than
raising if either check fails, because a health endpoint that 500s when things
are wrong teaches people to ignore it.

`statement_timeout_ms` is measured the same way -- `SHOW statement_timeout` on a
live read-only connection -- and compared against `STATEMENT_TIMEOUT_MS`. If they
disagree the response is `degraded`, because a cost guard that is configured but
not armed is worse than none: it is believed. `0` means unlimited.

### `GET /api/status`

Database name, role, `is_select_only`, `role_default_read_only`, the current
Alembic revision, and a per-table inventory with row counts.

Row counts come from `pg_class.reltuples` — the planner's estimate, not
`COUNT(*)`. Exact counts live in `/api/analytics` and in
`data/processed/_load_report.json`. Thirty exact counts including a 59-million-row
table is not something to put behind a health check.

### `GET /api/overview`

Per-source KPIs, computed from the database:

| Source | Metrics |
|---|---|
| `olist` | orders, revenue, customers, review_coverage_pct, delivered_pct |
| `dataco` | orders, revenue, profit, avg_margin_pct |
| `dunnhumby` | basket_lines, gross_sales, discounts_given, households_with_demographics, households_transacting |
| `m5` | series_days, units, stores, items, zero_sales_day_pct |

**There is no combined total, and the response shape makes that structural.**
`OverviewResponse` has `sources: list[SourceKpi]` and no `total` field. The four
datasets are unrelated companies — a Brazilian marketplace, a supply-chain
operator, a US grocery panel, Walmart — with no shared key space and different
definitions of a sale. Summing their revenue produces a number with no referent.

If one source's query fails, the other three still return real numbers and the
failure is reported in that source's `note`. A dashboard showing three sources
and one error beats a 500.

**It is cached, and it says so.** Those 26 metric queries include full scans of
`m5.sales_long` (59.2M rows); a cold call measures ~55 seconds, and two pages
need the numbers. The computed response is reused for
`OVERVIEW_CACHE_TTL_SECONDS` (120 by default), and every response carries:

| Field | Meaning |
|---|---|
| `generated_at` | when these numbers were **computed** |
| `cached` | whether THIS response came out of the cache |
| `age_seconds` | how long ago they were computed |

A response containing any source-level `note` is **never cached**, so a
transient failure is not pinned for the TTL. Set
`OVERVIEW_CACHE_TTL_SECONDS = 0` to always recompute.

### `GET /api/analytics/queries?source=<name>`

Lists the catalogued queries with their business questions. Reads the `.sql`
files; runs nothing. `source` is optional; an unknown source is a 404.

### `GET /api/analytics/queries/{name}`

Runs one query. `name` is qualified (`olist.review_coverage_and_score`) or bare
when unambiguous.

| Parameter | Default | Notes |
|---|---|---|
| `limit` | 50 | 1–1000; above the cap is a 422, not a silent clamp |
| `offset` | 0 | |
| `sort_by` | — | Ignored unless it names a column the query returns |
| `descending` | false | |
| `include_total` | true | false skips the unpaginated `count(*)`. On the m5 aggregates that count costs as much as the page itself. |

```json
{
  "query": "olist.review_coverage_and_score",
  "source": "olist",
  "columns": ["orders_total", "orders_with_review", "pct_reviewed", "..."],
  "rows": [{"orders_total": 99441, "pct_reviewed": 99.23, "...": "..."}],
  "row_count": 1,
  "total_rows": 1,
  "total_excluded": false,
  "elapsed_ms": 12.4,
  "sort_ignored": null
}
```

With `include_total=false` the response carries `"total_rows": null` and
`"total_excluded": true`. `null` means *not measured*; it is never backfilled
with `row_count`, because that substitution is wrong in exactly the case a reader
cares about -- `row_count == limit`.

### `GET /api/forecasting`

The completed M5 model-selection benchmark, served verbatim from
`data/processed/_forecast_results.json`: 30,490 series, 1,941 days, five models
scored on a 28-day holdout, `sma_7` winning by MASE with a 0.0147 margin over
the runner-up. The run's own `generated_utc` is in the payload, because a
benchmark with no timestamp is not a measurement.

It returns the artefact **as it was written**. Selecting fields here would create
a second place to keep in step with the batch run that produced them.

This is an aggregate model-selection benchmark, not a point forecast. No
per-series point forecast is stored anywhere in this project, and the response
says so in `shape` so `total_forecast` cannot be mistaken for a curve.

### `GET /api/anomalies`

The completed anomaly-detection run, flattened to one row per **firing** with
the parent metric's `method` and `baseline` attached, so a row can always be
read without its context. `metrics_count` is the number of metrics scanned (3);
`total` is the number of individual firings after filtering (63). Reporting both
is what stops a dashboard counting three when it means sixty-three.

| Parameter | Default | Notes |
|---|---|---|
| `source` | — | `olist`, `dunnhumby`, `m5` |
| `severity` | — | `critical`, `warning`, `info` |
| `limit` | 200 | 1–5000 |
| `offset` | 0 | |

Neither endpoint runs a model. If the artefact is missing the response is a 503
naming the file and the command that produces it, rather than an empty 200 that
a dashboard would render as "this dataset has no anomalies".

## Design decisions

### The SQL is a catalogue, not an input

There is no `?sql=` endpoint. That is deliberate.

A route accepting arbitrary SQL would turn a read-only role into a general
execution surface for anyone who can reach the port, and in the AI phase it
would let a prompt injection run statements. Generating SQL from a question is a
real requirement — but it needs a validator, an allowlist, and a refusal path
that explains itself. Shipping only the dangerous half now means the safety
controls get written later, under time pressure, against an interface nobody has
designed. So the 41 queries are fixed and named.

`test_there_is_no_endpoint_that_accepts_sql` asserts this by walking the OpenAPI
schema, so it cannot be added by accident.

### One parser, not two

The API imports the runner's `parse_file` rather than reimplementing the
`-- [query: name]` marker. One parser, one set of `.sql` files, one name per
query. A change to the query-file format has exactly one place to be made, and
the API cannot drift from what `docs/sql_analytics.md` documents.

`test_catalogue_names_match_the_sql_files` compares the served names against
`_sql_results.json` and fails if they diverge.

### Money is a number, not a string

psycopg returns money as `Decimal`, which has no JSON representation. The usual
workaround — `json.dumps(..., default=str)` — produces `"16008872.12"` as a
**string**. A frontend then does string arithmetic or renders it without
separators, and the number is quietly wrong.

`_jsonable()` converts `Decimal` → `float` explicitly. That is a real, documented
loss of exactness for very large or very precise amounts, and it is the right
trade for a JSON analytics API — but it is a decision, not an accident of a
default encoder. Dates become ISO 8601 strings.

### Pagination is pushed into SQL

`LIMIT`/`OFFSET` go into the statement, so PostgreSQL stops when it has enough.
Fetching all of `m5.demand_scale_by_hierarchy` — 59 million rows aggregated — to
return 50 would cost a full scan per request.

`OFFSET` degrades on deep pages. That is a performance limit, not a correctness
one: page 50,000 is slow but still correct. Keyset pagination is the right change
if these result sets ever get browsed that far; it is not the right complexity
before anyone needs it.

Three executions per request, cheapest first: `LIMIT 0` for column names (no
rows moved), the page, then `count(*)` for the total.

### Bounding what a query may cost

Two independent controls, because they bound different things.

**`statement_timeout`, per connection.** PostgreSQL's default is `0` — wait
forever. On a database with a 59-million-row fact table that is an availability
risk: one unlucky plan holds a pooled connection for as long as it likes, and the
pool is small. Every read-only connection is created with the libpq option
`-c statement_timeout=...`, so the guard applies to every statement on that
connection including ones issued by SQLAlchemy itself and by any code path added
later — a guard implemented as a `SET LOCAL` inside one dependency is a guard the
next endpoint can forget.

The default is **300 s**, chosen from a measurement rather than a guess. The
slowest of the 41 verified queries is `m5.demand_scale_by_hierarchy`, recorded at
~199–210 s by the batch runner over the full 30,490 series. A ceiling below that
would mean the API cannot serve a query this project has already verified; a
ceiling far above it would not protect anything. 300 s leaves ~45% headroom over
the measured worst case while still bounding a runaway statement.

It is a **per-statement** ceiling, so a paginated query is bounded three times
over rather than once per request. When it fires, PostgreSQL raises
`57014 query_canceled` and the API returns **504**, not 502: the SQL was valid
and ran out of time, and telling the caller the database "rejected" it sends
them hunting a syntax error that does not exist.

**`include_total=false`, per request.** `LIMIT 50` does not make these queries
cheap, because the limit is applied to a *subquery*: PostgreSQL cannot push it
into the `GROUP BY` of `m5.demand_scale_by_hierarchy`, so it computes every
group and discards most. The `count(*)` therefore costs as much as the page, and
the endpoint costs roughly twice what one execution does. A caller that renders a
table and no pager should not pay for a number it never shows.

`backend/tests/test_query_guards.py` asserts both by measurement: it asks a live
connection what timeout it got, makes a real statement overrun a lowered one,
and records every statement the session sends to prove the `count(*)` is not
executed when it has been switched off.

### Sort keys are allowlisted, not filtered

`sort_by` comes from a URL. Passing it through unfiltered would be SQL injection,
so it is checked against the columns the query actually returns — discovered by
executing with `LIMIT 0`, which yields names without transferring data.

A rejected key is **reported**, not silently dropped: `sort_ignored` in the body
and an `X-Sort-Ignored` header. "My sort did nothing" is exactly the kind of thing
a caller needs to be told about.

## Security

| Control | Where |
|---|---|
| Read-only role, SELECT only | `omniretail_readonly`; verified at runtime by `/api/health` |
| `default_transaction_read_only = on` at role level | Holds even if a code path forgets `SET TRANSACTION` |
| No write endpoints | No PUT/PATCH/DELETE anywhere; POST only on five allow-listed question endpoints. Asserted by walking the OpenAPI schema in `test_no_endpoint_mutates` |
| No SQL parameter | Asserted by walking the OpenAPI schema |
| Sort keys allowlisted | Never interpolated into SQL |
| CORS: localhost only | A wildcard would let any visited page read the API |
| Errors name the exception type only | Echoing the failing statement teaches the schema |
| Read/write credentials absent from the API process | Separate engine built from `readonly_database_url` |
| `statement_timeout` on every read-only connection | libpq connect option; 504 on cancellation; reported by `/api/health` |
| `include_total=false` to skip the unpaginated count | Stated as `total_rows: null`, never backfilled |

## The AI layer

`/api/ai/ask` runs a six-gate pipeline. Any gate can refuse, and a refusal is a
**200 with `answered: false`** -- the request succeeded, the answer is "no".

| Gate | Refuses when | `stopped_at` |
|---|---|---|
| 1. `guards` | the question rests on something untrue of this data | `false_premise` |
| 2. `retrieval` | the verified corpus supports no answer | `evidence` |
| 3. `translate` | it is not one of the 41 catalogued queries | `translation` |
| 4. `sql_validator` | the statement is not safe to run | `sql_validator` |
| 5. `execute` | PostgreSQL rejected it | `execution` |
| 6. `verification` | a number is not in the rows the query returned | `verification` |

```json
{
  "answered": false,
  "stopped_at": "false_premise",
  "matched_rules": ["combined_total"],
  "reason": "the question rests on something that is not true of this data",
  "corrections": [
    {
      "rule": "combined_total",
      "correction": "There is no OmniRetail total. These are four datasets from four unrelated companies...",
      "citation": "backend/app/ai/GROUNDING_CONTRACT.md#L28"
    }
  ],
  "rows": []
}
```

An answered response carries the same shape plus `rows`, `columns`, `citations`
and `verified` -- which lists every value that was checked and how:

```json
{
  "answered": true,
  "query": "olist.geography_revenue_concentration",
  "rows": [{"destination_state": "SP", "orders": 41745, "revenue": 5998226.96}],
  "verified": {"ok": true, "checked": [...30 values...], "failures": [],
               "checks": ["rendered_cells", "prose_numbers"]},
  "citations": ["docs/sql_analytics.md#L173"],
  "pipeline": ["guards", "guards:clear", "retrieval", "retrieval:4-hits", "...",
               "verify", "verify:ok"]
}
```

### There is no endpoint that executes caller-supplied SQL

`/api/ai/validate` checks a statement and returns a verdict. It never runs it.
`/api/ai/translate` returns SQL only when it is one of the 41 verified
statements, verbatim -- it has no path that reaches the validator with invented
SQL in the first place.

### The translator is retrieval, not generation

`OPENAI_API_KEY` in `.env` is empty and `openai` is not installed, so the
natural-language path is deterministic: rank the 41 hand-written statements and
return one verbatim. A question outside the catalogue is refused with the
nearest matches rather than approximated, because an unimprovised query returns
rows and rows get quoted as answers.

An optional LLM layer sits **after** that pipeline and may only write prose.
Its model text is re-checked with the same result verification and discarded on
failure, so a wrong model degrades to the deterministic answer rather than to a
wrong one. `GET /api/ai/provider/status` reports what this process can actually
reach. See [`docs/retrieval.md`](retrieval.md) for the retrieval half and
[`docs/architecture.md`](architecture.md) for where the LLM sits in the chain.

The five controls this implements are specified in
[`backend/app/ai/GROUNDING_CONTRACT.md`](../backend/app/ai/GROUNDING_CONTRACT.md)
and proven by `backend/tests/test_ai_controls.py`.

## Known gaps

**Rate limiting is per-process, not shared.** A token bucket now bounds each
caller at `RATE_LIMIT_PER_MINUTE` with a burst of `RATE_LIMIT_BURST`, keyed by
API key (`GET /api/rate-limit` reports the live configuration and how many
buckets are currently tracked). But the buckets live in process memory, so
**running uvicorn with more than one worker multiplies the effective limit by
the worker count**. For a single-process local instance that is exact; for a
multi-worker deployment it is not, and a shared limiter needs shared storage.
`/api/health` is exempt on purpose: a monitoring system that receives 429 has to
report the service down.

**A timeout is a ceiling and a rate limit is a rate.** Together they bound how
long one query may run and how many a caller may start, but neither knows which
query is expensive. There is no per-query cost table, so a key can still spend
its whole allowance on `m5.demand_scale_by_hierarchy`. Cost-aware scheduling is
the missing piece and it needs a cost model that does not exist yet.

**The slow queries are slow by construction.** `LIMIT` is applied to a subquery
and cannot be pushed into an aggregate, so pagination does not bound the work on
`m5`. The honest fix is per-source result materialisation or a summary table,
which is a different system rather than a parameter.

**`sort_ignored` has no effect on `total_rows`.** Unrelated, but worth knowing
that a rejected sort still costs the `count(*)`.

**API keys do not expire or rotate automatically.** `expires_at` exists and is
enforced; nothing sets it by default, and there is no rotation schedule. An
operator issues short-lived keys manually if they want them.

**The AI layer has no model in it.** See above: it is deterministic by
construction. That is a deliberate position rather than a missing
configuration, but it does mean the analyst answers the 41 catalogued questions
and not arbitrary ones.