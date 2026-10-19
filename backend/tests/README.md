# Tests

Two things in this project can drift apart silently, and both are the kind of
drift that survives review because the file still looks right:

1. **The documentation and the data.** `docs/sql_analytics.md` quotes figures
   in prose. If the data changes, or somebody edits a sentence, the document
   becomes confidently wrong.
2. **The models and the database.** `backend/app/models/` is generated from the
   live catalog, but it is checked into git, so it is still only a snapshot.

A third, added later: **the migrations and the models**. Alembic cannot compare
primary keys at all (see `backend/alembic/README.md`), so `test_migrations.py`
does it by hand.

A fourth: **the loader and the migration.** Three layers used to be able to create
the same schema, so nothing failed when they started disagreeing - it just became
unknown which one the database matched.

A fifth, and the one that matters most before any AI layer exists: **the data
and the claims made about it.** A number is only worth stating if it traces to a
row that was actually read, so `test_ai_grounding_contract.py` pins that rule in
executable form.

A sixth: **the API and the documentation.** The API serves the same 41 queries
the docs describe. It imports the runner's parser rather than reimplementing it,
so there is one set of `.sql` files and one name per query, and
`test_catalogue_names_match_the_sql_files` fails if they ever diverge.

## Running

```bash
# fast tier - no database round-trip, under a second
.venv/Scripts/python.exe -m pytest -m "not slow"

# slow tier - re-executes all 41 analytics queries against PostgreSQL (~12 min)
.venv/Scripts/python.exe -m pytest -m slow

# everything
.venv/Scripts/python.exe -m pytest
```

The slow tier exists because `m5.price_and_demand` aggregates 59 million rows
and joins 6.8 million prices; the whole corpus is minutes, not seconds. It is
**session-scoped**, so the corpus is executed exactly once no matter how many
tests consume it.

## What each test catches

### `test_analytics_findings.py`

| Test | Tier | The failure it exists for |
|---|---|---|
| `test_documented_figures_match_recorded_results` | fast | An edited sentence whose number is no longer in the results. No database needed. |
| `test_documented_figures_match_live_database` | slow | The database changing underneath the documentation. This is the one the fast test cannot see. |
| `test_every_query_executed_successfully` | slow | A query that used to work and now errors. Failures are excluded from the docs, so a broken query could otherwise vanish without anyone noticing. |
| `test_every_query_is_documented` | fast | A new query added with no written finding, which would appear in the docs unexplained. |
| `test_findings_reference_real_queries` | fast | A finding for a query that was renamed or deleted, which is otherwise silently ignored. |
| `test_query_corpus_is_fully_covered` | slow | A query disappearing. Asserted as exact counts on purpose - if one is deleted that is a decision to make out loud. |
| `test_generated_document_is_up_to_date` | slow | `docs/sql_analytics.md` not matching what the generator produces. |
| `test_spot_check_a_known_finding` | slow | A weakening of the number-checking tokenizer itself. Hard-codes one figure (Olist review coverage 99.23%) so the guard has a floor of its own. |

### `test_models_match_catalog.py`

Compares `Base.metadata` against the live catalog field by field: table set,
column names, types, nullability, primary keys, foreign keys, unique
constraints.

| Test | The failure it exists for |
|---|---|
| `test_same_tables` | A table added or dropped in PostgreSQL without regenerating. Also pins the total at 30. |
| `test_columns_match` | A column added, removed, retyped or made nullable in the database only. |
| `test_primary_keys_match` | A key changing in the database only. |
| `test_foreign_keys_match` | A key appearing or vanishing, including its real name. |
| `test_unique_constraints_match` | A unique constraint changing. |
| `test_no_model_relationship_crosses_a_source` | Somebody hand-editing a generated file to join two companies. dunnhumby household 123 must never join Olist customer 123. |
| `test_mappers_configure` | A relationship that parses but cannot resolve at mapper-configuration time. |
| `test_loaded_database_is_stamped_at_head` | The database being loadable but not under migration control. `04_load.py` no longer creates tables, so it must be migrated. Fix: `alembic stamp head`. |
| `test_generator_output_is_current` | The checked-in model files being stale. |
| `test_autogenerate_finds_nothing_to_do` | Alembic's own comparator finding drift. Strong evidence — except for primary keys, which it cannot see. |
| `test_primary_keyless_table_renders_no_primary_key` | `dunnhumby.coupon_redemptions` gaining a primary key in the models, or a `PRIMARY KEY` leaking into rendered DDL. |
| `test_autogenerate_cannot_see_a_changed_primary_key` | A future Alembic version gaining a primary-key comparator, which would silently invalidate the reasoning in the two tests above. Fails on purpose rather than lying. |

