# Alembic

Schema migrations for the OmniRetail platform. One baseline revision, 30 tables,
five schemas.

## The ownership split

Three layers used to be able to create the same schema. Now only one can.

| Layer | Owns | Never does |
|---|---|---|
| `backend/scripts/db/bootstrap_db.py` | the database, the roles, the GRANTs, the read-only role's permissions | create tables |
| Alembic (`upgrade head`) | schemas, tables, columns, PK/UQ/FK constraints | load rows, create roles, GRANT |
| `backend/scripts/db/04_load.py` | the rows, and the evidence that the keys hold | **any DDL at all** |

The boundary is enforced, not merely documented:
`test_loader_issues_no_ddl` in `backend/tests/test_loader_alembic_split.py`
parses the loader's source and fails on any `CREATE TABLE`, `DROP TABLE`,
`ALTER TABLE`, `CREATE SCHEMA`, `DROP SCHEMA` or `CREATE INDEX`. That test was
itself broken once - it only read `ast.Constant`, and every SQL string in the
loader is an f-string, so a `CREATE TABLE` smuggled into it passed. It now reads
`ast.JoinedStr` too, and both cases have been negative-tested.

### What the loader does instead of creating tables

```
TRUNCATE <table> CASCADE          structure, columns and constraints untouched
\copy the CSV in
re-measure every declared constraint against the rows that are now loaded
```

`TRUNCATE` rather than `DROP TABLE` because the shape is not the loader's to
change. `CASCADE` because PostgreSQL refuses to truncate a table a foreign key
points at, and the load order deliberately fills parents before children.

The verification step is the interesting one. The constraints in
`db_constraints.yaml` are the SPEC - what was proposed. `pg_constraint` is the
FACT - what the migration created. The loader tests the facts against the rows
and records the verdict, so "46 constraints verified" is a fact about the data as
it is now rather than a claim inherited from whichever build first ran them.

Re-running that is cheap, because it never copies:

```bash
.venv/Scripts/python.exe backend/scripts/db/04_load.py --verify-only
```

Measured: **106 seconds** to re-measure all 46 constraints against 106,704,583
rows, with no COPY and no TRUNCATE.

## Commands

All of these must be run **from the repository root**. `alembic.ini` resolves
`script_location` against the current directory, not against the ini file.

```bash
.venv/Scripts/python.exe -m alembic current
.venv/Scripts/python.exe -m alembic history
.venv/Scripts/python.exe -m alembic upgrade head
.venv/Scripts/python.exe -m alembic downgrade base
.venv/Scripts/python.exe -m alembic upgrade head --sql   # offline, prints SQL
```

## The connection URL is not in `alembic.ini`

`env.py` reads it from `app.core.config.Settings`, which reads `.env` - the same
object the loader, the API and the analytics runner use. The `sqlalchemy.url` in
`alembic.ini` is a placeholder that is overwritten before any connection is made.

Because `Settings` is a `pydantic-settings` object, a real environment variable
beats the `.env` file. That is the supported way to aim a command somewhere else:

```bash
POSTGRES_DB=some_other_database .venv/Scripts/python.exe -m alembic upgrade head
```

## The baseline, and why it was stamped rather than run

`backend/scripts/db/04_load.py` created these tables by bulk load. Running
`alembic upgrade head` against that database would fail - the tables are already
there. So the sequence was:

1. Create an empty scratch database.
2. Point Alembic at it and autogenerate. Against an empty database Alembic emits
   `op.create_table()` for all 30 tables; against the loaded database the same
   command emits nothing at all.
3. Hand-add the `CREATE SCHEMA` block (Alembic 1.20.0 has no `create_schema` op).
4. `alembic upgrade head` against the scratch database, to prove the baseline
   actually builds.
5. `alembic stamp head` against the real database - records that the baseline
   describes what is already there, without running it.
6. Drop the scratch database.

**From now on, new tables go in migrations, not in the loader.** A new table
means: add it to `backend/app/models/` via the generator, write a revision, then
run `upgrade head`. `test_baseline_creates_exactly_the_modelled_tables` fails if
you do only the first.

## The workflow, end to end

```
bootstrap_db.py                  roles, database, schemas, GRANTs
        |
alembic upgrade head             tables, columns, constraints  (idempotent)
        |
04_load.py                       TRUNCATE + COPY + verify keys
        |
05_verify_integrity.py           independent re-derivation from the rows
        |
alembic current                  should already read (head)
        |
pytest                           models, migration and load agree
```

Measured on this machine:

| Step | Result |
|---|---|
| `alembic upgrade head` run 3x on an already-migrated database | exit 0, 0 output, no change |
| `downgrade base` then `upgrade head` on a scratch database | 30 tables -> 0 -> 30 tables; 29 PK / 2 UQ / 15 FK |
| `04_load.py` into an Alembic-only schema | rows in, `pk_dunnhumby_coupons` holds, candidate key on `coupon_redemptions` still rejected with 14 duplicates |
| `04_load.py --verify-only` on the loaded database | 46 constraints verified, 1 rejected, 106s, no COPY |
| `alembic revision --autogenerate` after loading | 0 ops |

