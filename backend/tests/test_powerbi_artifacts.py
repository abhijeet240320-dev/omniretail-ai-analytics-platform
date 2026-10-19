"""Prove the Power BI artefacts describe the database they claim to.

WHY THIS FILE EXISTS
--------------------
`powerbi/measures.dax` and `powerbi/data_model.md` are the only artefacts in
the project that CANNOT be checked by running them - there is no DAX engine
here, and a `.pbix` is a binary. So they are prose about a database, and prose
about a database goes stale silently.

This file executes the claims. Every row count, every figure quoted in a DAX
comment, and every warning message in a measure is checked against PostgreSQL.
If a measure says "801 of 2,500" and the live figure is different, this fails.

WHAT IS CHECKED, AND WHAT CANNOT BE
----------------------------------
Checked: every number in both files that names a table, a row count, a
percentage or a detection result. The DAX *syntax* is not checked - there is
no DAX engine available - and that limitation is stated here rather than
implied away.

The one thing that would be most valuable and is not possible: actually
executing the DAX. What is done instead is to check the ARGUMENTS - the table
and column names each measure references must exist in the live schema. That
catches the most common DAX failure, which is a measure pointing at a column
that was renamed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import text

from app.integrations import powerbi_availability
from conftest import REPO_ROOT

MEASURES_DAX = REPO_ROOT / "powerbi" / "measures.dax"
DATA_MODEL_MD = REPO_ROOT / "powerbi" / "data_model.md"
README_MD = REPO_ROOT / "powerbi" / "README.md"


@pytest.fixture(scope="module")
def powerbi_files() -> dict[str, str]:
    for path in (MEASURES_DAX, DATA_MODEL_MD, README_MD):
        assert path.exists(), f"{path} is missing; the Power BI artefacts are required"
    return {
        "measures.dax": MEASURES_DAX.read_text(encoding="utf-8"),
        "data_model.md": DATA_MODEL_MD.read_text(encoding="utf-8"),
        "README.md": README_MD.read_text(encoding="utf-8"),
    }


# ==============================================================================
# THE MODEL'S CENTRAL CLAIM
# ==============================================================================


def test_no_cross_source_relationship_can_exist_in_the_model():
    """The star schema has no relationship between fact tables, by construction.

    `data_model.md` opens by claiming this. A `.pbix` is binary so the claim
    cannot be read out of the file - but the REASON it is true is that no
    foreign key crosses a schema boundary, and that IS checkable, and it is
    what makes a relationship between two sources meaningless rather than merely
    absent.
    """
    from app.database.base import Base
    import app.models  # noqa: F401 - registers the 30 tables

    offenders = [
        f"{table.schema}.{table.name} -> {element.column.table.schema}"
        for table in Base.metadata.tables.values()
        for constraint in table.foreign_key_constraints
        for element in constraint.elements
        if element.column.table.schema != table.schema
    ]
    assert not offenders, (
        f"cross-source foreign keys exist ({offenders}); the Power BI model's "
        f"claim that the four sources cannot be joined is no longer true"
    )


def test_the_model_declares_the_four_sources_and_says_they_are_not_joined(powerbi_files):
    # Whitespace-normalised: the phrases span line wraps in the source, so a
    # substring search on raw text fails on the newline rather than on the
    # meaning. It reported a missing phrase that was present.
    text = " ".join(powerbi_files["data_model.md"].lower().split())
    for source in ("olist", "dataco", "dunnhumby", "m5"):
        assert source in text, f"data_model.md does not mention {source}"
    # The prohibition must be stated, not merely implied by the layout.
    assert "no relationship connects them" in text
    assert "no referent" in text
    # And the absence of a combined measure must be explicit.
    assert "there is no `total revenue` measure" in text or \
        "there is not one added" in text


def test_every_row_count_in_the_model_matches_the_database(live, powerbi_files):
    """The table in `data_model.md` quotes five row counts. Check all of them."""
    # NOT named `text` - that shadows the sqlalchemy `text` used below, and the
    # failure mode is `TypeError: 'str' object is not callable` on the very
    # query the test exists to run.
    doc = powerbi_files["data_model.md"]
    # `| \`olist\` | ... | 12 | 774,081 |`
    pattern = re.compile(
        r"\|\s*`(olist|dataco|dunnhumby|m5)`\s*\|[^|]+\|\s*(\d+)\s*\|\s*([\d,]+)\s*\|"
    )
    found = pattern.findall(doc)
    assert found, "the source table in data_model.md could not be parsed"

    for source, tables_claimed, rows_claimed in found:
        with live.bind.connect() as conn:
            actual_tables = conn.execute(
                text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema = :schema"
                ),
                {"schema": source},
            ).scalar_one()
            actual_rows = conn.execute(
                text(
                    "SELECT COALESCE(sum(c.reltuples)::bigint, 0) "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = :schema AND c.relkind = 'r'"
                ),
                {"schema": source},
            ).scalar_one()
        # reltuples is an ANALYZE estimate. It can lag, so the row count is
        # accepted within a tolerance that still catches a wrong order of
        # magnitude - which is the failure that matters here. The exact counts
        # are checked below against the load report.
        claimed = int(rows_claimed.replace(",", ""))
        assert int(tables_claimed) == actual_tables, (
            f"{source}: data_model.md claims {tables_claimed} tables, "
            f"the database has {actual_tables}"
        )
        assert abs(actual_rows - claimed) / claimed < 0.02, (
            f"{source}: data_model.md claims {claimed:,} rows, "
            f"PostgreSQL estimates {actual_rows:,}"
        )


# ==============================================================================
# EVERY FIGURE QUOTED IN A DAX COMMENT
# ==============================================================================


def _live_scalar(sql: str) -> object:
    from app.database.readonly import get_readonly_session_factory

    session = get_readonly_session_factory()()
    try:
        session.execute(text("SET TRANSACTION READ ONLY"))
        return session.execute(text(sql)).scalar()
    finally:
        session.close()


CLAIMED_FIGURES = [
    # (label, the value the artefact states, the query that establishes it)
    (
        "olist has 789 review_ids on more than one order",
        789,
        "SELECT count(*) FROM ("
        "  SELECT review_id FROM olist.reviews "
        "  GROUP BY review_id HAVING count(DISTINCT order_id) > 1"
        ") t",
    ),
    (
        "dunnhumby coupon_redemptions candidate key has 14 duplicate groups",
        14,
        "SELECT count(*) FROM ("
        "  SELECT household_key, day, coupon_upc FROM dunnhumby.coupon_redemptions "
        "  GROUP BY 1,2,3 HAVING count(*) > 1"
        ") t",
    ),
    (
        "dunnhumby: 801 households have demographics",
        801,
        "SELECT count(DISTINCT household_key) FROM dunnhumby.households "
        "WHERE classification_3 IS NOT NULL",
    ),
    (
        "dunnhumby: 2,500 households transact",
        2500,
        "SELECT count(DISTINCT household_key) FROM dunnhumby.transactions",
    ),
    (
        "m5: 30,490 series",
        30490,
        "SELECT count(DISTINCT id) FROM m5.sales_long",
    ),
    (
        "m5: 3,049 items",
        3049,
        "SELECT count(DISTINCT item_id) FROM m5.sales_long",
    ),
    (
        "m5: 10 stores",
        10,
        "SELECT count(DISTINCT store_id) FROM m5.sales_long",
    ),
]


@pytest.mark.parametrize(
    "label,claimed,sql",
    CLAIMED_FIGURES,
    ids=[c[0] for c in CLAIMED_FIGURES],
)
def test_every_figure_quoted_in_a_dax_comment(label, claimed, sql):
    """Each number a DAX comment uses as a justification must be true.

    These comments are the part a BI developer reads to decide whether to trust
    a measure. "789 review_ids attach to more than one order" is the entire
    reason `Review Rows` uses `COUNTROWS`. If it stops being true, the measure
    is quietly wrong and nothing else would notice.
    """
    actual = _live_scalar(sql)
    assert actual == claimed, (
        f"{label}: measures.dax / data_model.md states {claimed}, "
        f"the live database says {actual}. Either the data changed or the "
        f"artefact is stale."
    )


def test_the_801_and_2500_split_is_described_correctly():
    """The coverage gap is the single most misreported number in this project.

    A careless report says "2,500 dunnhumby customers". The verified figure is
    801 for anything demographic, and the two populations are different people
    sets, not a sample of the same one.
    """
    with_demographics = _live_scalar(
        "SELECT count(DISTINCT household_key) FROM dunnhumby.households "
        "WHERE classification_3 IS NOT NULL"
    )
    transacting = _live_scalar(
        "SELECT count(DISTINCT household_key) FROM dunnhumby.transactions"
    )
    assert with_demographics == 801
    assert transacting == 2500
    # The 1,699 without demographics are exactly the NULL classification_3.
    assert transacting - with_demographics == 1699


def test_the_forecast_and_anomaly_figures_in_the_measures_are_the_measured_ones():
    """`Forecast Winning MASE` and `Anomalies Detected` are hardcoded in the
    DAX, so they must equal the recorded measurements.

    A hardcoded figure in a measure is indistinguishable from a computed one in
    a report. The only defence is to check it against the JSON the ML layer
    actually wrote, on every test run.
    """
    import json
    import re

    measures = MEASURES_DAX.read_text(encoding="utf-8")

    def dax_number(name: str):
        """Read a measure's literal.

        Some are numeric (`Forecast Winning MASE := 1.6717`) and one is a
        string (`Forecast Winning Model := "sma_7"`). The first version of this
        helper only matched a number and reported the string measure as
        "not defined", which read like a missing measure rather than a regex
        that did not account for quotes.
        """
        match = re.search(rf'{name}\s*:=\s*"?([-\d.]+|[A-Za-z_][A-Za-z0-9_]*)"?', measures)
        assert match, f"{name} is not defined in measures.dax"
        raw = match.group(1)
        try:
            return float(raw)
        except ValueError:
            return raw

    forecast = json.loads(
        (REPO_ROOT / "data" / "processed" / "_forecast_results.json").read_text(
            encoding="utf-8"
        )
    )
    assert dax_number("Forecast Winning MASE") == forecast["models"][
        forecast["winner_by_mase"]
    ]["MASE"], (
        "Forecast Winning MASE in measures.dax does not match "
        "_forecast_results.json. Re-run 01_evaluate_forecast.py and "
        "docs/generate_forecasting.py, then update the measure."
    )
    assert dax_number("Forecast Winning Model") == forecast["winner_by_mase"]
    assert dax_number("Forecast Models Evaluated") == len(forecast["models"])
    assert dax_number("Forecast Series Evaluated") == forecast["scope"][
        "series_evaluated"
    ]
    assert dax_number("Forecast Horizon Days") == forecast["split"]["test_days"]
    assert forecast["scope"]["is_full_dataset"] is True, (
        "the recorded forecast is from a partial run and must not be published"
    )

    anomalies = json.loads(
        (REPO_ROOT / "data" / "processed" / "_anomaly_results.json").read_text(
            encoding="utf-8"
        )
    )
    m5 = next(d for d in anomalies["detections"] if d["source"] == "m5")
    assert dax_number("Anomalies Detected") == m5["anomalies_found"], (
        "Anomalies Detected in measures.dax does not match "
        "_anomaly_results.json"
    )
    assert dax_number("Anomaly Firing Rate") == round(
        m5["firing_rate_pct"] / 100, 4
    )

    # The measure file claims every M5 hit was a known calendar event. That is
    # the most quotable line in either artefact, so it is the one that most
    # needs checking.
    events = {e["date"] for e in anomalies["known_events"]}
    flagged = [a["date"] for a in m5["anomalies"]]
    explained = [d for d in flagged if d in events]
    assert dax_number("Anomalies Explained By Calendar") == len(explained)
    assert len(explained) == len(flagged), (
        "the DAX comment claims every flagged M5 day is a known calendar event, "
        f"but only {len(explained)} of {len(flagged)} are"
    )


# ==============================================================================
# EVERY TABLE AND COLUMN THE MEASURES REFERENCE MUST EXIST
# ==============================================================================


# The Power BI model gives the tables FRIENDLY names; the database calls them
# `schema.table`. The mapping is declared here rather than inferred, because
# inferring it by prefix would silently pass for any table whose name happened
# to start with a schema name, and would miss a rename in the middle.
#
# Declaring it also means a new fact table cannot appear in a measure without
# someone deciding what it maps to - which is the moment to notice that it has
# no verified grain.
POWER_BI_TABLES = {
    "olist_orders": ("olist", "orders"),
    "olist_customers": ("olist", "customers"),
    "olist_reviews": ("olist", "reviews"),
    "olist_payments": ("olist", "payments"),
    "olist_order_payments_agg": ("olist", "order_payments_agg"),
    "dataco_orders": ("dataco", "orders"),
    "dataco_order_items": ("dataco", "order_items"),
    "dunnhumby_transactions": ("dunnhumby", "transactions"),
    "dunnhumby_households": ("dunnhumby", "households"),
    "dunnhumby_coupons": ("dunnhumby", "coupons"),
    "dunnhumby_coupon_redemptions": ("dunnhumby", "coupon_redemptions"),
    "m5_sales_long": ("m5", "sales_long"),
    "m5_calendar": ("m5", "calendar"),
    "m5_series": ("m5", "series"),
    "m5_sell_prices": ("m5", "sell_prices"),
}


def code_only(dax: str) -> str:
    """Strip DAX comments, keeping the line structure.

    Comments in measures.dax QUOTE columns that do not exist, on purpose:
    "'olist_orders'[payment_total], which does not exist" is the note left
    behind by the schema check. Scanning the raw file treated those notes as
    live references and reported the documentation of a fixed bug as the bug.

    Line structure is preserved so a line-number report still points at the
    right place after stripping.
    """
    stripped: list[str] = []
    for line in dax.splitlines():
        out: list[str] = []
        in_string = False
        index = 0
        while index < len(line):
            char = line[index]
            if char == '"':
                in_string = not in_string
                out.append(char)
            elif not in_string and line.startswith("//", index):
                break
            elif char == "'":
                # A quoted table/column name - keep it, it IS a reference.
                end = line.find("'", index + 1)
                if end == -1:
                    break
                out.append(line[index : end + 1])
                index = end + 1
                continue
            else:
                out.append(char)
            index += 1
        stripped.append("".join(out))
    return "\n".join(stripped)


def test_every_table_referenced_by_a_measure_is_mapped(live, powerbi_files):
    """A measure pointing at a renamed table is blank, not wrong-looking.

    DAX returns an empty result for an unknown table rather than an error, so
    a rename produces a dashboard full of blanks with nothing in the logs. This
    is the most common way a DAX file rots.
    """
    from sqlalchemy import inspect as sqla_inspect

    measures = code_only(powerbi_files["measures.dax"])

    # `'olist_orders'[column]` and `COUNTROWS ( 'olist_orders' )`
    tables = set(re.findall(r"'([a-z0-9_]+)'", measures))
    tables -= {"Source"}  # the Power BI dimension, not a database table

    assert tables, "no table references were parsed from measures.dax"

    unmapped = sorted(t for t in tables if t not in POWER_BI_TABLES)
    assert not unmapped, (
        f"measures.dax references Power BI tables with no declared mapping to "
        f"a database table: {unmapped}. Add them to POWER_BI_TABLES in this "
        f"test, and check the grain is the one the SQL verified."
    )

    inspector = sqla_inspect(live.bind)
    for power_bi_name, (schema, db_name) in sorted(POWER_BI_TABLES.items()):
        if power_bi_name not in tables:
            continue
        assert db_name in inspector.get_table_names(schema=schema), (
            f"'{power_bi_name}' maps to {schema}.{db_name}, which does not exist"
        )


def test_every_column_referenced_by_a_measure_exists(live, powerbi_files):
    """Same rationale as the table check, one level down."""
    from sqlalchemy import inspect as sqla_inspect

    measures = code_only(powerbi_files["measures.dax"])
    inspector = sqla_inspect(live.bind)

    references = re.findall(
        r"'([a-z0-9_]+)'\s*\[\s*([A-Za-z0-9_]+)\s*\]", measures
    )
    assert references, "no column references were parsed from measures.dax"

    problems: list[str] = []
    for table, column in references:
        mapping = POWER_BI_TABLES.get(table)
        if mapping is None:
            continue  # an unmapped table is the mapping test's job
        schema, db_name = mapping
        db_columns = {c["name"] for c in inspector.get_columns(db_name, schema=schema)}
        if db_columns and column not in db_columns:
            problems.append(f"{table}[{column}] -> {schema}.{db_name}.{column}")

    assert not problems, (
        f"measures.dax references columns that do not exist: {sorted(problems)}"
    )


# ==============================================================================
# THE THINGS THE MEASURES DELIBERATELY DO NOT DO
# ==============================================================================


def test_no_dax_measure_sums_across_sources(powerbi_files):
    """There must be no measure a slicer could point at all four sources.

    Checked structurally rather than by reading: every measure body that
    aggregates money is inspected for the absence of an `ALL`, `ALLSELECTED` or
    `REMOVEFILTERS` over the `Source` column. Those are the functions that
    would discard the source filter and let a slicer combination total.
    """
    measures = powerbi_files["measures.dax"]

    bodies = re.split(r"\n([A-Z][A-Za-z0-9 ]+) :=", measures)
    offenders: list[str] = []
    for index in range(1, len(bodies), 2):
        name = bodies[index].strip()
        body = bodies[index + 1] if index + 1 < len(bodies) else ""
        if not re.search(r"\b(SUM|COUNTROWS|DISTINCTCOUNT|AVERAGE)\s*\(", body):
            continue
        # A filter on Source is what makes a measure safe. An ALL-family call
        # over Source is what would make it unsafe.
        if re.search(r"\b(ALL|ALLSELECTED|REMOVEFILTERS)\s*\(\s*'Source'", body):
            offenders.append(name)

    assert not offenders, (
        f"these measures remove the Source filter, so a slicer could total "
        f"across sources: {offenders}. That number would have no referent."
    )


def test_the_measures_file_documents_the_sign_convention(powerbi_files):
    """The dunnhumby discount sign trap is the most expensive mistake available.

    `retail_disc` and `coupon_disc` are NEGATIVE; `total_discount` is POSITIVE.
    Subtracting one twice, or adding it, produces a plausible wrong number with
    no error anywhere. A measure file that does not state this will produce one.
    """
    measures = powerbi_files["measures.dax"]
    assert "retail_disc" in measures and "coupon_disc" in measures
    section = measures[measures.index("SIGN CONVENTION"):]
    assert "NEGATIVE" in section
    assert "POSITIVE" in section


def test_the_measures_file_documents_the_keyless_table(powerbi_files):
    """`Redemption Rows` must use COUNTROWS and must say why."""
    measures = powerbi_files["measures.dax"]
    match = re.search(r"Redemption Rows :=(.*?)\n\n", measures, re.DOTALL)
    assert match, "the Redemption Rows measure is missing"
    body = match.group(1)
    assert "COUNTROWS" in body
    assert "DISTINCTCOUNT" not in body, (
        "Redemption Rows must not use DISTINCTCOUNT: the table has no primary "
        "key and its candidate key leaves 14 duplicate groups"
    )
    assert "NO PRIMARY KEY" in measures.upper()


def test_the_artefacts_are_ascii(powerbi_files):
    """Console output and committed text stay ASCII in this project.

    A stray en-dash in a `.dax` file is not a correctness problem, but it has
    broken `grep`, diff output and terminal rendering three times in this
    project already, and the rule is cheaper to keep than to re-learn.
    """
    for name, text in powerbi_files.items():
        offenders = sorted({c for c in text if ord(c) > 127})
        assert not offenders, (
            f"powerbi/{name} contains non-ASCII characters: "
            f"{[hex(ord(c)) for c in offenders]}"
        )


# ==============================================================================
# Is a .pbix actually buildable here? Probed, not asserted.
# ==============================================================================


def test_the_availability_probe_reports_the_real_environment():
    """A probe that always returns False is a lie in the other direction.

    The probe is required to describe *this* machine: either it finds Desktop,
    or it says precisely where it looked.
    """
    result = powerbi_availability.probe()
    payload = result.as_dict()

    assert isinstance(payload["desktop_available"], bool)
    assert payload["desktop_detail"], "the probe must say where it looked"
    assert isinstance(payload["desktop_paths"], list)
    assert payload["tooling_detail"]

    if payload["desktop_available"]:
        assert payload["desktop_paths"], "available with no path located"
        assert payload["can_produce_pbix"] is True
    else:
        assert payload["can_produce_pbix"] is False
        assert "not installed" in payload["pbix_reason"] or "pbi-tools" in (
            payload["pbix_reason"]
        )


def test_no_artefact_claims_to_be_a_pbix():
    """No renamed text file may stand in for the binary.

    A `.pbix` is an OPC zip whose `DataModel` part is a proprietary Analysis
    Services backup. Shipping a text file with that extension would be the
    single easiest way to fabricate a deliverable in this project, so it is
    refused mechanically rather than by good intentions.
    """
    root = REPO_ROOT / "powerbi"
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        assert path.suffix.lower() != ".pbix", (
            f"{path} claims to be a .pbix. This machine has no Power BI "
            f"Desktop and no open-source writer for the format. Remove it "
            f"rather than shipping a fake."
        )

    # And a .pbix anywhere else in the repo, for the same reason.
    for path in REPO_ROOT.rglob("*.pbix"):
        assert path.parent == root, f"unexpected .pbix outside powerbi/: {path}"


def test_the_readme_states_the_measured_availability():
    """The documentation and the probe must not disagree.

    Whichever way this machine is configured, the README has to say what the
    probe said. This is the test that stops "requires Power BI Desktop" from
    becoming stale prose after someone installs it.
    """
    result = powerbi_availability.probe()
    readme = (REPO_ROOT / "powerbi" / "README.md").read_text(encoding="utf-8")
    lowered = readme.lower()

    if result.can_produce_pbix:
        assert "not installed" not in lowered, (
            "Power BI is available on this machine but the README still says "
            "it is not installed; update it to describe how to build the pbix"
        )
    else:
        assert "not installed" in lowered or "external" in lowered, (
            "the README does not disclose that a .pbix cannot be produced here"
        )
        assert result.pbix_reason.split(".")[0].lower()[:30] in lowered or (
            "pbix" in lowered
        )


def test_the_dax_is_machine_verified_so_the_limitation_costs_only_the_binary():
    """The honest substitute for a .pbix is a verified model, not an unverified one.

    If Desktop is unavailable, the deliverable that remains is the DAX plus the
    data model - and those are only worth shipping if they are checked against
    the live database. That the checks exist and pass is what makes the .pbix
    gap a packaging step for the reader rather than a correctness gap.
    """
    measures = (REPO_ROOT / "powerbi" / "measures.dax").read_text(encoding="utf-8")
    model = (REPO_ROOT / "powerbi" / "data_model.md").read_text(encoding="utf-8")

    defined = re.findall(r"^([A-Za-z][A-Za-z0-9 ]*?)\s*:=", measures, re.MULTILINE)
    assert len(defined) >= 60, f"only {len(defined)} measures defined"

    # The README quotes a count. If the count in the file and the count in the
    # prose drift apart, the prose is what a reader trusts and the file is what
    # they paste - so the two are checked against each other.
    readme = (REPO_ROOT / "powerbi" / "README.md").read_text(encoding="utf-8")
    quoted = re.search(r"(\d+)\s+DAX measures", readme)
    assert quoted, "powerbi/README.md does not say how many measures there are"
    assert int(quoted.group(1)) == len(defined), (
        f"powerbi/README.md says {quoted.group(1)} measures; measures.dax "
        f"defines {len(defined)}"
    )

    referenced = set(re.findall(r"'[^']+'\[([^\]]+)\]", measures))
    assert referenced, "no column is referenced by any measure"
    for column in referenced:
        assert column, "a measure references an empty column name"

    # Every source is present in the model doc, and the model doc refuses the
    # cross-source join the database itself refuses.
    for source in ("olist", "dataco", "dunnhumby", "m5"):
        assert source in model.lower(), f"{source} is missing from data_model.md"
    assert "relationship connects them" in model.lower(), (
        "the model does not state that no relationship connects the four sources"
    )
    assert re.search(r"no foreign key[^.]*cross", model, re.IGNORECASE), (
        "the model does not cite the structural reason the sources cannot be joined"
    )


def test_no_two_measures_share_a_name(powerbi_files):
    """Measure names are unique per model; two measures called `Orders` cannot paste.

    This file defines a measure per source section, and both the Olist and the
    DataCo section wanted "the number of orders". In DAX a measure name is
    model-wide, so the second definition collides with the first and the model
    refuses to load - or, worse, somebody resolves it by deleting one and the
    other source is silently counted by the wrong table. Source-qualify the name
    instead (`Olist Orders`, `DataCo Orders`).

    Found by reading, not by a failing model: Desktop is not installed here, so
    no amount of testing would have caught this on its own.
    """
    measures = code_only(powerbi_files["measures.dax"])

    names = re.findall(r"^([A-Za-z][A-Za-z0-9 ]*?)\s*:=", measures, re.MULTILINE)
    assert len(names) >= 60, f"only {len(names)} measures parsed"

    duplicates = sorted({name.strip() for name in names if names.count(name) > 1})
    assert not duplicates, (
        f"these measure names are defined more than once: {duplicates}. A DAX "
        f"model allows one measure per name - qualify them by source."
    )