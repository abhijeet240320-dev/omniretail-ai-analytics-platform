"""Regression tests for the loader / Alembic ownership split.

THE SPLIT BEING ENFORCED
-------------------------
    bootstrap_db.py  owns  the database, the roles, the GRANTs
    Alembic          owns  schemas, tables, columns, PK/UQ/FK constraints
    04_load.py       owns  the ROWS, and the evidence that the keys hold

Two layers writing the same schema is not a style preference. It is a way to end
up with a database that neither one describes, discovered only when somebody
tries to rebuild from scratch. These tests make the boundary executable.

WHY MOST OF THIS IS A SOURCE-LEVEL TEST
--------------------------------------
The strongest possible check - `alembic upgrade head` on an empty database and
compare the result with the models - needs a CREATEDB privilege the `omniretail`
role deliberately does not have, so it is a documented manual procedure in
backend/alembic/README.md rather than a test. Everything that CAN be asserted
per-commit is asserted here, and each one has been negative-tested.

THE MOST IMPORTANT TEST IN THIS FILE
------------------------------------
`test_loader_issues_no_ddl`. It reads the loader's source and fails on any DDL
statement. That single assertion is what stops the boundary eroding: it is
tempting, six months from now, to add back one `CREATE TABLE IF NOT EXISTS` so
a fresh checkout works without Alembic, and that is precisely the change that
leaves two owners of the schema.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from conftest import REPO_ROOT

# Importing the package is what registers all 30 tables on Base.metadata. Without
# it `Base.metadata` is empty and compare_metadata reports every real table as a
# `remove_table` - which reads exactly like catastrophic drift and is in fact
# nothing of the kind.
import app.models  # noqa: F401

LOAD_SCRIPT = REPO_ROOT / "backend" / "scripts" / "db" / "04_load.py"
LOADER_MODULE = REPO_ROOT / "backend" / "load" / "loader.py"
SCHEMA_MODULE = REPO_ROOT / "backend" / "load" / "schema.py"
REPORT = REPO_ROOT / "data" / "processed" / "_load_report.json"
SOURCE_SCHEMAS = ("olist", "dataco", "dunnhumby", "m5")

# The statements that would mean the loader owns the schema again. ALTER is
# included because `ALTER TABLE ... ADD CONSTRAINT` was the original sin: the
# loader used to declare keys, and that is how the two owners started.
DDL = re.compile(
    r"\b(CREATE\s+SCHEMA|DROP\s+SCHEMA|CREATE\s+TABLE|DROP\s+TABLE|ALTER\s+TABLE|"
    r"CREATE\s+INDEX|DROP\s+INDEX|CREATE\s+(TYPE|VIEW|FUNCTION)|"
    r"TRUNCATE\s+TABLE\s+\w+\s+[^;]*RESTART)\b",
    re.IGNORECASE,
)


def _executable_sql(path: Path) -> list[tuple[int, str]]:
    """Every SQL string that reaches a database, with its line number.

    Parsed rather than grepped so DOCSTRINGS ARE EXCLUDED. All three modules are
    heavily commented about exactly the DDL they no longer run - `DROP TABLE
    ... CASCADE`, `ALTER TABLE ADD CONSTRAINT` - and a grep over the raw text
    would match those explanations and fail on correct code.

    F-STRINGS ARE THE POINT, and getting that wrong made this test useless once.
    Every statement in the loader is an f-string, because it interpolates a
    schema and a table name. An AST scanner that only reads `ast.Constant`
    therefore sees none of them: a `CREATE TABLE` smuggled into the loader passed
    this test. F-strings are `ast.JoinedStr`, and the interpolations are rendered
    as `{...}` so `f'DROP TABLE IF EXISTS {schema}."{table}"'` still reads as
    `DROP TABLE IF EXISTS {...}."{...}"`, which is all the regex needs.

    The rule is structural: a string (plain, f-string, or concatenated) passed
    to `.execute(...)`, or assigned to a SQL-shaped name, is executable.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[int, str]] = []

    def as_text(node: ast.AST) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            parts = []
            for value in node.values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    parts.append(value.value)
                else:
                    parts.append("{...}")
            return "".join(parts)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = as_text(node.left)
            right = as_text(node.right)
            if left is not None and right is not None:
                return left + right
        return None

    class Walker(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            if _called_name(node.func) in {"execute", "cursor", "executemany"}:
                for arg in node.args:
                    text = as_text(arg)
                    if text is not None:
                        found.append((arg.lineno, text))
            self.generic_visit(node)

        def visit_Assign(self, node: ast.Assign) -> None:
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if any(re.search(r"SQL|STATEMENT|QUERY", t, re.IGNORECASE) for t in targets):
                text = as_text(node.value)
                if text is not None:
                    found.append((node.lineno, text))
            self.generic_visit(node)

    Walker().visit(tree)
    return found


def _called_name(func: ast.AST) -> str:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def test_loader_issues_no_ddl():
    """No CREATE TABLE, no DROP TABLE, no ALTER TABLE. In any of the three modules.

    This is the load-bearing test. Everything else here checks that a particular
    behaviour still works; this one checks that the OWNERSHIP BOUNDARY still
    exists. If the loader can create a table, the schema has two authors and the
    next divergence is a matter of time.
    """
    offenders: list[str] = []
    for path in (LOAD_SCRIPT, LOADER_MODULE, SCHEMA_MODULE):
        for lineno, sql in _executable_sql(path):
            match = DDL.search(sql)
            if match:
                offenders.append(f"  {path.name}:{lineno}: {match.group(0)!r} in {sql[:90]!r}")
    assert not offenders, (
        "the loader issues DDL, which belongs to Alembic:\n" + "\n".join(offenders)
    )


def test_loader_has_no_constraint_creation_helpers():
    """The `add_*_key` helpers must be gone, not merely unused.

    Dead code that creates constraints is worse than no code at all: it reads as
    an available option, and the next person under time pressure reaches for it.
    """
    source = LOADER_MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    defined = {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }
    assert not (defined & {"add_primary_key", "add_unique", "add_foreign_key"}), (
        f"loader.py still defines {sorted(defined & {'add_primary_key', 'add_unique', 'add_foreign_key'})}"
    )
    assert "create_table_sql" not in defined, (
        "schema.py should no longer build DDL - Alembic owns it"
    )


def test_loader_refuses_to_run_without_a_migrated_database(monkeypatch, capsys):
    """A database with tables but no alembic_version must be refused, not guessed at.

    This is the failure mode the split introduces: an old database built by the
    previous loader has perfectly good tables and no version row. The tempting
    behaviour is to carry on. The correct one is to stop and say which of the two
    commands would fix it, because stamping a database whose schema does not
    match the migration would mark it correct forever.
    """
    from load.loader import connect, table_exists  # noqa: F401

    import importlib.util

    spec = importlib.util.spec_from_file_location("load_step", LOAD_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)

    class FakeCursor:
        def __init__(self, revision):
            self._revision = revision
            self._sql = ""

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, *params):
            self._sql = sql
            if "to_regclass('public.alembic_version')" in sql:
                return None
            if "version_num" in sql:
                self._rows = [] if self._revision is None else [(self._revision,)]
                return None
            self._rows = []
            return None

        def fetchone(self):
            return (False,)  # alembic_version table is absent

        def fetchall(self):
            return getattr(self, "_rows", [])

    class FakeConn:
        def __init__(self, revision):
            self._revision = revision

        def cursor(self):
            return FakeCursor(self._revision)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(module, "connect", lambda: FakeConn(None))
    monkeypatch.setattr(module, "alembic_head", lambda: "baseline")
    monkeypatch.setattr("sys.argv", ["04_load.py"])

    with pytest.raises(SystemExit) as excinfo:
        module.main()
    message = str(excinfo.value)
    assert "no alembic_version" in message, message
    assert "alembic upgrade head" in message or "alembic stamp head" in message, message