### `test_migrations.py`

Parses the baseline migration's AST rather than executing it. Executing it needs
a `CREATEDB` privilege the `omniretail` role deliberately lacks; the build was
done once by hand and the result is recorded in `backend/alembic/README.md`.

| Test | The failure it exists for |
|---|---|
| `test_single_linear_head` | A branch in the history appearing without anybody deciding on one. |
| `test_baseline_creates_exactly_the_modelled_tables` | A table added to the models with no migration written. A fresh `upgrade head` would silently build 29 tables where the code expects 30. |
| `test_baseline_creates_the_five_schemas` | The hand-added `CREATE SCHEMA` block being deleted as "redundant with the bootstrap". Alembic 1.20.0 has no `create_schema` op and autogenerate never emits one, so `upgrade head` on an empty database fails without it. |
| `test_baseline_schema_comments_match_the_bootstrap` | The schema comments drifting between the migration and `03_schemas_and_grants.sql`, which deliberately duplicates them. Two sources of truth for one sentence always diverge. |
| `test_baseline_keys_match_the_models` | Primary keys disagreeing between the migration and the models. Alembic cannot do this check, which is exactly why it is done here. |
| `test_baseline_preserves_the_keyless_coupon_redemptions` | The rejected candidate key `(household_key, day, coupon_upc)` coming back. It was rejected against real rows: 14 of them repeat it. |
| `test_baseline_has_no_cross_source_foreign_key` | A hand-edited `ForeignKeyConstraint` pointing at another schema. The models are already checked for this; the migration is the artefact a DBA actually runs. |
| `test_baseline_drops_the_schemas_it_creates` | A downgrade that leaves schemas behind, so `downgrade base` then `upgrade head` is not a round trip. |
| `test_loaded_database_is_stamped_at_head` | The database being loadable but not under migration control. `04_load.py` creates tables directly, so Alembic has to be told. Fix: `alembic stamp head`. |

Every one of these was negative-tested: each was broken on purpose, the
corresponding test was confirmed to fail, and the baseline was restored
byte-identical.

### `test_loader_alembic_split.py`

Enforces the ownership boundary: bootstrap owns roles, Alembic owns the schema,
the loader owns the rows.

| Test | The failure it exists for |
|---|---|
| `test_loader_issues_no_ddl` | `CREATE TABLE` / `DROP TABLE` / `ALTER TABLE` creeping back into the loader. Parses the source with `ast`, so the modules' long docstrings about the DDL they no longer run do not trip it — but f-strings are read, which is the whole point: every SQL string in the loader is one, and a scanner that skipped them passed a smuggled `CREATE TABLE` when this test was first written. |
| `test_loader_has_no_constraint_creation_helpers` | `add_primary_key` and friends surviving as dead code. Dead code that creates constraints is worse than none: it reads as an available option. |
| `test_loader_refuses_to_run_without_a_migrated_database` | The loader carrying on against a database the old loader built. It must stop and say which command fixes it, because stamping a database whose schema does not match would mark it correct forever. |
| `test_declared_constraints_match_the_migration` | What the loader verifies being a different set from what the migration declared — which would make "46 constraints verified" a claim about 46 of an unknown number. |
| `test_coupon_redemptions_is_still_keyless` | A primary key appearing on the table that refused one. 14 rows genuinely repeat the candidate key. |
| `test_reviews_primary_key_is_the_composite` | `olist.reviews` reverting to `review_id` alone, which would reintroduce double-counted review metrics. |
| `test_load_report_records_the_migration_it_verified` | A report whose constraint verdicts cannot be traced to a schema revision, or one still carrying the pre-Alembic bucket names. |
| `test_no_schema_drift_after_the_load` | The end-to-end statement of the split: after 106 million rows land, Alembic still finds nothing to change. Says nothing about primary keys — see below. |
| `test_migration_types_match_the_documented_inference_rules` | The type rules in `db_constraints.yaml` (money is NUMERIC, not DOUBLE PRECISION) rotting into a comment that no longer matches the schema. Marked `slow`: it samples all 30 processed CSVs. |

