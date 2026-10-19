"""Regression tests for the Alembic baseline.

WHAT IS ACTUALLY BEING PROTECTED HERE
-------------------------------------
A migration history is a liability if it can quietly stop describing reality.
Three things can happen and none of them is caught by "the tests still pass":

1. Someone adds a table to the models and never writes a migration. The
   application works - the model is just an unused Python class - until
   somebody tries to build a fresh database from migrations and finds 29
   tables where the code expects 30.
2. Someone reintroduces a primary key on dunnhumby.coupon_redemptions. The
   rejected candidate key comes back, and the reason it was rejected (14 real
   duplicate rows) is quietly forgotten.
3. The schema comments in the baseline drift from the bootstrap SQL, which
   duplicates them. Two sources of truth for the same sentence always diverge.

These tests parse the migration source rather than executing it. Executing it
needs a scratch database, which needs a CREATEDB privilege the `omniretail`
role deliberately does not have; that round trip was performed once by hand and
its result is recorded in backend/alembic/README.md. A source-level test runs in
milliseconds and fails for the same reasons, so it can run on every commit.

THE CAVEAT THESE TESTS CANNOT REMOVE
------------------------------------
None of this means Alembic can see primary keys. It cannot - see
test_autogenerate_cannot_see_a_changed_primary_key in
test_models_match_catalog.py. These tests are the reason that is acceptable.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from conftest import REPO_ROOT

from app.database.base import Base

import app.models  # noqa: F401  - importing registers every table on Base.metadata

ALEMBIC_INI = REPO_ROOT / "alembic.ini"
ALEMBIC_DIR = REPO_ROOT / "backend" / "alembic"
BOOTSTRAP_SCHEMAS_SQL = (
    REPO_ROOT / "backend" / "sql" / "00_bootstrap" / "03_schemas_and_grants.sql"
)


@pytest.fixture(scope="module")
def revisions() -> list:
    """Every revision in the history, oldest first, read through Alembic's own API.

    Deliberately not a glob: ScriptDirectory is what `alembic` itself uses, so
    this sees exactly what the CLI sees, including files it would refuse.
    """
    config = Config(str(ALEMBIC_INI))
    # Absolute, because alembic.ini resolves script_location against the CURRENT
    # DIRECTORY rather than against the ini file. Relying on the working
    # directory would make this test depend on where pytest was invoked.
    # set_main_option() returns None and cannot be chained.
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    return list(ScriptDirectory.from_config(config).walk_revisions())


@pytest.fixture(scope="module")
def baseline(revisions):
    """The ROOT revision - the one that builds the schema from nothing.

    Deliberately not `revisions[0]`. `walk_revisions()` runs head to base, so
    indexing would return the NEWEST revision, and every test here would then be
    asserting against whatever was added last. The baseline is identified as the
    revision with no parent, which stays true however many migrations follow it.

    It also does not assert that there is only one revision. That is
    test_single_linear_head's job, and doing it here would turn the next
    legitimate migration into eight fixture errors instead of one clear failure.
    """
    roots = [revision for revision in revisions if revision.down_revision is None]
    assert len(roots) == 1, f"expected exactly one root revision, found {roots}"
    return roots[0]


def parse_baseline(baseline) -> ast.Module:
    """AST of the baseline migration file.

    The path comes from the `Script` object Alembic itself resolved, rather than
    from a glob, so this reads exactly the file `alembic` would run.
    """
    path = ALEMBIC_DIR / Path(str(baseline.path).replace("\\", "/"))
    assert path.exists(), f"Alembic reported a revision at {path} but it is not there"
    return ast.parse(path.read_text(encoding="utf-8"))


def create_table_calls(tree: ast.Module) -> list[dict]:
    """Every op.create_table() in the migration, with its schema and key columns.

    Reading the AST rather than the rendered SQL is what lets the test name the
    exact table that drifted instead of diffing a wall of DDL.
    """
    tables = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "create_table" or not node.args:
            continue

        schema = next(
            (kw.value.value for kw in node.keywords if kw.arg == "schema"), None
        )
        primary_key: list[str] = []
        for arg in node.args[1:]:
            if (
                isinstance(arg, ast.Call)
                and isinstance(arg.func, ast.Attribute)
                and arg.func.attr == "PrimaryKeyConstraint"
            ):
                primary_key = [a.value for a in arg.args]

        tables.append(
            {
                "schema": schema,
                "name": node.args[0].value,
                "primary_key": primary_key,
            }
        )
    return tables


def executed_statements(tree: ast.Module) -> list[str]:
    """The literal SQL strings passed to op.execute()."""
    statements = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "execute"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            statements.append(node.args[0].value)
    return statements


def foreign_keys(tree: ast.Module) -> list[tuple[str, str, str]]:
    """(owning schema, constraint name, target) for every inline foreign key."""
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "create_table":
            continue
        schema = next(
            (kw.value.value for kw in node.keywords if kw.arg == "schema"), None
        )
        for arg in node.args[1:]:
            if not (
                isinstance(arg, ast.Call)
                and isinstance(arg.func, ast.Attribute)
                and arg.func.attr == "ForeignKeyConstraint"
            ):
                continue
            name = next((kw.value.value for kw in arg.keywords if kw.arg == "name"), "")
            # sa.ForeignKeyConstraint([...local...], [...remote...], name=...)
            # The remote list is an ast.List node, not a plain element.
            remote = arg.args[1] if len(arg.args) > 1 else None
            target = remote.elts[0].value if isinstance(remote, ast.List) else ""
            found.append((schema, name, target))
    return found


def test_single_linear_head(revisions):
    """Exactly one head, and every revision reachable from it.

    A branch in the history is a design decision somebody has to make out loud -
    two code paths upgrading the same database is a data-loss story waiting to
    happen. This asserts the shape, not a particular count, so adding the second
    migration will not fail here.
    """
    # Config.set_main_option() returns None, so it cannot be chained - a small
    # trap that fails with 'NoneType has no attribute get_alembic_option'.
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_DIR))

    heads = ScriptDirectory.from_config(config).get_heads()
    assert len(heads) == 1, (
        f"the history must have exactly one head, found {heads}. A second head "
        "means two upgrade paths for the same database."
    )
    # walk_revisions() walks backwards from the single head, so a non-empty result
    # means the history is one line rather than a tree with an orphan branch.
    assert revisions, "the history is empty; there is nothing to migrate"


def test_baseline_creates_exactly_the_modelled_tables(baseline):
    """The migration and the models must describe the same 30 tables.

    This is the test that catches "a table was added to the models and no
    migration was written". Without it, a fresh `alembic upgrade head` would
    silently produce a database missing whatever was added.
    """
    created = create_table_calls(parse_baseline(baseline))
    in_migration = {(t["schema"], t["name"]) for t in created}
    in_models = {(table.schema, table.name) for table in Base.metadata.tables.values()}

    assert in_migration == in_models, (
        "the baseline migration and Base.metadata disagree:\n"
        f"  only in the migration: {sorted(in_migration - in_models)}\n"
        f"  only in the models   : {sorted(in_models - in_migration)}"
    )
    assert len(in_migration) == 30, f"expected 30 tables, found {len(in_migration)}"


def test_baseline_creates_the_five_schemas(baseline):
    """Alembic never emits CREATE SCHEMA, so the baseline has to.

    Without this block `alembic upgrade head` on a genuinely empty database
    fails with `schema "olist" does not exist`. The block was added by hand;
    this test is what stops it being deleted as "redundant with the bootstrap".
    """
    statements = executed_statements(parse_baseline(baseline))
    created = {
        match.group(1)
        for statement in statements
        if (match := re.search(r"CREATE SCHEMA IF NOT EXISTS (\w+)", statement))
    }
    assert created == {"dunnhumby", "dataco", "olist", "m5", "analytics"}, (
        f"baseline creates schemas {sorted(created)}"
    )


def test_baseline_schema_comments_match_the_bootstrap(baseline):
    """The migration and the bootstrap SQL both carry the schema comments.

    That is deliberate duplication - the bootstrap runs first and is what a DBA
    reads, while a from-scratch `alembic upgrade head` must not lose the
    provenance annotations. Two sources of truth for one sentence always
    diverge, so this test pins them together.
    """
    from_migration = dict(
        re.findall(
            r"COMMENT ON SCHEMA (\w+)\s+IS '([^']*)'",
            "\n".join(executed_statements(parse_baseline(baseline))),
        )
    )
    bootstrap = BOOTSTRAP_SCHEMAS_SQL.read_text(encoding="utf-8")
    # \s+ rather than a single space: the bootstrap pads the schema names so the
    # CREATE SCHEMA and COMMENT ON SCHEMA blocks line up, e.g.
    # `COMMENT ON SCHEMA dataco    IS '...'`.
    from_bootstrap = dict(
        re.findall(r"COMMENT ON SCHEMA (\w+)\s+IS '([^']*)';", bootstrap)
    )

    assert from_migration == from_bootstrap, (
        "the schema comments have drifted between the baseline migration and "
        f"{BOOTSTRAP_SCHEMAS_SQL.name}:\n"
        f"  migration: {from_migration}\n"
        f"  bootstrap: {from_bootstrap}"
    )
    assert len(from_migration) == 5


def test_baseline_keys_match_the_models(baseline):
    """Primary keys in the migration must equal primary keys in the models.

    Alembic cannot do this check (no primary-key comparator exists), which is
    precisely why it is done here.
    """
    created = {
        (t["schema"], t["name"]): t["primary_key"]
        for t in create_table_calls(parse_baseline(baseline))
    }
    problems = []
    for table in sorted(Base.metadata.tables.values(), key=lambda t: (t.schema or "", t.name)):
        expected = [column.name for column in table.primary_key.columns]
        actual = created.get((table.schema, table.name))
        if actual is None:
            problems.append(f"  {table.schema}.{table.name}: absent from the migration")
        elif actual != expected:
            problems.append(
                f"  {table.schema}.{table.name}: migration={actual} models={expected}"
            )
    assert not problems, "primary keys disagree:\n" + "\n".join(problems)


def test_baseline_preserves_the_keyless_coupon_redemptions(baseline):
    """dunnhumby.coupon_redemptions must stay keyless in the migration too.

    The candidate key (household_key, day, coupon_upc) was tested against the
    real rows and REJECTED - 14 rows genuinely repeat it. If a future edit adds
    `primary_key=True` to one of those columns, `alembic upgrade head` would
    start failing with a duplicate-key violation on the very first load, which
    is the correct outcome but an expensive way to learn it. Catching it here
    costs nothing.
    """
    tables = {
        (t["schema"], t["name"]): t
        for t in create_table_calls(parse_baseline(baseline))
    }
    entry = tables.get(("dunnhumby", "coupon_redemptions"))
    assert entry is not None, "dunnhumby.coupon_redemptions vanished from the baseline"
    assert entry["primary_key"] == [], (
        "a primary key was added to dunnhumby.coupon_redemptions; its candidate "
        "key was rejected against the real rows (14 duplicates)"
    )


def test_baseline_has_no_cross_source_foreign_key(baseline):
    """The project's central rule, enforced on the generated DDL.

    The same rule is asserted against the models in
    test_no_model_relationship_crosses_a_source. It is asserted a second time
    here because a migration is the artefact a DBA actually runs: a hand-edited
    `sa.ForeignKeyConstraint(['household_key'], ['olist.customers.x'])` would
    create a real cross-source join that no test on the models would ever see.
    """
    offenders = [
        f"  {schema}.{name} -> {target}"
        for schema, name, target in foreign_keys(parse_baseline(baseline))
        if target and target.split(".")[0] != schema
    ]
    assert not offenders, "cross-source foreign key(s) in the migration:\n" + "\n".join(offenders)


def test_baseline_drops_the_schemas_it_creates(baseline):
    """A downgrade that leaves the schemas behind makes the next upgrade a no-op lie.

    `alembic downgrade base` followed by `alembic upgrade head` must return to
    the same state, or a rollback is not a rollback.

    This reads the SOURCE. It cannot tell you whether the SQL is valid - an
    earlier version of this file contained `DROP SCHEMA IF EXISTS analytics AND
    NOT EXISTS (...)`, which reads correctly and is a syntax error, and the
    whole downgrade failed with it. `test_downgrade_round_trip_executes` is the
    one that runs it.
    """
    statements = executed_statements(parse_baseline(baseline))
    assert any("DROP SCHEMA" in s for s in statements), "downgrade leaves the schemas behind"
    for schema in ("dunnhumby", "dataco", "olist", "m5"):
        assert any(
            f"DROP SCHEMA IF EXISTS {schema}" in s for s in statements
        ), f"downgrade does not drop {schema}"


def test_downgrade_round_trip_executes(live, baseline):
    """The baseline's REAL `downgrade()` must run without error.

    This exists because a syntax error in the downgrade survived a whole round
    of work. The line was

        DROP SCHEMA IF EXISTS analytics AND NOT EXISTS (SELECT ...)

    which reads correctly, greps fine, contains the words DROP SCHEMA, and is
    invalid SQL - PostgreSQL parses `analytics AND` as the schema name. Only
    executing it finds that out. `test_baseline_drops_the_schemas_it_creates`
    reads the text and cannot.

    HOW IT STAYS SAFE
    ------------------
    The migration module's `op` is replaced with a small object that executes
    against a connection the test owns, and the connection is rolled back
    unconditionally - in a `finally`, so a mid-way failure cannot leave the
    database with some tables dropped. PostgreSQL has transactional DDL, so the
    rollback restores the schema exactly.

    The real `downgrade()` is called rather than a reimplementation of it. A
    test that duplicates the statements tests the copy, not the migration.
    """
    import importlib.util

    from sqlalchemy import text

    migration = _load_revision_module(baseline)

    class _Ops:
        """Just enough of alembic.op to run the downgrade against our connection."""

        def __init__(self, connection):
            self._connection = connection
            self.statements: list[str] = []

        def drop_table(self, table_name, schema=None, **kwargs):
            self._connection.execute(
                text(f'DROP TABLE IF EXISTS "{schema}"."{table_name}" CASCADE')
            )
            self.statements.append(f"DROP TABLE {schema}.{table_name}")

        def execute(self, statement):
            self._connection.execute(text(statement))
            self.statements.append(statement.strip().splitlines()[0])

    engine = live.bind
    with engine.connect() as connection:
        before = _table_count(connection)
        assert before == 30, f"expected 30 tables to start from, found {before}"

        ops = _Ops(connection)
        original = migration.op
        migration.op = ops
        try:
            migration.downgrade()
            after = _table_count(connection)
            assert after == 0, f"downgrade left {after} table(s) behind"
        finally:
            migration.op = original
            # Unconditional: a failure half way through must not leave the
            # database missing tables, which is what makes this test safe to run
            # against the real thing.
            connection.rollback()

    with engine.connect() as connection:
        assert _table_count(connection) == 30, (
            "the rollback did not restore the schema - PostgreSQL DDL is "
            "transactional, so something committed behind this test's back"
        )


def _load_revision_module(baseline):
    """Import the migration file as a module, so its `downgrade()` can be called."""
    import importlib.util

    path = ALEMBIC_DIR / Path(str(baseline.path).replace("\\", "/"))
    spec = importlib.util.spec_from_file_location("baseline_revision", path)
    assert spec and spec.loader, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _table_count(connection) -> int:
    from sqlalchemy import text

    return connection.execute(
        text(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n "
            "ON n.oid = c.relnamespace "
            "WHERE c.relkind = 'r' AND n.nspname IN "
            "('olist', 'dataco', 'dunnhumby', 'm5')"
        )
    ).scalar_one()


def test_loaded_database_is_stamped_at_head(live, revisions):
    """The database must be under migration control, not merely loadable.

    `backend/scripts/db/04_load.py` creates the tables directly, so Alembic has
    no way to know about them. `alembic stamp head` records that the baseline
    describes what is already there WITHOUT running it - running it would fail,
    because the tables exist. If this test fails, the fix is:
        .venv/Scripts/python.exe -m alembic stamp head
    """
    from sqlalchemy import text

    head = revisions[0].revision
    with live.bind.connect() as conn:
        stamped = conn.execute(text("SELECT version_num FROM alembic_version")).fetchall()
    assert [row[0] for row in stamped] == [head], (
        f"alembic_version says {[r[0] for r in stamped]}, head is {head!r}. "
        "If the tables were just loaded, run: alembic stamp head"
    )