def test_declared_constraints_match_the_migration(live):
    """What the loader verifies must be exactly what the migration declared.

    The loader's whole verification step reads `pg_constraint`. If the migration
    declared a constraint the loader never looks at, the report would quietly
    under-report - and "46 constraints verified" would be a claim about 46 of an
    unknown number.
    """
    from load.loader import connect, declared_constraints

    expected = {"primary key": 29, "unique": 2, "foreign key": 15}
    measured = {"primary key": 0, "unique": 0, "foreign key": 0}
    with connect() as conn:
        for schema in SOURCE_SCHEMAS:
            for table in live.get_table_names(schema=schema):
                found = declared_constraints(conn, schema, table)
                for kind in measured:
                    measured[kind] += len(found[kind])
    assert measured == expected, (
        f"pg_constraint holds {measured}, the load report claims {expected}"
    )


def test_coupon_redemptions_is_still_keyless(live):
    """The loader must not have acquired a key for the table that refused one.

    `dunnhumby.coupon_redemptions` has no primary key because its candidate was
    rejected against real rows. Alembic correctly declares none, the loader
    re-tests the candidate and records the rejection, and this asserts the end
    state. It is the single most load-bearing invariant in the database.
    """
    from load.loader import connect, declared_constraints

    with connect() as conn:
        found = declared_constraints(conn, "dunnhumby", "coupon_redemptions")
    assert found["primary key"] == [], "a primary key appeared on coupon_redemptions"
    assert found["unique"] == [], "a unique constraint appeared on coupon_redemptions"