### `test_data_coverage.py`

Coverage is a different question from consistency. Every other check asks "is
this value right?"; these ask "is anything *missing*?"

| Test | Tier | The failure it exists for |
|---|---|---|
| `test_m5_grid_is_complete_not_merely_covering_a_range` | slow | A missing cell in the 30,490 x 1,941 sales grid. `count(*)` alone passes, and so does a min/max range check - only the product `series x days == rows` distinguishes a complete grid from a truncated one. Marked slow because a bare `count(*)` over 59 million rows costs ~13s. |
| `test_the_28_calendar_only_days_are_a_horizon_not_a_truncated_load` | slow | The forecast horizon closing. Asserts the 28-day gap *exists*, which is the opposite of a completeness check and just as important: without it there is nothing left to evaluate a forecast against. |
| `test_orders_without_payments_are_the_publishers_own_gaps` | fast | Deleting an order's only payment row. The database would have 2 payment-less orders and the raw CSV has 1 - a pipeline discrepancy, not a source property. |
| `test_orders_without_items_are_the_publishers_own_gaps` | fast | Same, for the 775 orders with no item rows (603 unavailable, 164 canceled, and the rest never completed). |
| `test_the_shortfalls_are_recorded_as_corroborated` | fast | A coverage gap sitting in the report uncorroborated, where an AI layer would read it as an unexplained shortfall. |
| `test_time_that_is_not_a_timestamp_is_still_measured` | fast | 20 of 30 tables reporting "no date column" because their time axis is an index (`m5.d_num`) or a code (`dunnhumby.day`, `wm_yr_wk`). The audit must measure a fifth of the data, not skip it. |
| `test_m5_day_indices_are_resolved_to_real_dates` | fast | A coverage range reported as "1..1941", which is not a date. The audit resolves `d_num` through `m5.calendar`. |
| `test_the_audits_temporal_table_names_real_columns` | fast | A stale column in the audit's own table, which would raise at runtime and take the entire audit down. `wm_yr_wk` is on `m5.sell_prices`, not `m5.sales_long` - that mistake was made and fixed. |

### `test_ai_grounding_contract.py`

The anti-hallucination contract, in executable form. **No AI layer exists yet**
and building one is not this phase's job, so this file does two things: proves
the controls that exist today, and pins the ones that must exist before any LLM
is allowed to answer a question.

`REQUIRED_CONTROLS` names five controls that are **not built** -
`sql_validator`, `result_verification`, `evidence_requirement`,
`false_premise_handling`, `cross_source_guard` - each with what it must reject
and the test that will prove it.

| Test | The failure it exists for |
|---|---|
| `test_ai_role_cannot_write_to_anything` | `omniretail_readonly` gaining a privilege beyond SELECT. The database refusing writes is defence in depth that actually holds; the application promising not to write is not. |
| `test_ai_role_holds_no_create_on_any_source_schema` | The read-only role acquiring CREATE, which would let it add a table to hide data in. |
| `test_every_ai_session_starts_read_only` | The role-level `default_transaction_read_only` being dropped. The application flag is the first line of defence; this is the one that holds when the first is missing. |
| `test_no_data_defects_are_known` | A defect recorded by the audit sitting in the report instead of blocking the next phase. |
| `test_every_source_anomaly_is_classified_not_just_flagged` | An anomaly flagged but never checked against the raw file. If the source does not reproduce it, the pipeline introduced it. |
| `test_cross_source_joins_are_impossible_in_the_data` | A cross-source foreign key appearing. Asserted over the models, so `olist customer 123` can never be joined to `dunnhumby household 123`. |
| `test_known_fanouts_are_documented_so_the_ai_can_be_told_about_them` | The `olist.reviews` grain finding going stale. Note `duplicate_groups` is 0 there and correct - the grain IS `(review_id, order_id)`. The fan-out is 789 review_ids across orders, an 814-row surplus, one level up. An auditor checking only `duplicate_groups` would wrongly call the table clean. |
| `test_keyless_table_is_still_keyless` | A surrogate key appearing on `dunnhumby.coupon_redemptions` so an ORM could map it. The answer to "how many redemptions" is 2,318 ROWS, not a distinct count under an invented key. |
| `test_the_audit_refused_every_write_it_tried` | The nine forbidden statements not actually being refused. A backstop that does not stop anything is decoration. |
| `test_no_control_is_marked_implemented_without_proving_it` | A required control being quietly retired. Implementing one means deleting its placeholder AND adding the test named in `test_when_built`. |
| `test_the_keyword_list_covers_every_statement_the_audit_probed` | The future validator's keyword list drifting from the nine statements actually probed. |
| `test_the_contract_is_written_and_covers_every_required_control` | A control living only in a test. `backend/app/ai/GROUNDING_CONTRACT.md` is what the prompt builder will load; a rule it does not contain cannot be prompted for. |
| `test_forbidden_phrases_are_named_in_the_contract` | "OmniRetail Total Revenue" being left implied rather than named. Four unrelated companies cannot be summed, and this is the one question where the fluent wrong answer is available. |
| `test_the_contract_lists_the_known_false_premises` | The verified traps - `(review_id, order_id)`, 801 households, 96.88% - being dropped from the contract. Those are exactly what a model will confidently get wrong. |

