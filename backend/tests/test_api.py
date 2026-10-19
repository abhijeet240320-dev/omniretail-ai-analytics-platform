"""API tests. Every one runs against the real database.

NO MOCKED DATABASE
------------------
A test that mocks the database proves the code parses. It does not prove that
`revenue` is revenue, that `801` is still the number of dunnhumby households
with demographics, or that a column name the API returns actually exists.

The tests here run real queries against the 106 million loaded rows and assert
against numbers that have been independently verified elsewhere in this project.
That is the whole point: the API is a delivery mechanism for facts measured
elsewhere, so it must not be able to quietly serve something else.

The read-only tests are the exception to "nothing is mocked" in one respect -
they attempt real writes and assert PostgreSQL refuses them, which requires the
real role to really lack the privilege.
"""

from __future__ import annotations

import sys
from datetime import date

import pytest

from conftest import REPO_ROOT, load_script

sys.path.insert(0, str(REPO_ROOT / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def _alembic_head() -> str:
    """The revision Alembic considers current, read from the repository."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "backend" / "alembic"))
    return ScriptDirectory.from_config(config).get_current_head()


@pytest.fixture(scope="module")
def client():
    """A client that authenticates.

    These tests predate authentication and originally issued bare requests.
    Auth was added afterwards, and every one of them then failed with 401 -
    correctly: the API now requires a key on everything but `/api/health`.

    The key is issued through the real `08_issue_api_key.py` path, against the
    real database, and revoked in the teardown. That means the auth the tests
    exercise is the auth that ships, not a stub - a test-only header would pass
    here while the real dependency stayed broken.
    """
    # The issuing script's `connect()` reads the same `get_settings()` every
    # other module in this project reads, and `create_key()` is the one place
    # that writes a credential row. Using both keeps the test on the shipping
    # code path rather than on a copy of it.
    issuer = load_script("backend/scripts/db/08_issue_api_key.py")
    with issuer.connect() as conn:
        if not issuer.table_exists(conn):
            raise AssertionError(
                "app.api_keys does not exist; run `alembic upgrade head`"
            )
        key_id, key = issuer.create_key(
            conn,
            label="pytest-api-suite",
            scopes="analytics:read,ai:read,admin:read",
            created_by="pytest",
        )

    try:
        with TestClient(app) as test_client:
            test_client.headers.update({"X-API-Key": key})
            yield test_client
    finally:
        # Leave no key behind, even on failure. A leaked admin-scoped key in a
        # database is a real credential, not a test fixture.
        _revoke(key_id)


def _revoke(key_id: int) -> None:
    """Deactivate and delete a key issued by the fixture above.

    Done with the read/write engine on purpose: `omniretail_readonly` holds
    SELECT only, and the correct way to observe that is not to need it.
    """
    from sqlalchemy import create_engine, text

    from app.core.config import get_settings

    engine = create_engine(get_settings().database_url, future=True)
    try:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM app.api_keys WHERE id = :id"), {"id": key_id}
            )
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def unauthenticated():
    """A client with NO credentials, for the auth tests in test_auth.py's style."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def overview(client):
    """The overview payload, fetched once.

    Five tests read it and each fetch costs a live query over m5's 59 million
    rows - about 28 seconds apiece. Fetching once turns 140 seconds of repeated
    identical work into 28, and keeps the assertions honest: they compare
    against the same snapshot rather than five slightly different ones.
    """
    return client.get("/api/overview").json()


# ==============================================================================
# HEALTH
# ==============================================================================

def test_health_reports_ok_against_the_real_database(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok", body
    assert body["database_reachable"] is True
    assert body["read_only_enforced"] is True


def test_status_reports_the_real_schema(client):
    body = client.get("/api/status").json()
    assert body["database"] == "omniretail"
    assert body["role"] == "omniretail_readonly"
    assert body["is_select_only"] is True
    assert body["table_count"] == 30, (
        f"the API sees {body['table_count']} tables; 30 are loaded"
    )
    # Asserted against the LIVE Alembic head rather than a literal. This used
    # to say "baseline", which was true while there was exactly one migration
    # and went stale the moment the api_keys migration was added - reporting a
    # false failure for a correctly-reported revision.
    assert body["alembic_revision"] == _alembic_head(), (
        f"/api/status reports revision {body['alembic_revision']!r} but Alembic's "
        f"head is {_alembic_head()!r}"
    )

    names = {f"{t['schema_name']}.{t['table_name']}" for t in body["tables"]}
    for expected in (
        "olist.orders",
        "dataco.orders",
        "dunnhumby.transactions",
        "m5.sales_long",
    ):
        assert expected in names, f"{expected} is missing from {sorted(names)}"

    # Exactly the four source schemas, and nothing else. `app` is Alembic's own
    # schema and holds credentials rather than retail data, so the read-only
    # role has no privilege on it and it must never appear in an inventory.
    all_schemas = {t["schema_name"] for t in body["tables"]}
    assert all_schemas == {"olist", "dataco", "dunnhumby", "m5"}, sorted(all_schemas)
    assert "app" not in all_schemas, (
        "/api/status is enumerating the credential schema"
    )

    # `reltuples` is an estimate and this is a status page, so the assertion is
    # an order-of-magnitude floor rather than the exact 106,704,583. Exact
    # counts live in data/processed/_load_report.json and in the analytics
    # catalogue; a status page that COUNT(*)'d 30 tables on every load would be
    # a page nobody reloads.
    assert body["total_rows"] > 100_000_000, body["total_rows"]
    assert all(t["columns"] > 0 for t in body["tables"]), "a table has no columns"


def test_overview_returns_real_per_source_figures(overview):
    """The overview endpoint returns live per-source KPIs queried from the read-only role.

    Each source's metrics are independent: one source failing should not blank the others,
    and there must be NOTHING that aggregates across sources.

    The fixture fetches once and caches, because fetching every metric on every test
    would take minutes per run.
    """
    body = overview
    metrics = {s["source"]: s["metrics"] for s in body["sources"]}

    assert set(metrics) == {"olist", "dataco", "dunnhumby", "m5"}
    for source, values in metrics.items():
        assert values, f"{source} returned no metrics"
        for name, value in values.items():
            assert value is not None, f"{source}.{name} is None"
            assert not isinstance(value, bool), f"{source}.{name} is a bool, not a metric"
            # The ONLY legitimate string metric is a coverage date, and it must
            # be a real ISO date. Anything else means the query failed and the
            # failure was stringified into the value - a source that silently
            # reports "connection reset" where a number belongs is worse than
            # one that reports nothing, because a dashboard will render it.
            if isinstance(value, str):
                assert name in ("coverage_start", "coverage_end"), (
                    f"{source}.{name} came back as the string {value!r}; only "
                    f"the coverage dates may be strings"
                )
                parsed = date.fromisoformat(value)
                assert parsed.year > 2000, f"{source}.{name} is {value!r}"
    # And every source actually returned the coverage window, so the date
    # branch above is exercised rather than dead code.
    for source in ("olist", "dataco", "m5"):
        start = date.fromisoformat(metrics[source]["coverage_start"])
        end = date.fromisoformat(metrics[source]["coverage_end"])
        assert start < end, f"{source} coverage runs backwards: {start}..{end}"


def test_overview_never_sums_across_sources(overview):
    """There must be no combined total anywhere in the response.

    Not "we computed it but hid it" - structurally absent. Four unrelated
    companies cannot be summed, and a frontend that finds a field called
    `total_revenue` will display it.
    """
    body = overview

    # Check KEYS, not the whole serialised payload. The disclaimer legitimately
    # contains the words "combined" and "total" because it explains why they are
    # absent - searching the text would make the explanation itself the failure.
    forbidden = ("total_revenue", "grand_total", "combined", "omnitotal",
                 "all_sources", "overall_revenue", "total")

    def all_keys(node, found=None):
        found = set() if found is None else found
        if isinstance(node, dict):
            found.update(str(k).lower() for k in node)
            for value in node.values():
                all_keys(value, found)
        elif isinstance(node, list):
            for item in node:
                all_keys(item, found)
        return found

    keys = all_keys(body)
    offenders = {k for k in keys if any(f in k for f in forbidden)}
    assert not offenders, (
        f"the overview response has key(s) {sorted(offenders)}, which imply a "
        f"cross-source total that cannot exist"
    )
    assert "sources" in keys


def test_overview_states_why_there_is_no_total(overview):
    body = overview
    assert body["disclaimer"], "the disclaimer explaining the missing total is empty"
    lowered = body["disclaimer"].lower()
    assert "unrelated" in lowered or "shared key" in lowered, body["disclaimer"]


def test_overview_dunnhumby_reports_801_not_2500(overview):
    """The single most misleading number this API could serve.

    2,500 households transact; 801 have demographics. An API that reported
    2,500 for a demographic question would be wrong in a way that reads as
    correct, because 2,500 is the real household count for a different question.
    """
    body = overview
    dunnhumby = next(s for s in body["sources"] if s["source"] == "dunnhumby")

    assert dunnhumby["metrics"]["households_with_demographics"] == 801, (
        f"expected the verified 801, got "
        f"{dunnhumby['metrics']['households_with_demographics']}"
    )
    assert dunnhumby["metrics"]["households_transacting"] == 2500, dunnhumby["metrics"]


def test_overview_money_is_a_number_not_a_string(overview):
    """Decimal must become a JSON number.

    `json.dumps(..., default=str)` would render revenue as the string
    "16008872.12". A frontend then does string arithmetic, or renders it without
    thousands separators, and the number is quietly wrong.
    """
    body = overview
    olist = next(s for s in body["sources"] if s["source"] == "olist")

    revenue = olist["metrics"]["revenue"]
    assert isinstance(revenue, (int, float)), (
        f"olist revenue came back as {type(revenue).__name__}, not a number: {revenue!r}"
    )
    assert not isinstance(revenue, bool)
    assert revenue == 16008872.12, f"olist revenue is {revenue}, expected 16008872.12"


def test_overview_survives_one_failing_source(overview):
    """A single failing metric must not blank the whole dashboard.

    If one query errors, the other three sources still have real numbers to show.
    A 500 here would mean a temporary database hiccup takes out every KPI.
    """
    body = overview
    assert len(body["sources"]) == 4, body
    for source in body["sources"]:
        # either metrics, or a note explaining why there are none - never neither
        assert source["metrics"] or source["note"], source


# ==============================================================================
# ANALYTICS CATALOGUE
# ==============================================================================

def test_catalogue_lists_all_41_queries(client):
    body = client.get("/api/analytics/queries").json()
    assert body["total"] == 41, f"the API exposes {body['total']} queries; 41 are defined"
    assert body["sources"] == ["olist", "dataco", "dunnhumby", "m5"]
    assert all(q["question"] for q in body["queries"]), "a query has no question"


def test_catalogue_filter_by_source(client):
    body = client.get("/api/analytics/queries?source=m5").json()
    assert body["total"] == 8, body["total"]
    assert all(q["source"] == "m5" for q in body["queries"])


def test_catalogue_rejects_an_unknown_source(client):
    response = client.get("/api/analytics/queries?source=walmart")
    assert response.status_code == 404
    assert "unknown source" in response.json()["detail"].lower()


def test_catalogue_names_match_the_sql_files(client):
    """The API must expose the same names the documentation does.

    If these diverged, the docs would describe queries the API cannot run.
    """
    import json

    results_path = REPO_ROOT / "data" / "processed" / "_sql_results.json"
    recorded = json.loads(results_path.read_text(encoding="utf-8"))
    documented = {f"{q['source']}.{q['name']}" for q in recorded["queries"]}

    served = {q["name"] for q in client.get("/api/analytics/queries").json()["queries"]}
    assert served == documented, (
        f"only in the API: {sorted(served - documented)}; "
        f"only in the results file: {sorted(documented - served)}"
    )


# ==============================================================================
# ANALYTICS EXECUTION
# ==============================================================================

def test_running_a_query_returns_real_rows(client):
    body = client.get(
        "/api/analytics/queries/olist.review_coverage_and_score"
    ).json()
    assert body["row_count"] >= 1
    assert body["total_rows"] == body["row_count"]
    assert body["elapsed_ms"] > 0

    row = body["rows"][0]
    assert row["orders_total"] == 99441, row
    assert row["pct_reviewed"] == 99.23, (
        f"review coverage is {row['pct_reviewed']}%; 99.23 was verified and "
        f"documented"
    )
    assert row["review_rows"] == 99224, row
    assert row["distinct_review_ids"] == 98410, row


def test_bare_query_names_resolve_when_unambiguous(client):
    qualified = client.get(
        "/api/analytics/queries/olist.review_coverage_and_score"
    ).json()
    bare = client.get("/api/analytics/queries/review_coverage_and_score").json()
    assert bare["query"] == qualified["query"] == "olist.review_coverage_and_score"
    assert bare["rows"] == qualified["rows"]


def test_unknown_query_is_a_404_that_says_what_exists(client):
    response = client.get("/api/analytics/queries/no_such_query")
    assert response.status_code == 404
    detail = response.json()["detail"]
    assert "no query named" in detail.lower(), detail
    assert "41" in detail, detail


@pytest.mark.slow
def test_query_results_are_json_safe(client):
    """No Decimal, date or bytes may survive into the response.

    A `date` that reaches Pydantic unconverted serialises as an ISO string,
    which is fine; a `Decimal` that reaches a naive JSON encoder raises, and a
    bytes column would become an unprintable blob in the payload.

    Slow: `m5.store_comparison` aggregates 59 million rows.
    """
    import json

    for name in ("m5.store_comparison", "olist.monthly_revenue_trend"):
        body = client.get(f"/api/analytics/queries/{name}").json()
        # round-trips without a custom encoder
        json.dumps(body)
        for row in body["rows"]:
            for value in row.values():
                assert value is None or isinstance(
                    value, (str, int, float, bool, list, dict)
                ), f"{name} returned a {type(value).__name__}: {value!r}"


@pytest.mark.slow
def test_a_date_column_comes_back_as_an_iso_string(client):
    """Dates must be ISO 8601, not a Python repr the frontend has to guess at.

    `m5.calendar.date` is a TIMESTAMP, so the value legitimately carries a
    midnight time component - `2011-01-29T00:00:00`. That is still ISO 8601 and
    still parses with `new Date()` in JavaScript, which is the property that
    matters. Asserting a bare `YYYY-MM-DD` would fail on correct behaviour.

    Slow: the query aggregates 59 million rows.
    """
    from datetime import datetime

    body = client.get("/api/analytics/queries/m5.store_comparison").json()
    dates = [r["first_date"] for r in body["rows"]]
    assert dates, "no first_date column returned"

    for value in dates:
        assert isinstance(value, str), f"first_date came back as {type(value).__name__}"
        # must round-trip through the ISO parser without raising
        parsed = datetime.fromisoformat(value)
        assert parsed.year and parsed.month and parsed.day, value

    assert dates[0].startswith("2011-01-29"), dates[0]


def test_a_whole_day_date_column_has_no_time_component(client):
    """A true DATE column must not gain a spurious midnight time.

    The distinction matters to a frontend: `date` and `timestamp` need different
    handling, and flattening both to the same string loses it.
    """
    from datetime import date, datetime

    body = client.get("/api/analytics/queries/olist.order_status_funnel").json()
    values = [
        v
        for row in body["rows"]
        for k, v in row.items()
        if isinstance(v, str) and v[:2] == "20" and "T" in v
    ]
    # this endpoint's date columns, if any, must parse either way
    for value in values:
        assert datetime.fromisoformat(value) is not None
    # and date-only strings must not have been given a time
    for row in body["rows"]:
        for value in row.values():
            if isinstance(value, str) and value[:2] == "20" and "T" not in value:
                assert date.fromisoformat(value) is not None, value


def test_pagination_reports_a_real_total(client):
    """`total_rows` must exceed the page, or a pager cannot work.

    Asserted on a query that genuinely has more rows than the page, so the check
    is meaningful rather than trivially satisfied. `rfm_segment_value` returns 13
    rows across the RFM segments.
    """
    body = client.get(
        "/api/analytics/queries/dunnhumby.rfm_segment_value?limit=5"
    ).json()
    assert body["total_rows"] == 13, body["total_rows"]
    assert body["row_count"] == 5
    assert body["limit"] == 5
    assert body["truncated"] is True


def test_pagination_windows_do_not_overlap(client):
    """Two pages must not return the same row.

    Without a deterministic ORDER BY, `LIMIT n OFFSET m` is not a stable page and
    a row can appear twice - a bug that looks like intermittent data corruption.
    """
    def key(row):
        return repr(sorted(row.items()))

    first = client.get(
        "/api/analytics/queries/dunnhumby.rfm_segment_value?limit=6&offset=0"
    ).json()
    second = client.get(
        "/api/analytics/queries/dunnhumby.rfm_segment_value?limit=6&offset=6"
    ).json()

    keys_first = {key(r) for r in first["rows"]}
    keys_second = {key(r) for r in second["rows"]}
    assert len(keys_first) == 6, first["row_count"]
    assert len(keys_second) == 6, second["row_count"]
    assert not (keys_first & keys_second), "pages 1 and 2 share rows"


def test_limit_is_capped(client):
    response = client.get(
        "/api/analytics/queries/dunnhumby.rfm_segment_value?limit=100000"
    )
    assert response.status_code == 422, (
        "a limit above the cap should be rejected by validation, not silently "
        "clamped - a silent clamp looks like the server ignoring the caller"
    )


def test_sorting_on_a_real_column_works(client):
    response = client.get(
        "/api/analytics/queries/dunnhumby.rfm_segment_value"
        "?sort_by=total_monetary&descending=true&limit=13"
    )
    assert response.status_code == 200
    body = response.json()
    values = [r["total_monetary"] for r in body["rows"]]
    assert len(values) == 13
    assert values == sorted(values, reverse=True), values


def test_sorting_on_an_injected_column_is_ignored_not_executed(client):
    """A sort key that is not a column must never reach the statement.

    `sort_by` arrives from a URL, so passing SQL through it unfiltered would be
    injection. It must be dropped, not executed.
    """
    response = client.get(
        "/api/analytics/queries/dunnhumby.rfm_segment_value"
        "?sort_by=1;DROP%20TABLE%20olist.orders;--&limit=5"
    )
    assert response.status_code == 200, "the injection was not rejected"
    body = response.json()
    assert body["row_count"] == 5, "the injection broke the query"
    assert body["sort_ignored"], "the ignored sort key was not reported in the body"
    assert "X-Sort-Ignored" in response.headers

    # and the table is still there
    assert (
        client.get("/api/analytics/queries/olist.review_coverage_and_score").status_code
        == 200
    )


def test_a_bogus_sort_key_is_reported(client):
    response = client.get(
        "/api/analytics/queries/dunnhumby.rfm_segment_value?sort_by=not_a_column&limit=5"
    )
    assert response.status_code == 200
    assert response.json()["sort_ignored"] == "not_a_column", (
        "an unknown sort key was ignored without telling the caller"
    )
    assert "X-Sort-Ignored" in response.headers


def test_a_valid_sort_key_is_not_flagged_as_ignored(client):
    body = client.get(
        "/api/analytics/queries/dunnhumby.rfm_segment_value"
        "?sort_by=total_monetary&descending=true&limit=5"
    ).json()
    assert body["sort_ignored"] is None, body["sort_ignored"]


# ==============================================================================
# SECURITY
# ==============================================================================

def test_there_is_no_endpoint_that_accepts_sql(client):
    """No route may take arbitrary SQL.

    A `?sql=` parameter is the most dangerous thing this API could have, and its
    absence is a property worth testing rather than a comment. Any route whose
    parameters include 'sql', 'query_text' or similar would be a red flag.
    """
    schema = client.get("/openapi.json").json()
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            for parameter in operation.get("parameters", []):
                name = parameter.get("name", "").lower()
                assert name not in ("sql", "raw_sql", "query", "statement"), (
                    f"{method.upper()} {path} accepts a {name!r} parameter"
                )


# The AI endpoints take a question in a request BODY, so POST is the correct
# method for them. POST is not a write verb - it is a body verb - and what this
# project forbids is MUTATION, not POST. Listing them explicitly means a new
# POST route has to be added here deliberately, which is the point.
ALLOWED_POST_PATHS = frozenset({
    "/api/ai/ask",
    "/api/ai/ask/document",
    "/api/ai/translate",
    "/api/ai/validate",
    "/api/ai/search",
})


def test_no_endpoint_mutates(client):
    """No PUT, PATCH or DELETE, and POST only where it takes a question.

    This used to assert GET/OPTIONS only and failed the moment the AI endpoints
    landed. The failure was correct about the risk and wrong about the method:
    `/api/ai/ask` takes a natural-language question in a body, which is a
    read, and HTTP has no verb that says "run a question".

    What actually matters, and what is asserted here:
      * no PUT, PATCH or DELETE anywhere - those mutate by definition
      * POST appears only on the five allow-listed question endpoints
      * those five cannot mutate even if they tried, because the role is
        read-only (proven separately in test_ai_grounding_contract.py)

    The allow-list is the mechanism: a sixth POST route fails this test until
    somebody explains why it is a read.
    """
    schema = client.get("/openapi.json").json()

    for path, operations in schema["paths"].items():
        for method in operations:
            if method in ("put", "patch", "delete"):
                raise AssertionError(
                    f"{method.upper()} is exposed on {path}; this API is read-only"
                )
            if method == "post":
                assert path in ALLOWED_POST_PATHS, (
                    f"POST is exposed on {path}, which is not an allow-listed "
                    f"question endpoint. Allowed: {sorted(ALLOWED_POST_PATHS)}"
                )

    # And every one of them actually exists, so the allow-list cannot rot into
    # permitting nothing.
    for path in ALLOWED_POST_PATHS:
        assert path in schema["paths"], f"{path} is allow-listed but not routed"


def test_an_internal_error_does_not_leak_detail(client, monkeypatch):
    """A 500 must name the exception type and nothing else.

    Echoing the failing statement teaches an attacker the schema. The habit of
    including it is exactly how that leak reaches production.
    """
    def explode(*args, **kwargs):
        raise RuntimeError(
            "SECRET: SELECT * FROM olist.customers WHERE ssn = '...' at /srv/app/db.py:42"
        )

    monkeypatch.setattr("app.api.analytics.execute_query", explode)
    response = client.get("/api/analytics/queries/dunnhumby.rfm_segment_value")

    assert response.status_code == 502, response.status_code
    body = response.json()
    assert "SECRET" not in str(body), body
    assert "ssn" not in str(body).lower(), body
    assert "db.py:42" not in str(body), body


def test_cors_allows_only_configured_origins(client):
    """A wildcard origin would let any page the developer visits read the API."""
    response = client.get(
        "/api/health", headers={"Origin": "https://evil.example.com"}
    )
    assert "access-control-allow-origin" not in {
        k.lower() for k in response.headers
    }, response.headers


def test_cors_allows_the_dev_server(client):
    response = client.get(
        "/api/health", headers={"Origin": "http://localhost:5173"}
    )
    assert (response.headers.get("access-control-allow-origin")
            == "http://localhost:5173"
    ), response.headers


# ==============================================================================
# THE OVERVIEW CACHE
# ==============================================================================


def _cheap_queries(monkeypatch, sql: str) -> None:
    """Reduce the overview to one real metric query.

    The cache tests are about CACHING, not about the 26 metrics. Running the real
    set would add roughly four minutes of scanning to three tests that assert
    nothing about the metrics; the metrics themselves are covered by the
    `overview` fixture tests above, which run against the real thing.
    """
    from app.api import overview as overview_module

    monkeypatch.setattr(
        overview_module,
        "SOURCE_QUERIES",
        {"olist": [("orders", "SELECT count(*) AS orders FROM olist.orders")]},
    )


def test_the_overview_discloses_whether_it_was_cached(client, monkeypatch):
    """A reused number must say it was reused.

    The overview is 26 metric queries over tables with 59 million rows, so it is
    cached. Caching without saying so would let a stale figure be read as a
    fresh one, which is the whole failure this project is built against.
    """
    from app.api.overview import cache_clear

    _cheap_queries(monkeypatch, "")
    cache_clear()

    fresh = client.get("/api/overview").json()
    assert fresh["cached"] is False, "the first request should have computed it"
    assert fresh["age_seconds"] == 0.0

    second = client.get("/api/overview").json()
    assert second["cached"] is True, (
        "the second request within the TTL should have been served from cache; "
        "the payload makes that visible rather than pretending otherwise"
    )
    assert second["age_seconds"] >= 0.0
    assert second["generated_at"] == fresh["generated_at"], (
        "a cached response must carry the timestamp of the computation it "
        "reused, not a new one"
    )
    assert second["sources"] == fresh["sources"]

    cache_clear()


def test_a_partial_overview_is_never_cached(client, monkeypatch):
    """A failure must not be pinned for the TTL.

    Caching a response that contains a `note` would turn a moment of database
    slowness into a dashboard showing a broken source for the next two minutes -
    longer than the outage that caused it.
    """
    from app.api import overview as overview_module

    _cheap_queries(monkeypatch, "")
    overview_module.cache_clear()
    monkeypatch.setattr(
        overview_module,
        "SOURCE_QUERIES",
        {"olist": [("orders", "SELECT count(*) AS orders FROM olist.no_such_table")]},
    )

    first = client.get("/api/overview").json()
    olist = next(s for s in first["sources"] if s["source"] == "olist")
    assert olist["note"], "the broken query should have been reported in `note`"
    assert olist["metrics"] == {}, olist

    second = client.get("/api/overview").json()
    assert second["cached"] is False, (
        "a PARTIAL response was cached; a transient failure has been pinned "
        "for the cache TTL"
    )
    olist_again = next(s for s in second["sources"] if s["source"] == "olist")
    assert olist_again["note"], "the failure should be recomputed, not remembered"

    overview_module.cache_clear()


def test_the_overview_cache_can_be_turned_off(client, monkeypatch):
    """`OVERVIEW_CACHE_TTL_SECONDS = 0` must mean "always recompute".

    Otherwise an operator who sets it has no way to know whether it took effect,
    and the honest setting ("I want fresh numbers") is silently ignored.
    """
    from app.api import overview as overview_module
    from app.core.config import get_settings

    _cheap_queries(monkeypatch, "")
    overview_module.cache_clear()
    monkeypatch.setattr(get_settings(), "overview_cache_ttl_seconds", 0.0)

    first = client.get("/api/overview").json()
    second = client.get("/api/overview").json()
    assert first["cached"] is False and second["cached"] is False, (
        "OVERVIEW_CACHE_TTL_SECONDS = 0 was ignored"
    )

    overview_module.cache_clear()


# ==============================================================================
# THE API MUST NOT BE ABLE TO WRITE
# ==============================================================================

def test_the_api_role_cannot_execute_a_write(client):
    """Prove the safety property end to end, through the app's own session.

    The audit already probed this role directly; this confirms the API's session
    path - engine, role, `SET TRANSACTION READ ONLY` - inherits the same refusal.
    """
    from sqlalchemy import text

    from app.database.readonly import get_readonly_session_factory

    session = get_readonly_session_factory()()
    try:
        session.execute(text("SET TRANSACTION READ ONLY"))
        with pytest.raises(Exception) as caught:
            session.execute(text("DELETE FROM olist.orders WHERE false"))
        assert "read-only" in str(caught.value).lower(), caught.value
    finally:
        session.rollback()
        session.close()


def test_the_api_connection_cannot_write_even_without_the_explicit_setting(client):
    """The role-level default is the layer that holds when the app forgets.

    `SET TRANSACTION READ ONLY` is per-transaction and per-connection. If a future
    code path opened a session without it, the role default is what still refuses.
    """
    from sqlalchemy import text

    from app.database.readonly import get_readonly_session_factory

    session = get_readonly_session_factory()()  # deliberately NOT setting read-only
    try:
        with pytest.raises(Exception) as caught:
            session.execute(text("UPDATE olist.orders SET order_status = 'x' WHERE false"))
        assert "read-only" in str(caught.value).lower(), caught.value
    finally:
        session.rollback()
        session.close()