def test_reviews_primary_key_is_the_composite(live):
    """olist.reviews must stay keyed on (review_id, order_id).

    789 review_ids are duplicated across orders, all from one customer with
    identical text and BOTH timestamps, and 32 of the order links were created
    after the review that references them. That is a one-to-many fan-out, not a
    resubmission, so review_id alone is not a key. Reverting this is the single
    easiest way to reintroduce double-counted review metrics.
    """
    from load.loader import connect, declared_constraints

    with connect() as conn:
        found = declared_constraints(conn, "olist", "reviews")
    columns = found["primary key"][0]["columns"] if found["primary key"] else []
    assert columns == ["review_id", "order_id"], (
        f"olist.reviews is keyed on {columns}, expected (review_id, order_id)"
    )


def test_load_report_records_the_migration_it_verified():
    """The report must name the revision its constraint verdicts belong to.

    Without it, "46 constraints verified" is a floating claim: nobody can tell
    which schema was measured, and a report generated under an older migration
    reads exactly like one generated under the current one.
    """
    import json

    assert REPORT.exists(), f"{REPORT} is missing"
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    assert report.get("alembic_revision"), "the report does not name an Alembic revision"
    assert report["alembic_revision"] == "baseline"
    assert len(report.get("constraints_verified", [])) == 46, (
        f"expected 46 verified constraints, found {len(report.get('constraints_verified', []))}"
    )
    assert len(report.get("constraints_rejected", [])) == 1, (
        f"expected exactly 1 rejected candidate, found {len(report.get('constraints_rejected', []))}"
    )
    # The pre-Alembic bucket names must be gone. A report that still carries
    # them describes work this loader does not do.
    for stale in ("constraints_added", "constraints_already_present"):
        assert stale not in report, (
            f"{stale} is a pre-Alembic bucket name; regenerate the report with "
            "`04_load.py --verify-only`"
        )


