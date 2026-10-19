"""The analytics runner must not destroy recorded results when narrowing a run.

THE BUG THIS PINS
-----------------
`run_analytics.py` rebuilds `_sql_results.json` from scratch on a full run, so a
renamed or deleted query leaves no stale entry behind. That is correct. But the
merge-into-previous branch was gated on `args.source` alone.

`--only <name>` also narrows the run - to one query out of 41 - and it did NOT
take the merge branch. Running one query to check it still worked silently
rewrote the evidence file down to that single query.

That is not a cosmetic problem. `_sql_results.json` is the record every
documented figure in `docs/sql_analytics.md` is validated against. Truncating it
makes the documentation tests fail in a way that looks like data corruption, and
it destroys the only record of what the other 40 queries actually returned.

Found by doing it, which is the argument for testing it: a check that only runs
on the full-corpus path cannot catch a bug on the scoped path.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from conftest import REPO_ROOT

RUNNER = REPO_ROOT / "backend" / "scripts" / "sql" / "run_analytics.py"
RESULTS = REPO_ROOT / "data" / "processed" / "_sql_results.json"


def _runner_tree() -> ast.Module:
    return ast.parse(RUNNER.read_text(encoding="utf-8"), filename=str(RUNNER))


def _merge_condition() -> str | None:
    """The expression that decides full-rebuild vs merge-into-previous.

    Found by the `merged = ...` assignment it guards, then followed back to the
    definition of whatever name it tests. Following the variable rather than
    hard-coding `args.source` is the point: if someone renames the flag binding,
    this test must keep working, and if they drop a flag from the condition the
    test must fail.
    """
    tree = _runner_tree()

    condition_name: str | None = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = ast.unparse(node.test)
        body = ast.unparse(node.body)
        if "merged = {" in body and isinstance(node.test, ast.Name):
            condition_name = node.test.id
            break
    if condition_name is None:
        return None

    # resolve `narrowed = <expr>` wherever it is assigned
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == condition_name for t in node.targets
        ):
            return ast.unparse(node.value)
    return None


def test_the_runner_exists():
    assert RUNNER.exists(), f"{RUNNER} is missing"


def test_a_narrowed_run_merges_instead_of_rebuilding():
    """`--source` and `--only` must BOTH preserve the other queries' results.

    Reads the real condition out of the source rather than restating it, so the
    test fails if someone narrows the condition back to one flag.
    """
    condition = _merge_condition()
    assert condition is not None, (
        "could not find the full-rebuild/merge branch in run_analytics.py; "
        "if it was restructured, re-check which runs preserve prior results"
    )
    assert "args.source" in condition, f"merge condition is {condition!r}"
    assert "args.only" in condition, (
        f"merge condition is {condition!r}. A --only run executes one query of 41 "
        f"but would rebuild the file, erasing the other 40."
    )
    assert " or " in f" {condition} ", (
        f"merge condition {condition!r} has no 'or', so either flag alone triggers "
        f"a merge and a full run cannot be distinguished"
    )


def test_the_runner_has_a_narrowing_flag_for_each_dimension():
    """Both dimensions of narrowing must exist and be reachable.

    Guards against the inverse mistake: 'fixing' the bug by removing --only
    rather than by making it merge.
    """
    tree = _runner_tree()
    flags: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument"
        ):
            for argument in node.args:
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                    flags.add(argument.value)
    assert "--source" in flags, f"runner flags are {sorted(flags)}"
    assert "--only" in flags, f"runner flags are {sorted(flags)}"


def test_results_file_holds_the_whole_corpus():
    """The evidence file on disk must contain every query, not a subset.

    This is the state the bug above produced (query_count == 1), so the
    assertion is deliberately about the FILE rather than about a fresh run -
    it catches damage done by any previous scoped run, including one performed
    by hand.
    """
    assert RESULTS.exists(), f"{RESULTS} is missing"
    results = json.loads(RESULTS.read_text(encoding="utf-8"))

    count = results["query_count"]
    assert count == len(results["queries"]), (
        f"query_count says {count} but {len(results['queries'])} are recorded"
    )
    assert count >= 41, (
        f"_sql_results.json holds {count} query result(s). A full run records 41. "
        f"A narrowed run must merge, not truncate this file."
    )
    assert results["failure_count"] == 0, (
        f"{results['failure_count']} query/queries are recorded as failed"
    )


@pytest.mark.parametrize(
    "source,expected",
    [("olist", 13), ("dataco", 9), ("dunnhumby", 11), ("m5", 8)],
)
def test_every_source_is_fully_represented(source, expected):
    """All four sources present, with their documented query counts.

    A truncated file usually loses the LAST source processed rather than
    scattering holes, so a whole-source check is the one that catches it.
    """
    results = json.loads(RESULTS.read_text(encoding="utf-8"))
    found = [q for q in results["queries"] if q["source"] == source]
    assert len(found) == expected, (
        f"{source} has {len(found)} recorded result(s), expected {expected}"
    )
    assert not any("error" in q for q in found), (
        f"{source} has a recorded failure: "
        + "; ".join(q["name"] for q in found if "error" in q)
    )


def test_each_recorded_result_carries_its_rows():
    """A recorded result with no rows cannot support a documented figure.

    The merge path writes whatever the previous file held, so a corrupted entry
    would persist across runs instead of being overwritten.
    """
    results = json.loads(RESULTS.read_text(encoding="utf-8"))
    empty = [
        (q["source"], q["name"], q.get("row_count"))
        for q in results["queries"]
        if q.get("row_count", 0) == 0 and "error" not in q
    ]
    assert not empty, f"these queries recorded zero rows: {empty}"


def test_query_names_are_unique():
    """The merge is keyed by name, so a duplicate would silently drop a query."""
    results = json.loads(RESULTS.read_text(encoding="utf-8"))
    names = [q["name"] for q in results["queries"]]
    duplicates = {n for n in names if names.count(n) > 1}
    assert not duplicates, f"duplicate query names in the results file: {duplicates}"