### `test_api.py`

Every test runs against the real database. A mocked database proves the code
parses; it does not prove `revenue` is revenue or that 801 is still the number of
dunnhumby households with demographics. The API is a delivery mechanism for facts
measured elsewhere in this project, so it must not be able to serve something
else.

| Test | The failure it exists for |
|---|---|
| `test_overview_never_sums_across_sources` | A cross-source total appearing anywhere in the payload. Asserted over KEY names, not the serialised text - the disclaimer legitimately contains "combined" and "total" because it explains why they are absent, so searching the text would make the explanation itself the failure. |
| `test_overview_dunnhumby_reports_801_not_2500` | The most misleading number this API could serve. 2,500 households transact; 801 have demographics. Both are true, so reporting the wrong one reads as correct. |
| `test_overview_money_is_a_number_not_a_string` | `json.dumps(..., default=str)` rendering revenue as the string `"16008872.12"`. The frontend then does string arithmetic and the number is quietly wrong. |
| `test_overview_survives_one_failing_source` | One bad query blanking every KPI. Three sources with real numbers plus an error beats a 500. |
| `test_catalogue_names_match_the_sql_files` | The API's query names drifting from `_sql_results.json`, so the docs describe queries the API cannot run. |
| `test_sorting_on_an_injected_column_is_ignored_not_executed` | `sort_by` reaching the SQL unfiltered. `sort_by` comes from a URL, so this is injection. Asserts the table survives too, not just that the request 200s. |
| `test_a_bogus_sort_key_is_reported` | A rejected sort key being silently dropped. "My sort did nothing" must be stated, in the body and in a header. |
| `test_pagination_windows_do_not_overlap` | A page boundary that returns a row twice. Without a deterministic ORDER BY, `LIMIT n OFFSET m` is not a stable page - and this looks like intermittent data corruption. |
| `test_limit_is_capped` | A limit above the cap being silently clamped. A clamp looks like the server ignoring the caller; a 422 is correctable. |
| `test_query_results_are_json_safe` | A `Decimal`, `date` or `bytes` surviving into the payload. Slow - `m5.store_comparison` aggregates 59 million rows. |
| `test_a_date_column_comes_back_as_an_iso_string` | A date arriving as a Python repr. Asserted as ISO 8601 rather than bare `YYYY-MM-DD`, because `m5.calendar.date` is a TIMESTAMP and legitimately carries a midnight time component. Slow. |
| `test_there_is_no_endpoint_that_accepts_sql` | A `?sql=` parameter appearing. Asserted by walking the OpenAPI schema, so it cannot be added by accident. This is the control that keeps the NL-to-SQL phase from shipping only its dangerous half. |
| `test_no_write_methods_are_exposed` | A POST or DELETE. There is nothing in this project to write. |
| `test_an_internal_error_does_not_leak_detail` | A 500 echoing the failing statement or a traceback, which teaches an attacker the schema. Asserts a fake secret does not appear. |
| `test_cors_allows_only_configured_origins` | A wildcard origin letting any page a developer visits read the API. |
| `test_the_api_role_cannot_execute_a_write` | The API session path losing the read-only refusal the audit proves directly. |
| `test_the_api_connection_cannot_write_even_without_the_explicit_setting` | Relying only on per-request `SET TRANSACTION READ ONLY`. The role default is the layer that holds when a future code path forgets it. |

### `test_analytics_runner.py`