def test_no_schema_drift_after_the_load(live):
    """Alembic must find nothing to change after the loader has run.

    This is the end-to-end statement of the split: the loader filled 106 million
    rows without touching the schema, and the schema still matches the models
    exactly.

    CAVEAT, restated because it is easy to forget: this proves nothing about
    primary keys, which Alembic cannot compare. `test_primary_keys_match` in
    test_models_match_catalog.py is the authority for those.
    """
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from app.database.base import Base
    from app.models.api_keys import AppBase

    with live.bind.connect() as conn:
        context = MigrationContext.configure(
            conn, opts={"include_schemas": True, "compare_type": True}
        )
        # Both registries, matching alembic/env.py. Passing only `Base.metadata`
        # would report `remove_table('api_keys')` and the assertion below would
        # fail for the wrong reason, so the list matters.
        ops = compare_metadata(context, [Base.metadata, AppBase.metadata])
    assert ops == [], "Alembic wants to change the schema after loading:\n" + "\n".join(
        f"  {type(op).__name__}: {op}" for op in ops
    )


def test_migration_types_match_the_documented_inference_rules():
    """The types Alembic declared must equal what backend/load/schema.py infers.

    `db_constraints.yaml`'s type rules are why the columns are what they are -
    money is NUMERIC rather than DOUBLE PRECISION, and that decision is
    documented and load-bearing. The loader no longer creates tables, so nothing
    re-derives those types from the rules any more; this test does, so the rules
    cannot rot into a comment that no longer matches the schema.

    Runs in about two seconds despite touching all 30 processed CSVs, because
    `table_columns` reads a bounded sample from the head of each file rather
    than loading it - including the 4.5 GB one. That is why it lives in the fast
    tier: it is cheap enough to guard every commit, and it is exactly the kind
    of check that gets skipped when it is slow.

    A mismatch here is a real disagreement, not a spelling one: both sides are
    rendered in PostgreSQL vocabulary before being compared.
    """
    import importlib.util

    from load.schema import load_constraints, table_columns

    spec = importlib.util.spec_from_file_location("load_step", LOAD_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)

    constraints = load_constraints()
    rules = constraints["type_rules"]
    overrides = constraints.get("type_overrides", {})

    problems = []
    checked = 0
    for source, tables in constraints["load_order"].items():
        for table in tables:
            csv_path = REPO_ROOT / "data" / "processed" / source / f"{table}.csv"
            if not csv_path.exists():
                continue
            for column in table_columns(csv_path, rules, overrides, source, table):
                checked += 1
                expected = column.pg_type
                # The declared type is read from the migration, not the live
                # database: this test is about the two agreeing, and reading one
                # from the database would make it circular.
                declared = _declared_type(source, table, column.name)
                if declared is None:
                    problems.append(f"  {source}.{table}.{column.name}: not in the migration")
                elif _normalise(declared) != _normalise(expected):
                    problems.append(
                        f"  {source}.{table}.{column.name}: "
                        f"migration={declared!r} rules={expected!r}"
                    )
    assert not problems, (
        f"{len(problems)} column type(s) disagree with the documented rules:\n"
        + "\n".join(problems[:40])
    )
    assert checked > 200, f"only {checked} columns were checked - the CSVs are missing"


_TYPES: dict[tuple[str, str], dict[str, str]] | None = None


