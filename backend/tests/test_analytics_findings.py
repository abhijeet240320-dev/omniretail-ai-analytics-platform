"""Regression tests: does the documentation still describe the data?

`docs/sql_analytics.md` is generated from two inputs - the executed query
results and a hand-written interpretation in `sql_findings.yaml`. Either one can
go stale, and a stale document is worse than no document because it looks
authoritative.

These tests assert the invariant that holds the two together: every figure
quoted in the findings prose must be present in the result rows of the query it
belongs to. The same check runs twice, against the recorded results (fast, no
database) and against a fresh execution of every query (slow, catches the data
changing underneath the documentation).
"""

from __future__ import annotations

import pytest


def keys_of(results: list[dict]) -> set[str]:
    return {f"{q['source']}.{q['name']}" for q in results}


@pytest.mark.slow
def test_every_query_executed_successfully(live_results):
    """No analytics query may fail. A failed query is excluded from the docs."""
    failed = [f"{q['source']}.{q['name']}: {q.get('error')}" for q in live_results
              if "error" in q]
    assert not failed, "analytics queries failed:\n  " + "\n  ".join(failed)


def test_every_query_is_documented(recorded_results, findings):
    """A query with no written finding would appear in the docs unexplained."""
    undocumented = sorted(keys_of(recorded_results) - set(findings))
    assert not undocumented, (
        "these queries ran but have no entry in sql_findings.yaml:\n  "
        + "\n  ".join(undocumented)
    )


def test_findings_reference_real_queries(recorded_results, findings):
    """A finding for a query that no longer exists is silently ignored, so catch it."""
    orphans = sorted(set(findings) - keys_of(recorded_results))
    assert not orphans, (
        "sql_findings.yaml documents queries that do not exist:\n  "
        + "\n  ".join(orphans)
    )


def test_documented_figures_match_recorded_results(generator, findings, recorded_results):
    """The fast guard: every quoted figure is in the recorded result rows.

    No database needed. This is what catches an edited sentence whose number
    stopped being true.
    """
    problems = generator.check_findings(recorded_results, findings)
    assert not problems, (
        f"{len(problems)} documented figure(s) are not in the query results:\n  "
        + "\n  ".join(problems)
    )


@pytest.mark.slow
def test_documented_figures_match_live_database(
    generator, findings, live_results
):
    """The slow guard: re-execute everything, then re-check every figure.

    This is the test that catches the database changing under the
    documentation, which the fast test cannot see because it reads a file.
    """
    problems = generator.check_findings(live_results, findings)
    assert not problems, (
        f"{len(problems)} documented figure(s) no longer match a live query:\n  "
        + "\n  ".join(problems)
    )


@pytest.mark.slow
def test_query_corpus_is_fully_covered(live_results):
    """The corpus must not have silently shrunk.

    Counts are asserted as exact numbers on purpose: if somebody deletes a
    query, that is a decision someone should make out loud, not something a
    passing test suite should absorb.
    """
    per_source: dict[str, int] = {}
    for query in live_results:
        per_source[query["source"]] = per_source.get(query["source"], 0) + 1
    assert per_source == {"olist": 13, "dataco": 9, "dunnhumby": 11, "m5": 8}, (
        "the number of analytics queries per source changed; update this test "
        "and the documentation deliberately, do not let it drift"
    )


@pytest.mark.slow
def test_generated_document_is_up_to_date(generator, live_results, findings):
    """docs/sql_analytics.md must equal what the generator produces right now.

    The live results are put through the SAME JSON round-trip the runner uses
    before the comparison. psycopg hands back Decimal and datetime objects, and
    `json.dumps(default=str)` is what turns them into the strings that end up in
    the document. Comparing raw live values against a document that was written
    from serialised ones reports differences that are pure formatting - which is
    how this test failed the first time, for no real reason.

    Per-query TIMINGS are normalised away. The document prints how long each
    query took, and a fresh execution will never match the recorded one to the
    millisecond, so asserting on them would fail every single run while
    detecting nothing. What matters is that the rows, columns and prose match.
    """
    import json
    import re

    def normalise(text: str) -> str:
        return re.sub(r"[\d,]+ ms\._", "TIME ms._", text.rstrip("\n"))

    serialised = json.loads(json.dumps(live_results, default=str))
    produced = normalise("\n".join(generator.build(serialised, findings)))
    current = normalise(generator.DESTINATION.read_text(encoding="utf-8"))
    assert produced == current, (
        "docs/sql_analytics.md is stale - regenerate with:\n"
        "  .venv/Scripts/python.exe backend/scripts/docs/generate_sql_analytics.py"
    )


@pytest.mark.slow
def test_spot_check_a_known_finding(live_results):
    """One hard-coded figure, as a tripwire independent of the generator.

    The generator's own check could in principle be weakened by a change to its
    tokenizer. This asserts a specific, well-known number end to end: Olist
    review coverage. If that ever stops being 99.23%, something real changed.

    Values are compared through str() because psycopg returns Decimal for
    NUMERIC and int for BIGINT, while the committed JSON holds the strings that
    json.dumps(default=str) produced. The number is what matters, not its type.
    """
    review = next(
        (q for q in live_results if q["name"] == "review_coverage_and_score"), None
    )
    assert review is not None, "review_coverage_and_score did not run"
    values = dict(zip(review["columns"], review["rows"][0]))
    assert str(values["pct_reviewed"]) == "99.23"
    assert str(values["orders_total"]) == "99441"