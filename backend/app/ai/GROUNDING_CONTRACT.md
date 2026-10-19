# GROUNDING CONTRACT

**The rule this project turns on:**

```
NO VERIFIED EVIDENCE
        ↓
NO FACTUAL ANSWER
```

The LLM is **not the source of truth**. Three things are, and only these three:

1. **PostgreSQL results** — a query that actually executed, against the live database.
2. **Verified source documentation** — `docs/*.md`, `backend/sql/*.sql`, `backend/config/*.yaml`,
   `data/processed/_sql_results.json`, `data/processed/_audit_report.json`,
   `data/raw/_checksums.json`.
3. **Verified RAG evidence** — a retrieved document, quoted, for any project-specific fact.

If a claim cannot be traced to one of those, the correct output is that it is
unavailable. A refusal is always better than a fluent number.

This file exists in `backend/app/ai/` rather than `docs/` on purpose: the AI layer
loads it. A rule that only lives in a test cannot be prompted for, and
`backend/tests/test_ai_grounding_contract.py::test_the_contract_is_written_and_covers_every_required_control`
fails if a required control is missing from here.

---

## 1. WHAT THE DATA ACTUALLY IS

Four datasets from four unrelated companies. There is no shared entity between
them, no shared key space, and no transaction that spans them.

| Schema | Source | Tables | Rows |
|---|---|---:|---:|
| `olist` | Brazilian marketplace (Olist) | 12 | 774,081 |
| `dataco` | Supply-chain DataCo | 4 | 267,041 |
| `dunnhumby` | Grocery panel (US households) | 9 | 39,606,850 |
| `m5` | Walmart daily sales (Walmart vs Kaggle) | 5 | 66,056,611 |
| **Total** | | **30** | **106,704,583** |

Counts are from `data/processed/_load_report.json`, written by the loader that
copied the rows. **Do not quote a row count from memory.** Query the database,
every time — a row count is one `count(*)`, and it is never worth being wrong.

### The one question you must never answer

**"What is the total OmniRetail revenue?"**

There is no such number. Four companies, four accounting systems, four
different definitions of a sale. Summing them produces a figure with no
referent. **omnitotal revenue** and **omni retail total revenue** are forbidden
questions — not because the answer is hard, but because no answer exists.

If asked, say which single dataset you can answer for and why the others cannot
be combined. Per-source revenue is legitimate; the total is not.

---

## 2. FALSE PREMISES YOU MUST CORRECT, NOT CONFIRM

Each of these has a confident, wrong answer readily available. Confirming the
premise is worse than answering the wrong question, because it makes the error
authoritative.

**"`review_id` is the primary key of `olist.reviews`."**
No. The live key is `(review_id, order_id)`. 789 `review_id` values attach to
more than one order — a fan-out, not a resubmission. Identical text, identical
score, identical *both* timestamps, one person per group, and 32 attachments
where the order was placed *after* the review was written. **Joining reviews to
orders can double-count a review**, so per-order review metrics computed on
`review_id` alone are inflated by 814 rows.

**"What is the primary key of `dunnhumby.coupon_redemptions`?"**
There isn't one. The candidate key `(household_key, day, coupon_upc)` was tested
against the real rows and **rejected — 14 duplicate groups**. No surrogate key
was invented. `SELECT count(*)` is 2,318 ROWS and that is the only honest count;
`count(DISTINCT ...)` over the candidate key would need the duplicates collapsed,
which no verified rule allows.

**"How many dunnhumby customers are there?"**
**801**, not 2,500. `hh_demographic.csv` covers 801 households; 2,500 households
appear in `transaction_data.csv`. This is the publisher's coverage — the file is
byte-identical to the one in their distribution zip, so nothing was truncated in
download. The 1,699 households with no demographics are exactly the ones with a
NULL `classification_3` in `household_rfm`. Any household-level statistic must
say which of the two populations it covers.

**"How many Olist customers are there?"**
96.88% of people place exactly one order. `olist.customers.customer_id` is
unique per order — it is **not** a person. The person is `customer_unique_id`.