def _declared_types() -> dict[tuple[str, str], dict[str, str]]:
    """Column name -> declared SQL type, per table, parsed from the migrations.

    EVERY revision is walked, not just the first one `walk_revisions` yields.

    That was a latent bug. `next(iter(script.walk_revisions()))` returns the HEAD
    revision, which happened to be the baseline for months - one migration, so
    head and baseline were the same file. Adding
    `20261003_2100_api_keys.py` made head a DIFFERENT file that declares no
    source tables, and every one of the 264 source columns then reported "not
    in the migration".

    The failure was misleading rather than loud: it read like 264 type
    mismatches when the truth was that the parser had stopped looking at the
    right file. So the walk is now explicit and order-independent, and a later
    revision's declaration of a column WINS over an earlier one - which is the
    correct semantics for a migration history.
    """
    global _TYPES
    if _TYPES is None:
        from conftest import REPO_ROOT as root

        from alembic.config import Config
        from alembic.script import ScriptDirectory

        config = Config(str(root / "alembic.ini"))
        config.set_main_option("script_location", str(root / "backend" / "alembic"))
        script = ScriptDirectory.from_config(config)

        types: dict[tuple[str, str], dict[str, str]] = {}
        # `walk_revisions` yields head first, so this visits newest before
        # oldest; updating the dict each time means the OLDEST revision wins.
        # Sorted by revision id instead, so the newest wins and the result is
        # the same on every run regardless of how Alembic orders its walk.
        revisions = sorted(
            script.walk_revisions(), key=lambda rev: rev.revision
        )
        assert revisions, "no Alembic revisions were found"
        for migration in revisions:
            path = root / "backend" / "alembic" / Path(
                str(migration.path).replace("\\", "/")
            )
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "create_table"
                ):
                    continue
                schema = next(
                    (kw.value.value for kw in node.keywords if kw.arg == "schema"), None
                )
                name = node.args[0].value
                columns: dict[str, str] = {}
                for arg in node.args[1:]:
                    if not (
                        isinstance(arg, ast.Call)
                        and isinstance(arg.func, ast.Attribute)
                        and arg.func.attr == "Column"
                    ):
                        continue
                    columns[arg.args[0].value] = _render_type(arg.args[1])
                types[(schema, name)] = columns
        _TYPES = types
    return _TYPES


def _declared_type(schema: str, table: str, column: str) -> str | None:
    return _declared_types().get((schema, table), {}).get(column)


def _render_type(node: ast.AST) -> str:
    """Turn `sa.Numeric(precision=18, scale=4)` into 'numeric(18,4)'.

    Rendered in POSTGRESQL vocabulary, because that is what the type rules in
    db_constraints.yaml produce. Comparing the two directly means one
    translation rather than two, and it makes a mismatch mean what it says: the
    migration declared a different type, not a different spelling of it.
    """
    if not isinstance(node, ast.Call):
        return ast.unparse(node).strip().lower()
    base = _SA_TO_POSTGRES.get(node.func.attr.lower(), node.func.attr.lower())
    args: list = []
    for keyword in node.keywords:
        if keyword.arg in {"precision", "scale", "length"}:
            args.append(ast.literal_eval(keyword.value))
    for arg in node.args[1:]:
        if isinstance(arg, ast.Constant):
            args.append(arg.value)
    if not args:
        return base
    return f"{base}({','.join(str(a) for a in args)})"


# SQLAlchemy type name -> the PostgreSQL spelling the type rules use. Anything
# missing here would make this test fail on a spelling rather than a type, which
# is worse than not testing it.
_SA_TO_POSTGRES = {
    "biginteger": "bigint",
    "double": "double precision",
    "double_precision": "double precision",
    "float": "double precision",
    "datetime": "timestamp",
    "datetimetz": "timestamptz",
    "text": "text",
    "varchar": "text",
    "boolean": "boolean",
    "integer": "integer",
    "smallinteger": "smallint",
    "date": "date",
    "time": "time",
    "numeric": "numeric",
    "decimal": "numeric",
}


def _normalise(declared: str) -> str:
    """Collapse the last few spelling differences between the two sources.

    PostgreSQL reflects `timestamp without time zone` and `character varying`
    where the rules simply say `timestamp` and `text`. Same types, different
    vocabulary, and exactly the kind of difference that would make this test
    noisy enough to be ignored.
    """
    text = re.sub(r"\s+", " ", declared.strip().lower())
    text = text.replace("timestamp without time zone", "timestamp")
    text = text.replace("timestamp with time zone", "timestamptz")
    text = text.replace("character varying", "text")
    text = text.replace("numeric(", "numeric(").replace(", ", ",")
    return text
