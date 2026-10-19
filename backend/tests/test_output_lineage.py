"""Every claim in `backend/config/output_lineage.yaml` is checked against reality.

THE POINT OF THIS FILE
----------------------
`docs/data_lineage.md` is generated and proves where the DATA came from. It
stops at the database, because that is as far as the loader can see. Nothing in
the repository answers the question a reader has about a number on screen:
which table, at what grain, through which query, via which endpoint, on which
page?

So that map is written down in `output_lineage.yaml`, and this file takes none
of it on trust:

* `source` is one of the four real schemas
* `table` exists in PostgreSQL, with the named grain column actually present
* `sql` names a query in the live catalogue - not a plausible-looking name
* `endpoint` is a route really registered on the FastAPI application
* `frontend_route` is a page id really defined in `routes.ts`
* `transformation` is the real ETL module for that source, and it exists
* every artefact's `produced_by` script exists

WHAT WOULD FAIL HERE
--------------------
A query renamed in the catalogue. A table dropped by a migration. An endpoint
moved. A page deleted from the sidebar. A source renamed. Each of those makes
this file red, which is the only reason a lineage document stays true: without
it, a lineage map is a story.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from sqlalchemy import text

from app.main import app
from app.services.query_catalogue import load_catalogue

REPO_ROOT = Path(__file__).resolve().parents[2]
LINEAGE_YAML = REPO_ROOT / "backend" / "config" / "output_lineage.yaml"
ROUTES_TS = REPO_ROOT / "frontend" / "src" / "lib" / "routes.ts"

SOURCE_SCHEMAS = {"olist", "dataco", "dunnhumby", "m5"}

# The grain column each entry's table is checked against. A lineage claim
# about grain that is not backed by a column in the live schema is a claim
# about nothing.
GRAIN_COLUMN = {
    "olist.orders": "order_id",
    "olist.reviews": "review_id",
    "dataco.orders": "order_id",
    "dataco.products": "product_card_id",
    "dunnhumby.transactions": "household_key",
    "dunnhumby.household_rfm": "household_key",
    "dunnhumby.campaigns": "campaign",
    "dunnhumby.coupon_redemptions": "household_key",
    "m5.sales_long": "id",
    "m5.calendar": "d_num",
}

ETL_FOR_SOURCE = {
    "olist": "backend/etl/olist.py",
    "dataco": "backend/etl/dataco.py",
    "dunnhumby": "backend/etl/dunnhumby.py",
    "m5": "backend/etl/m5.py",
}

REQUIRED_FIELDS = (
    "output",
    "source",
    "table",
    "grain",
    "transformation",
    "sql",
    "endpoint",
    "frontend_route",
)


@pytest.fixture(scope="module")
def lineage() -> dict:
    payload = yaml.safe_load(LINEAGE_YAML.read_text(encoding="utf-8"))
    assert isinstance(payload, dict), "output_lineage.yaml did not parse"
    return payload


@pytest.fixture(scope="module")
def catalogue() -> dict:
    return load_catalogue()


@pytest.fixture(scope="module")
def api_routes() -> set[str]:
    """Every path the running application actually serves.

    Read from the OpenAPI schema, not from `app.routes`. This FastAPI version
    keeps each `include_router` as a single `_IncludedRouter` entry rather than
    flattening it, so walking `app.routes` sees four default paths and none of
    the application's own - which would make every endpoint assertion below pass
    for the wrong reason, or fail for the wrong one. The OpenAPI schema is the
    authoritative list of what is served.

    Paths are templates, so the catalogue endpoint is
    `/api/analytics/queries/{name}`. That is what the application serves, and
    the lineage map records the concrete URL a caller actually requests. The
    reconciliation happens in `test_every_endpoint_is_really_registered`.
    """
    return set(app.openapi().get("paths", {}))


def _matches_template(path: str, template: str) -> bool:
    """True when `path` fills in `template`'s `{...}` segments."""
    parts = template.split("/")
    given = path.split("/")
    if len(parts) != len(given):
        return False
    for expected, actual in zip(parts, given):
        if expected.startswith("{") and expected.endswith("}"):
            if not actual:
                return False
        elif expected != actual:
            return False
    return True