**"Which customers are in both Olist and dunnhumby?"**
None can be identified. There is no key, no shared identifier, and no verified
lineage between them. Any query joining `olist.customers` to
`dunnhumby.households` on an integer column is asserting an identity that does
not exist. **Cross-source joins are forbidden** — see §4.

**"How does Olist compare to DataCo on market share?"**
No competitor, rival or market-share data exists in this project. The four
sources are one marketplace (Olist), one supply-chain manufacturer (DataCo), one
retail panel (dunnhumby) and one retailer (Walmart/M5). They sell different
things to different customers in different currencies and are not measured
against a shared market. A percentage-share comparison between any two of them
would divide two unrelated numbers and present the quotient as a finding.
**A cross-source *ratio* is forbidden by the same reasoning that forbids a
cross-source *sum*** — one happens to look plausible, which is what makes it the
more dangerous of the two.

---

## 3. SIGN CONVENTIONS THAT FLIP A RESULT

`dunnhumby` uses `retail_disc` and `coupon_disc`, and **both are negative**.
`total_discount` is **positive** (0–180). A query that subtracts `retail_disc`
twice, or adds it, inverts the discount and produces a plausible wrong number.
Read `docs/data_dictionary.md` before any dunnhumby money query.

M5 has 10 stores (`CA_1..4`, `TX_1..3`, `WI_1..3`), 3,049 series each, 30,490
total. `wday = 1` is **Saturday**. 68% of series-days have zero sales —
intermittent demand, which is why a naive forecast on this data will look
plausible and be wrong.

---

## 4. CROSS-SOURCE JOINS

Forbidden without exception. `analytics` is the only meeting point, and every
row there carries `source_system` so a figure can always be attributed.

This is structurally enforced: `test_cross_source_joins_are_impossible_in_the_data`
asserts that **no foreign key in the database crosses a schema boundary**. So
a cross-source join is legal SQL that will return rows, and only a rule stops it
being quoted as a finding. That rule is this section.

---

## 5. NUMBERS

Every number stated to a user must appear in the rows a query returned in this
same session. Not from memory, not from training data, not from a previous
answer, not from a document that itself needs checking.

The build-time version of this already exists and fails the test suite:
`backend/tests/test_analytics_findings.py` fails if a figure in
`backend/config/sql_findings.yaml` is not present in
`data/processed/_sql_results.json`. The AI layer needs the same check at
**answer** time.

## 6. FORECASTS AND ANOMALIES

Report a forecast only when a model actually ran and the result was actually
evaluated. Report the metric (MAE, MAPE, whatever was used) with it. An
untrained method applied to a test set is not a forecast.

Report an anomaly only when a detection run actually flagged it, and say what
the baseline and threshold were. "Order 123 is unusual" without those is
decoration.

**Both now exist and both are measured.**

Forecasting: five models (naive, 7-day simple moving average, 7-day seasonal
naive, Croston, and the sbF/Teunter-Syntetos-Babai intermittent family),
evaluated on all 30,490 M5 series against a 28-day holdout - train `d_num`
1..1913, test 1914..1941, a window the size of the 28 forecast-horizon days
`m5.calendar` carries past the last observation. `sma_7` wins on MASE at
**1.6717**. `docs/forecasting.md` is generated from
`data/processed/_forecast_results.json`.

The per-band table matters more than the headline: no model is best in all
three intermittency bands, and the overall winner is not the winner of the
band most series fall in.

Anomaly detection: three detectors, one per source, because the three violate
three different assumptions. Olist orders by day use a same-weekday rolling
mean over 28 weeks at |z| >= 3. dunnhumby basket lines use a 30-day rolling
median with 1.4826 x MAD at |z| >= 3. M5 total daily units use a 56-day rolling
mean and standard deviation at |z| >= 4. `docs/anomaly_detection.md` is
generated from `data/processed/_anomaly_results.json`.

**All five flagged M5 days are Christmas Day.** That is a detector working and
a dataset with no incidents in it, and it is the honest answer to "what went
wrong here": nothing this method can see. Reporting "5 anomalies detected"
without that fact would have turned a correct detector into a false-alarm list.

