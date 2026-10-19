"""Regression tests: do the generated models still match the database?

The models in `backend/app/models/` are generated from the live catalog by
`backend/scripts/db/06_generate_models.py`, but a generated file checked into
git is still only a snapshot. Somebody can add a column in PostgreSQL, forget to
regenerate, and the application keeps working happily against a schema that no
longer exists.

These tests compare the two structures directly, field by field. If they differ,
one of them is wrong and the generator should be re-run - the message says so.
"""

from __future__ import annotations

import alembic
import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.schema import ForeignKeyConstraint, UniqueConstraint

# Pinned so that a future alembic upgrade which DOES learn to compare primary keys
# trips the assertion in test_autogenerate_cannot_see_a_changed_primary_key rather
# than silently invalidating that test's reasoning.
_ALEMBIC_VERSION = alembic.__version__

# Types must be compared through the POSTGRESQL dialect, not str().
# str() uses the generic dialect, where DateTime() renders as "DATETIME" and
# Double() as "DOUBLE", while reflection of the same columns reports
# "TIMESTAMP" and "DOUBLE PRECISION". Those are the same types on PostgreSQL, so
# comparing raw strings reports drift that does not exist. Compiling both sides
# with the real dialect compares what would actually be sent to the server.
_DIALECT = postgresql.dialect()


def type_name(sa_type) -> str:
    return str(sa_type.compile(dialect=_DIALECT))

from app.database.base import Base

import app.models  # noqa: F401  - importing registers every table on Base.metadata

# The `live` inspector fixture now lives in conftest.py, shared with
# test_migrations.py.

SOURCE_SCHEMAS = ("olist", "dataco", "dunnhumby", "m5")


def describe(inspector, schema: str):
    """Reduce a schema to comparable facts: tables, columns, keys, foreign keys.

    Both sides are put through the SAME function so the comparison cannot fail
    because the two representations are formatted differently - that would be a
    test artefact, not a real disagreement.
    """
    described = {}
    for table in sorted(inspector.get_table_names(schema=schema)):
        columns = inspector.get_columns(table, schema=schema)
        pk = inspector.get_pk_constraint(table, schema=schema) or {}
        described[f"{schema}.{table}"] = {
            "columns": {
                c["name"]: (type_name(c["type"]), bool(c["nullable"])) for c in columns
            },
            "primary_key": tuple(pk.get("constrained_columns") or []),
            "unique": sorted(
                (u.get("name"), tuple(u["column_names"]))
                for u in inspector.get_unique_constraints(table, schema=schema)
            ),
            "foreign_keys": sorted(
                (
                    fk.get("name"),
                    tuple(fk["constrained_columns"]),
                    f"{fk['referred_schema']}.{fk['referred_table']}."
                    + ".".join(fk["referred_columns"]),
                )
                for fk in inspector.get_foreign_keys(table, schema=schema)
            ),
        }
    return described


def describe_models(schema: str) -> dict:
    """The same facts as describe(), read from declarative Table objects.

    `inspect(Base.metadata)` returns the MetaData itself, not an Inspector, so
    there is no inspector API to lean on here. Reading the Table objects
    directly keeps the output shape identical to the reflected side, which is
    what makes the comparison meaningful rather than a formatting contest.
    """
    described = {}
    for key, table in sorted(Base.metadata.tables.items()):
        if table.schema != schema:
            continue
        primary = tuple(col.name for col in table.primary_key.columns)
        described[f"{schema}.{table.name}"] = {
            "columns": {
                col.name: (type_name(col.type), bool(col.nullable))
                for col in table.columns
            },
            "primary_key": primary,
            "unique": sorted(
                (con.name, tuple(col.name for col in con.columns))
                for con in table.constraints
                if isinstance(con, UniqueConstraint)
            ),
            "foreign_keys": sorted(
                (
                    con.name,
                    tuple(sorted(e.parent.name for e in con.elements)),
                    ".".join(con.elements[0].target_fullname.split(".")),
                )
                for con in table.constraints
                if isinstance(con, ForeignKeyConstraint)
            ),
        }
    return described


@pytest.fixture(scope="module")
def from_database(live):
    return {
        schema: describe(live, schema)
        for schema in SOURCE_SCHEMAS
        if live.has_schema(schema)
    }