@pytest.fixture(scope="module")
def live_columns(live) -> dict[str, set[str]]:
    """`schema.table` -> set of column names, straight from the live database."""
    from sqlalchemy import text as sql_text

    result: dict[str, set[str]] = {}
    with live.bind.connect() as connection:
        rows = connection.execute(
            sql_text(
                "SELECT table_schema, table_name, column_name "
                "FROM information_schema.columns "
                "WHERE table_schema = ANY(:schemas)"
            ),
            {"schemas": sorted(SOURCE_SCHEMAS)},
        )
        for schema, table, column in rows:
            result.setdefault(f"{schema}.{table}", set()).add(column)
    return result


@pytest.fixture(scope="module")
def db_tables(live_columns) -> set[str]:
    return set(live_columns)


@pytest.fixture(scope="module")
def frontend_routes() -> set[str]:
    text_body = ROUTES_TS.read_text(encoding="utf-8")
    return set(re.findall(r"id:\s*'([a-z-]+)'", text_body))


def _outputs(lineage: dict) -> list[dict]:
    entries = lineage.get("outputs") or []
    assert entries, "output_lineage.yaml declares no outputs"
    return entries


# ==============================================================================
# The file itself
# ==============================================================================


def test_every_entry_declares_every_field(lineage):
    for entry in _outputs(lineage):
        missing = [field for field in REQUIRED_FIELDS if not entry.get(field)]
        assert not missing, (
            f"lineage entry {entry.get('output', '<unnamed>')!r} is missing "
            f"{missing}. An unstated field is an unverifiable claim, so it "
            f"does not get an entry."
        )


def test_no_field_is_left_as_a_placeholder(lineage):
    """'TBD' in a lineage map is a lineage gap wearing a label."""
    placeholders = ("tbd", "todo", "unknown", "n/a", "?", "fixme", "xxx")
    for entry in _outputs(lineage) + (lineage.get("artefacts") or []):
        for key, value in entry.items():
            if not isinstance(value, str):
                continue
            assert value.strip().lower() not in placeholders, (
                f"lineage field {key!r} in {entry.get('output', entry.get('name'))!r} "
                f"is the placeholder {value!r}"
            )


# ==============================================================================
# Source and schema
# ==============================================================================


def _entry_ids(lineage: dict) -> list[str]:
    return [e["output"] for e in _outputs(lineage)]


def test_every_source_is_one_of_the_four_real_schemas(lineage):
    bad = sorted({e["source"] for e in _outputs(lineage)} - SOURCE_SCHEMAS)
    assert not bad, f"lineage names schemas that do not exist: {bad}"


def test_the_transformation_is_the_real_etl_module_for_that_source(lineage):
    for entry in _outputs(lineage):
        expected = ETL_FOR_SOURCE[entry["source"]]
        assert entry["transformation"] == expected, (
            f"{entry['output']!r} claims {entry['source']} but names "
            f"{entry['transformation']}, not {expected}"
        )
        assert (REPO_ROOT / expected).is_file(), f"{expected} does not exist"


# ==============================================================================
# Tables and grain
# ==============================================================================


def test_every_table_exists_in_the_live_database(lineage, db_tables):
    missing = sorted({e["table"] for e in _outputs(lineage)} - db_tables)
    assert not missing, (
        f"lineage names tables that do not exist in PostgreSQL: {missing}. "
        f"The database has {len(db_tables)} tables across the four schemas."
    )


@pytest.mark.parametrize("table,column", sorted(GRAIN_COLUMN.items()))
def test_the_named_grain_column_exists_on_the_table(live_columns, table, column):
    """A grain claim with no column behind it is a sentence, not a fact."""
    columns = live_columns.get(table)
    assert columns is not None, f"{table} does not exist in the live database"
    assert column in columns, (
        f"{table} has no column {column!r}; the grain claim is unsupported. "
        f"It has {sorted(columns)[:8]}..."
    )