| Test | The failure it exists for |
|---|---|
| `test_a_narrowed_run_merges_instead_of_rebuilding` | `--only <name>` rewriting `_sql_results.json` down to one query. The merge branch was gated on `args.source` alone, so a one-query run destroyed the recorded evidence for the other 40 - which is what every documented figure is validated against. Found by doing it. |
| `test_the_runner_has_a_narrowing_flag_for_each_dimension` | "Fixing" that bug by deleting `--only` instead of making it merge. |
| `test_results_file_holds_the_whole_corpus` | A results file already truncated by an earlier scoped run, whether from the bug above or by hand. Asserted about the file, not about a fresh run. |
| `test_each_recorded_result_carries_its_rows` | A corrupted entry persisting. The merge path writes whatever the previous file held. |
| `test_query_names_are_unique` | A duplicate name, since the merge is keyed by name and would silently drop one. |

## Five things that will confuse you

**Types are compared through the PostgreSQL dialect, not `str()`.** `str()` uses
the generic dialect, where `DateTime()` renders as `DATETIME` and `Double()` as
`DOUBLE`, while reflection reports `TIMESTAMP` and `DOUBLE PRECISION`. Those are
the same types on PostgreSQL. Comparing raw strings reported drift that did not
exist - which is how this suite caught itself being wrong before it caught
anything else.

**Live query results are JSON round-tripped before the document is rebuilt.**
psycopg returns `Decimal` and `datetime`; `json.dumps(default=str)` is what
turns them into the strings the generator actually writes. Comparing raw live
values against a document built from serialised ones reports formatting
differences that are not drift.

**An empty Alembic diff is weaker evidence than it looks.** `alembic revision
--autogenerate` compares unique constraints, indexes, foreign keys and check
constraints. It has *no primary-key comparator*, so a key added to or removed
from a model is invisible to it - the only symptom is an unrelated
`modify_nullable`. That is why `test_primary_keys_match` and
`test_baseline_keys_match_the_models` exist, and why
`test_autogenerate_cannot_see_a_changed_primary_key` pins the limitation rather
than hoping nobody notices.

**A test that reads SQL as text proves nothing about whether it runs.** The
baseline's downgrade contained `DROP SCHEMA IF EXISTS analytics AND NOT EXISTS
(...)`, which greps for `DROP SCHEMA`, parses as Python, and is a PostgreSQL
syntax error. It survived until somebody ran `alembic downgrade base` on a
scratch database. `test_downgrade_round_trip_executes` calls the migration's
real `downgrade()` now.

**An anomaly is not a defect until the raw file has been asked.** 1,359 Olist
orders are handed to the carrier before approval, and the temptation is to call
that a broken ETL. The raw publisher CSV has 1,359 too. Same for 61 delivered
early, 166 handed over before purchase, 9 zero-value payments, and 775 orders
with no item rows. Every one of these is a faithful copy of an imperfect source.
The audit re-derives each from the raw CSV on every run and only calls it a
defect when the two disagree - and the allowances are *measured*, never
hardcoded, so a changed raw file cannot be silently tolerated.

**`duplicate_groups == 0` can be correct and still mean a fan-out exists.**
`olist.reviews` has 0 duplicate groups at its declared grain, because that grain
is `(review_id, order_id)` and the pair is unique. The problem is one level up:
789 `review_id` values attach to more than one order. Reading the grain section
alone would conclude the table is clean.

## When a test fails

Failure messages name the command that fixes them. Do not edit the generated
files or the document by hand - regenerate, then re-run:

```bash
.venv/Scripts/python.exe backend/scripts/sql/run_analytics.py
.venv/Scripts/python.exe backend/scripts/docs/generate_sql_analytics.py
.venv/Scripts/python.exe backend/scripts/db/06_generate_models.py
```

If the failing test is `test_loaded_database_is_stamped_at_head`, the fix is
`.venv/Scripts/python.exe -m alembic stamp head` — it records that the baseline
already describes what is loaded, without running it.

If a constraint count in `_load_report.json` looks stale, re-measure rather than
edit:

```bash
.venv/Scripts/python.exe backend/scripts/db/04_load.py --verify-only
```

106 seconds, no COPY, no TRUNCATE — it cannot damage the data.

If the audit report is missing or a coverage figure looks stale, re-run it. It
connects as `omniretail_readonly`, so it cannot alter the data it measures:

```bash
.venv/Scripts/python.exe backend/scripts/db/07_data_truth_audit.py
```

Roughly four minutes over 106 million rows.