@pytest.fixture(scope="module")
def from_models():
    return {schema: describe_models(schema) for schema in SOURCE_SCHEMAS}


def test_same_tables(from_database, from_models):
    live = {t for tables in from_database.values() for t in tables}
    modelled = {t for tables in from_models.values() for t in tables}
    assert live == modelled, (
        "table set differs - regenerate with:\n"
        "  .venv/Scripts/python.exe backend/scripts/db/06_generate_models.py"
    )
    assert len(live) == 30, f"expected 30 tables, found {len(live)}"


def test_columns_match(from_database, from_models):
    problems = []
    for schema in from_database:
        for table, expected in from_database[schema].items():
            actual = from_models[schema].get(table, {}).get("columns", {})
            if expected["columns"] != actual:
                missing = set(expected["columns"]) - set(actual)
                extra = set(actual) - set(expected["columns"])
                wrong = {
                    k for k in set(expected["columns"]) & set(actual)
                    if expected["columns"][k] != actual[k]
                }
                problems.append(
                    f"  {table}: missing={sorted(missing)} extra={sorted(extra)} "
                    f"type-or-nullability-differs={sorted(wrong)}"
                )
    assert not problems, "columns drifted:\n" + "\n".join(problems)


def test_primary_keys_match(from_database, from_models):
    problems = [
        f"  {table}: database={expected['primary_key']} "
        f"models={from_models[schema][table]['primary_key']}"
        for schema in from_database
        for table, expected in from_database[schema].items()
        if expected["primary_key"] != from_models[schema][table]["primary_key"]
    ]
    assert not problems, "primary keys drifted:\n" + "\n".join(problems)


def test_foreign_keys_match(from_database, from_models):
    problems = [
        f"  {table}:\n      database={expected['foreign_keys']}\n"
        f"      models  ={from_models[schema][table]['foreign_keys']}"
        for schema in from_database
        for table, expected in from_database[schema].items()
        if expected["foreign_keys"] != from_models[schema][table]["foreign_keys"]
    ]
    assert not problems, "foreign keys drifted:\n" + "\n".join(problems)


def test_unique_constraints_match(from_database, from_models):
    problems = [
        f"  {table}: database={expected['unique']} "
        f"models={from_models[schema][table]['unique']}"
        for schema in from_database
        for table, expected in from_database[schema].items()
        if expected["unique"] != from_models[schema][table]["unique"]
    ]
    assert not problems, "unique constraints drifted:\n" + "\n".join(problems)


def test_no_model_relationship_crosses_a_source():
    """The project's central rule, enforced in the ORM layer rather than by convention.

    A foreign key from one schema to another would let dunnhumby household 123
    join Olist customer 123. It is impossible in the database today, so it must
    be impossible in the models too - and this test fails if anyone hand-edits
    a generated file to add one.
    """
    offenders = []
    for table in Base.metadata.tables.values():
        for constraint in table.foreign_key_constraints:
            for element in constraint.elements:
                target = element.column.table
                if target.schema != table.schema:
                    offenders.append(
                        f"{table.schema}.{table.name} -> {target.schema}.{target.name}"
                    )
    assert not offenders, "cross-source foreign key(s) in the models:\n  " + "\n  ".join(offenders)


def test_mappers_configure():
    """Every relationship must actually resolve, not just be syntactically present."""
    from sqlalchemy.orm import configure_mappers

    configure_mappers()


def test_autogenerate_finds_nothing_to_do(live):
    """Alembic's own opinion must be that the models and the database agree.

    This is the strongest available statement of "the models provably match the
    loaded tables": it is not our comparison function agreeing with itself, it
    is Alembic's independent comparator producing an EMPTY migration. If this
    ever returns ops, `alembic revision --autogenerate` would want to change the
    schema, and that is exactly the drift alarm.

    Both registries are compared, exactly as `alembic/env.py` does. `app.api_keys`
    is modelled on its own `AppBase` (see the docstring in
    `app/models/api_keys.py`), so passing `Base.metadata` alone would report
    `remove_table('api_keys')` plus both of its indexes and this test would fail
    for a bookkeeping reason rather than a real one.
    """
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from app.models.api_keys import AppBase

    engine = live.bind
    with engine.connect() as conn:
        context = MigrationContext.configure(
            conn, opts={"include_schemas": True, "compare_type": True}
        )
        ops = compare_metadata(context, [Base.metadata, AppBase.metadata])
    assert ops == [], "Alembic wants to change the schema:\n" + "\n".join(
        f"  {type(op).__name__}: {op}" for op in ops
    )