def test_every_grain_table_actually_exists(db_tables):
    """`GRAIN_COLUMN` is test scaffolding; it must not drift either."""
    missing = sorted(set(GRAIN_COLUMN) - db_tables)
    assert not missing, (
        f"GRAIN_COLUMN names tables that do not exist: {missing}. This map is "
        f"test scaffolding, but a wrong entry in it makes the grain assertions "
        f"vacuous."
    )


def test_every_entry_names_a_grain_we_know_how_to_check(lineage):
    """If the grain cannot be machine-checked, it is a note, not a grain."""
    for entry in _outputs(lineage):
        table = entry["table"]
        assert table in GRAIN_COLUMN, (
            f"{entry['output']!r} names table {table!r}, which has no entry in "
            f"GRAIN_COLUMN; either the table is wrong or the test has not been "
            f"told how to check its grain"
        )


def test_the_m5_grain_is_the_pair_not_the_id(lineage):
    """The single most consequential grain claim in the project."""
    m5 = [e for e in _outputs(lineage) if e["source"] == "m5" and e["table"] == "m5.sales_long"]
    assert m5, "no m5.sales_long entry to check"
    for entry in m5:
        assert "(id, d_num)" in entry["grain"], (
            f"{entry['output']!r} states the m5 grain as {entry['grain']!r}. The "
            f"grain is (id, d_num); repeated ids are legitimate and 30,490 "
            f"series x 1,941 days is 59,181,090 rows."
        )


# ==============================================================================
# Queries, endpoints, frontend
# ==============================================================================


def test_every_sql_name_is_in_the_live_catalogue(lineage, catalogue):
    missing = sorted(
        {e["sql"] for e in _outputs(lineage)} - set(catalogue)
    )
    assert not missing, (
        f"lineage names queries that are not in the catalogue: {missing}. "
        f"The catalogue holds {len(catalogue)} queries."
    )


def test_every_sql_query_resolves_the_validator(lineage, catalogue):
    """A lineage entry pointing at unrunnable SQL is a broken edge."""
    from app.ai.sql_validator import validate

    for entry in _outputs(lineage):
        query = catalogue.get(entry["sql"])
        assert query is not None, f"{entry['sql']} vanished mid-test"
        verdict = validate(query.sql)
        assert verdict.ok, (
            f"{entry['sql']} is in the lineage map but the validator refuses it: "
            f"{verdict.explain()}"
        )


def test_every_endpoint_is_really_registered(lineage, api_routes):
    """The concrete URL a caller uses must fill a route the app really serves.

    Matched against the OpenAPI templates, so
    `/api/analytics/queries/olist.monthly_revenue_trend` is checked against
    `/api/analytics/queries/{name}` rather than being rejected for not being
    spelled the same way the router is.
    """
    unroutable = [
        endpoint
        for endpoint in {e["endpoint"] for e in _outputs(lineage)}
        if not any(_matches_template(endpoint, template) for template in api_routes)
    ]
    assert not unroutable, (
        f"lineage names endpoints the application does not serve: "
        f"{sorted(unroutable)}. It serves {sorted(api_routes)}."
    )


def test_the_named_table_is_actually_read_by_the_named_query(lineage, catalogue):
    """The strongest check in this file, and the one that matters most.

    An earlier draft of `output_lineage.yaml` named `dataco.shipments`,
    `dunnhumby.basket_lines` and `dunnhumby.campaign_performance` as tables.
    None of them exists. The real names are `dataco.orders` (which carries the
    shipping columns), `dunnhumby.transactions` (whose grain is the triple
    household/basket/product) and `dunnhumby.campaigns`. All three were
    plausible, and all three would have read as true to anyone who had not
    queried the schema.

    Parsing the catalogued SQL and comparing it against the declared table is
    what turns the map from a claim into a checked one.
    """
    from app.ai.sql_validator import referenced_tables

    wrong: list[str] = []
    for entry in _outputs(lineage):
        query = catalogue.get(entry["sql"])
        if query is None:
            continue  # reported by its own test
        tables = set(referenced_tables(query.sql))
        if entry["table"] not in tables:
            wrong.append(
                f"{entry['output']!r} says {entry['table']} but "
                f"{entry['sql']} actually reads {sorted(tables)}"
            )

    assert not wrong, "lineage names a table the query does not read:\n  " + "\n  ".join(wrong)