`app/ai/verification.py` enforces both rules at answer time:
`forecast_claim_is_evaluated` and `anomaly_claim_is_detected` refuse a claim
about a model that did not run or a detector that did not fire.

---

## 7. SQL SAFETY

The read-only role is `omniretail_readonly`. PostgreSQL refuses `INSERT`,
`UPDATE`, `DELETE`, `DROP`, `ALTER`, `TRUNCATE`, `CREATE`, `GRANT` and `REVOKE`
for that role — verified by executing all nine against the live database and
recording the refusal in `data/processed/_audit_report.json`.

On top of that, `app/ai/sql_validator.py` refuses the same nine plus `MERGE`,
`COPY`, `CALL` and `DO` before PostgreSQL ever sees the statement, because the
database is the backstop and the validator is the control that **explains**. It
also refuses:

- more than one statement in a submitted string (`SELECT 1; DROP TABLE x`)
- functions with filesystem, network or timing side effects — `pg_read_file`,
  `pg_sleep`, `lo_import`, `dblink`, `nextval`, `set_config` and the rest of
  `FORBIDDEN_FUNCTIONS`. **The read-only role permits all of these.** They are
  legal SELECTs, which is precisely why a validator is not redundant with
  permissions
- a cross-source join, including one written as a comma-separated FROM list
- a query against `analytics` with no `source_system` predicate

There is no endpoint that executes caller-supplied SQL. `/api/ai/validate`
checks a statement and returns a verdict; it never runs it.

---

## 8. REQUIRED CONTROLS — STATUS

These are the controls the AI layer must enforce. Each was specified in
`REQUIRED_CONTROLS` in `backend/tests/test_ai_grounding_contract.py` BEFORE the
AI layer existed, each named the test that would prove it, and the tripwire
asserted that nothing was implemented yet. **All five are now built and
proven.**

| Control | Must reject | Built in | Proved by |
|---|---|---|---|
| `sql_validator` | 13 modifying statements, multi-statement strings, side-effecting functions | `app/ai/sql_validator.py` | `test_validator_rejects_every_forbidden_statement` |
| `result_verification` | a number absent from the executed rows; an answer with no query; a forecast with no evaluated model; an anomaly with no detection run | `app/ai/verification.py` | `test_answer_numbers_all_appear_in_the_query_result` |
| `evidence_requirement` | a business rule with no retrieved document; a KPI with no definition; a column meaning inferred | `app/ai/retrieval.py` + `analyst.py` | `test_unanswerable_question_returns_no_factual_claim` |
| `false_premise_handling` | the premises in §2, plus competitor/market-share data that does not exist | `app/ai/guards.py` | `test_false_premises_are_corrected_not_confirmed` |
| `cross_source_guard` | generated SQL joining two source schemas; `analytics` without a `source_system` predicate | `app/ai/sql_validator.py` + `nl_to_sql.py` | `test_generated_sql_joining_two_sources_is_rejected` |

The proving tests are in `backend/tests/test_ai_controls.py`. That file asserts
its own `PROVING_TESTS` map equals `REQUIRED_CONTROLS`, so a control added
without a test fails the build.

**The tripwire still works, in its new form.** Marking a control `implemented`
without naming a `proving_test` that exists now fails
`test_no_control_is_marked_implemented_without_proving_it`, so a control can
still not be quietly retired or quietly claimed.

### The natural-language path is retrieval, not generation

`app/ai/nl_to_sql.py` does not write SQL. It ranks the 41 hand-written,
verified statements in `backend/sql/` and returns one **verbatim**. It never
assembles a statement, so there is no path by which a novel query reaches the
database.

A question outside the catalogue is refused with the nearest matches rather than
approximated. That is the deliberate trade: an unimprovised query returns rows,
and rows get quoted as answers. The cost is that some genuine questions get
refused; the benefit is that no wrong query is ever run.

---

## 9. WHEN YOU DO NOT KNOW

Say so. "The dataset does not cover that" is a complete, correct answer and
frequently the truthful one — the 801-household coverage gap and the keyless
`coupon_redemptions` table are both cases where the correct answer is that the
question cannot be answered as asked.

Absence of evidence ends the answer. It does not invite a guess.