def test_primary_keyless_table_renders_no_primary_key():
    """dunnhumby.coupon_redemptions has no primary key, and that must stay true.

    SQLAlchemy always exposes `table.primary_key`, so a table with no key gets an
    EMPTY PrimaryKeyConstraint() sitting in `table.constraints`. That phantom is
    benign - it renders no PRIMARY KEY clause and produces no Alembic operation -
    but it is exactly the kind of thing that could become a real
    `create_primary_key` if the Core declaration ever changed. Pinning both
    halves of the behaviour means a future edit that introduces a genuine key
    has to be a decision rather than an accident.
    """
    from sqlalchemy.schema import CreateTable
    from sqlalchemy.dialects import postgresql

    table = Base.metadata.tables["dunnhumby.coupon_redemptions"]
    assert list(table.primary_key.columns) == [], "the keyless table gained a primary key"

    ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    assert "PRIMARY KEY" not in ddl.upper(), f"a PRIMARY KEY leaked into the DDL:\n{ddl}"


def test_autogenerate_cannot_see_a_changed_primary_key(live):
    """The real hazard is not the phantom - it is that Alembic is BLIND to primary keys.

    Investigated against alembic 1.20.0: the autogenerate constraint registry
    (alembic/ddl/_autogen.py) registers only unique_constraint, index,
    foreign_key_constraint and check_constraint. There is no primary-key sig and
    no compare_primary_key, so `compare_metadata` can never emit
    create_primary_key or drop_primary_key for a table that already exists.

    Consequence: if somebody deleted a PK from a model, or added `primary_key=True`
    to one of the keyless columns, the empty autogenerate would be a LIE of
    omission, not a clean bill of health. All that would surface is a
    `modify_nullable`, because a PK column is implicitly NOT NULL.

    So dunnhumby.coupon_redemptions' missing key is protected by
    test_primary_keys_match above - an explicit, purpose-built comparison - and
    NOT by test_autogenerate_finds_nothing_to_do. This test records the reason,
    because "autogenerate said zero ops" reads like much stronger evidence than
    it is.
    """
    from alembic.autogenerate import compare_metadata
    from alembic.ddl._autogen import _clsreg
    from alembic.migration import MigrationContext
    from sqlalchemy import BigInteger, Column, MetaData, Table

    assert "primary_key_constraint" not in _clsreg, (
        f"alembic {_ALEMBIC_VERSION} gained a primary-key comparator "
        f"({sorted(_clsreg)}); re-check test_autogenerate_cannot_see_a_changed_primary_key"
    )

    scratch = MetaData()
    Table(
        "keyless",
        scratch,
        Column("a", BigInteger(), primary_key=True),
        Column("b", BigInteger()),
        schema="public",
    )
    engine = live.bind
    with engine.connect() as conn:
        context = MigrationContext.configure(
            conn,
            opts={
                "include_schemas": True,
                "include_object": lambda o, n, t, r, c: False,
            },
        )
        ops = compare_metadata(context, scratch)
    assert not any(type(op).__name__ == "CreatePrimaryKeyOp" for op in ops), (
        "alembic emitted a primary-key operation; the reasoning in this test's "
        "docstring is now out of date"
    )
    from sqlalchemy.schema import CreateTable
    from sqlalchemy.dialects import postgresql

    table = Base.metadata.tables["dunnhumby.coupon_redemptions"]
    assert list(table.primary_key.columns) == [], "the keyless table gained a primary key"

    ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    assert "PRIMARY KEY" not in ddl.upper(), f"a PRIMARY KEY leaked into the DDL:\n{ddl}"


def test_generator_output_is_current(live):
    """The checked-in model files must equal what the generator produces now."""
    from pathlib import Path

    from conftest import REPO_ROOT, load_script

    module = load_script("backend/scripts/db/06_generate_models.py")
    stale = [
        path.name
        for path, content in module.render(live).items()
        if not path.exists() or path.read_text(encoding="utf-8") != content
    ]
    assert not stale, (
        "model files are stale - regenerate with:\n"
        "  .venv/Scripts/python.exe backend/scripts/db/06_generate_models.py\n"
        f"  stale: {stale}"
    )