`downgrade base` leaves the `analytics` schema alone if it holds anything. That
schema is created empty by this migration; the curated layer that belongs there
arrives in Phase 9 and will not be in any migration, so a bare `DROP SCHEMA`
would delete real analytics data on a routine rollback.

## When the loader refuses to run

It stops, rather than guessing, in three cases:

- **No `alembic_version` row.** The database was built by the old loader. Fix
  with `alembic stamp head` if the tables are already correct, or
  `alembic upgrade head` if it is genuinely empty.
- **Stamped at a revision that is not head.** Fix with `alembic upgrade head`.
- **The CSV header and the table disagree.** A column was added or renamed on
  one side. The loader will NOT reconcile them, because one of the two is wrong
  and it is not the loader's job to guess which.

It also refuses to write `_load_report.json` when any table failed. A partial
report that merges into a complete one looks exactly like a complete one, and a
report that cannot be trusted is worse than no report.

## What was verified, and how

Everything below was executed, not reasoned about. Re-run it with the commands
in the code blocks.

**The baseline builds the schema from nothing.** Against an empty
`omniretail_migration_build` database:

```
  dunnhumby    9 tables      constraints (pk, uq, fk, unvalidated): (29, 2, 15, 0)
  dataco       4 tables      alembic_version                      : [('baseline',)]
  olist       12 tables      constraints on dunnhumby.coupon_redemptions: 0
  m5           5 tables
  analytics    0 tables
  TOTAL       30 tables
```

Those constraint counts match `_load_report.json` exactly - 29 PK, 2 UQ, 15 FK,
all validated - and `dunnhumby.coupon_redemptions` genuinely has zero
constraints, which is the rejected candidate key staying rejected.

**It is structurally identical to the database the loader built.** Every column,
type, precision, scale, nullability flag, primary key, unique constraint and
foreign key was compared between the migration-built database and the loaded one:

```
TOTAL STRUCTURAL DIFFERENCES (columns+types, PK, UQ, FK): 0
```

**A fresh autogenerate is empty, in both directions of the round trip.**

- After `upgrade head` on the scratch database: `alembic revision --autogenerate`
  produced a revision whose `upgrade()` body is `pass`.
- After `stamp head` on the loaded database: the same command produced `pass`.

The scratch database was dropped afterwards. It is a build artefact, not
evidence; the evidence is that `pass`, and the constraint counts above.

## Read this before trusting an empty diff

`alembic revision --autogenerate` returning no ops does **not** mean the models
match the database.

Alembic 1.20.0's autogenerate compares unique constraints, indexes, foreign
keys and check constraints. It has **no primary-key comparator** - there is no
`compare_primary_key` anywhere in the package - so `create_primary_key` and
`drop_primary_key` can never appear in a diff for an existing table. Measured on
a scratch schema:

| Situation | What Alembic actually reports |
|---|---|
| Database has a PK, model dropped it | `modify_nullable` - **no** `drop_primary_key` |
| Database is keyless, model sets `primary_key=True` | `modify_nullable` - **no** `create_primary_key` |

`dunnhumby.coupon_redemptions` has no primary key because its candidate key
`(household_key, day, coupon_upc)` was tested against the real rows and
rejected: 14 rows genuinely repeat it. SQLAlchemy still gives the Core `Table` an
empty `PrimaryKeyConstraint()` because `table.primary_key` always exists. That
phantom is harmless - it renders no `PRIMARY KEY` clause, and reflecting the
keyless table from the database produces the same phantom on the other side of
the comparison.

The real consequence is the one in the table above: if somebody deleted a PK
from a model, autogenerate would stay silent. The authoritative checks are
`test_primary_keys_match` and `test_baseline_keys_match_the_models`.

## Deliberately not here

**Roles, the database, and GRANTs.** Those belong to
`backend/scripts/db/bootstrap_db.py`, which runs before Alembic. A migration
that granted to `omniretail_readonly` would abort on any database where that role
had not been created yet, turning a convenience into a startup ordering hazard.

**The `analytics` schema contents.** It is created (empty) by the baseline, but
`env.py` excludes it from autogenerate via `include_object`. When Phase 9 fills
it with curated objects that have no ORM models, an unfiltered autogenerate would
try to **DROP** them on a routine schema change. Delete that exclusion the day
`analytics` gets ORM-mapped tables.

## Layout

```
alembic.ini                          no credentials; run from the repo root
backend/alembic/env.py               Settings-driven URL, include_schemas, include_object
backend/alembic/script.py.mako       revision template (no post-write hooks)
backend/alembic/versions/            the baseline
backend/tests/test_migrations.py     the guards
```

`backend/tests/test_migrations.py` parses the baseline's AST rather than
executing it. Executing it needs a `CREATEDB` privilege the `omniretail` role
deliberately does not have, and the round trip in the section above is the
recorded evidence that it works.