def test_every_query_reads_only_tables_from_its_own_source(lineage, catalogue):
    """Cross-source edges in the lineage map would be a cross-source join."""
    for entry in _outputs(lineage):
        query = catalogue.get(entry["sql"])
        if query is None:
            continue
        schemas = {t.split(".", 1)[0] for t in referenced_tables_all(query.sql)}
        others = schemas - {entry["source"]}
        assert not others, (
            f"{entry['sql']} is filed under source {entry['source']!r} but reads "
            f"{sorted(others)} as well; the lineage map would be documenting a "
            f"cross-source join"
        )


def referenced_tables_all(sql: str) -> set[str]:
    from app.ai.sql_validator import referenced_tables

    return set(referenced_tables(sql))


def test_the_analyst_endpoint_is_the_one_the_frontend_calls(lineage, api_routes):
    """`/api/ai/ask` must be a real route, not a plausible-looking name."""
    assert "/api/ai/ask" in api_routes
    entries = [e for e in _outputs(lineage) if e["endpoint"] == "/api/ai/ask"]
    assert entries, "no lineage entry uses the analyst endpoint"


def test_every_frontend_route_is_really_a_page(lineage, frontend_routes):
    missing = sorted({e["frontend_route"] for e in _outputs(lineage)} - frontend_routes)
    assert not missing, (
        f"lineage names pages that do not exist in routes.ts: {missing}. "
        f"Defined pages: {sorted(frontend_routes)}"
    )


def test_the_analyst_entry_does_not_claim_to_improvise_sql(lineage):
    """The NL path returns a catalogued statement; the entry must say so."""
    analyst = [e for e in _outputs(lineage) if e["endpoint"] == "/api/ai/ask"]
    assert analyst, "no lineage entry for the AI analyst"
    for entry in analyst:
        assert entry["sql"] in load_catalogue()
        assert "verbatim" in entry["grain"] or "catalogued" in entry["grain"].lower()


# ==============================================================================
# Artefacts
# ==============================================================================


def test_every_artefact_exists_and_names_a_real_script(lineage):
    artefacts = lineage.get("artefacts") or []
    assert len(artefacts) >= 5, f"only {len(artefacts)} artefacts recorded"

    for artefact in artefacts:
        name = artefact.get("name", "")
        producer = artefact.get("produced_by", "")

        assert (REPO_ROOT / name).is_file(), f"{name} does not exist"
        assert (REPO_ROOT / producer).is_file(), (
            f"{name} claims to be produced by {producer}, which does not exist"
        )
        assert artefact.get("describes"), f"{name} does not say what it describes"


def test_every_endpoint_that_reads_an_artefact_is_declared(lineage):
    """`/api/forecasting` and `/api/anomalies` read JSON, not SQL.

    If those artefacts disappear, the endpoints return an error. They are in
    the artefacts list precisely so that is a documented dependency.
    """
    declared = {a["name"] for a in (lineage.get("artefacts") or [])}
    for required in (
        "data/processed/_forecast_results.json",
        "data/processed/_anomaly_results.json",
        "data/processed/_sql_results.json",
    ):
        assert required in declared, f"{required} is not declared as a dependency"


def test_the_artefact_that_roots_all_provenance_is_declared(lineage):
    declared = {a["name"] for a in (lineage.get("artefacts") or [])}
    assert "data/raw/_checksums.json" in declared, (
        "the SHA-256 lockfile is the root of every provenance claim in this "
        "project and must be declared as an artefact"
    )
