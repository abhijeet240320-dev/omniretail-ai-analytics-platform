# Database

## 1. Where the database lives, and why

| | |
|---|---|
| **Cluster data directory** | `~/.omniretail/pgdata` (i.e. `C:\Users\<you>\.omniretail\pgdata`) |
| **Host / port** | `127.0.0.1:5433` |
| **PostgreSQL version** | 18.6 |
| **Superuser** | `postgres` (loopback `trust`, used only for administration) |
| **Application roles** | `omniretail` (read/write), `omniretail_readonly` (read-only, for the AI) |
| **Database** | `omniretail` |
| **Managed by** | `backend/scripts/db/pg_ctl.sh` |

### Why not the machine's existing PostgreSQL on port 5432

There *is* a system-wide PostgreSQL 18 service on port 5432. We do not use it,
for three reasons: we do not have its superuser password; the project would then
depend on the state of a global instance; and a project-local cluster is
disposable and reproducible. Port **5433** keeps the two completely separate, so
nothing this project does can disturb anything else on the machine.

### Why the cluster is NOT inside the project folder

This is a Windows-specific hazard worth recording, because it cost real debugging
time and will recur for anyone working inside a synced folder.

A PostgreSQL data directory must be opened with OS-level exclusive sharing
semantics. OneDrive (and antivirus real-time scanning) hold handles on files
inside synced folders. The observed failure was:

```
LOG: could not open file "./server.log": sharing violation
LOG: You might have antivirus, backup, or similar software interfering
     with the database system.
```

The postmaster came up and listened on the port, but every client hung and timed
out. Moving the cluster to the local disk (outside OneDrive) fixed it
permanently. The repository's `.gitignore` also guards against a cluster being
initialised inside the project by accident.

### The `pg_ctl` trap

After moving the cluster, a *second* failure appeared:

```
LOG: client backend (PID n) was terminated by exception 0xC0000142
```

`0xC0000142` is `STATUS_DLL_INIT_FAILED`. The postmaster started and reported
"ready to accept connections", but **every backend it forked — and every
autovacuum worker — died during DLL initialisation**, so each client waited until
its connection timed out. A cluster created from the *same binaries* but launched
by running `postgres.exe` **directly** works perfectly.

The conclusion is narrow and practical: **on this machine, `pg_ctl start` cannot
be used**. `backend/scripts/db/pg_ctl.sh` therefore launches `postgres.exe`
itself, in the background, and then polls `pg_isready` until the server genuinely
answers. `pg_ctl stop` is still used for shutdown, because stopping only signals
the running postmaster and never forks anything.

The script also fixed a related performance trap: it now binds
`listen_addresses=127.0.0.1,::1`. Previously the server bound IPv4 only, so the
hostname `localhost` (which resolves to `::1` first on Windows) made **every**
connection wait out the full connection timeout before falling back to IPv4 —
which presented as a hang rather than an error.

## 2. Starting, stopping and checking

```bash
# create the cluster (once)
bash backend/scripts/db/pg_ctl.sh init

# daily use
bash backend/scripts/db/pg_ctl.sh start
bash backend/scripts/db/pg_ctl.sh status
bash backend/scripts/db/pg_ctl.sh stop

# create roles, database, schemas and grants (idempotent)
.venv/Scripts/python.exe backend/scripts/db/bootstrap_db.py

# verify both roles, including that the AI role cannot write
.venv/Scripts/python.exe backend/scripts/db/check_db.py
```

`check_db.py` is the gate. It must print `RESULT: DATABASE READY` before any
loading or API work is meaningful.

## 3. Schemas

One schema per source. The reasoning is in `docs/data_sources.md` §6: the four
datasets are four unrelated organisations, so their identifier spaces must never
be joined.

```
omniretail
├── dunnhumby   -- grocery: households, transactions, products, promotions, coupons
├── dataco      -- supply chain: orders, shipping, profitability
├── olist       -- e-commerce: orders, items, payments, reviews, sellers, geo
├── m5          -- forecasting: sales, calendar, prices
└── analytics   -- curated cross-source layer; every table carries source_system
```

`analytics` is the **only** schema permitted to reference more than one source,
and even there a cross-source identifier join is forbidden. Only genuinely
comparable *aggregate measures* may sit side by side, always labelled with their
`source_system`.

## 4. Roles and the security model

Two roles, from day one — before the AI analyst exists.

| Role | Purpose | Privileges |
|---|---|---|
| `omniretail` | ETL, migrations, writing analytics outputs | owns the schemas; full DDL/DML |
| `omniretail_readonly` | used **only** by the AI natural-language layer | `CONNECT`, `USAGE`, `SELECT` only |

The read-only role exists as **defence in depth**. The AI layer will validate
generated SQL against a blocklist, but validation is *our code*, and our code can
have bugs. So a second, independent control sits underneath it in the database:

1. **No write privilege.** `REVOKE CREATE` on schemas and database;
   `GRANT SELECT` only; no `INSERT`/`UPDATE`/`DELETE`/`TRUNCATE`/`REFERENCES`/
   `TRIGGER`.
2. **`default_transaction_read_only = on`.** Every session opened as that role
   starts read-only. Even a mistaken future `GRANT` does not enable writes,
   because the write would fail with *"cannot execute ... in a read-only
   transaction"*.
3. **`NOSUPERUSER`, `NOCREATEDB`, `NOCREATEROLE`, `NOBYPASSRLS`.**
4. **`ALTER DEFAULT PRIVILEGES`** so tables created *later* by migrations are
   automatically readable — closing the classic production bug where a new table
   is silently invisible to the reporting role.

### This is verified, not asserted

`03_schemas_and_grants.sql` ends with a `DO` block that scans
`information_schema.role_table_grants` and **raises an exception** if the
read-only role holds any write privilege. `check_db.py` goes further and actually
**attempts a `CREATE TABLE` as the read-only role**, requiring the database to
reject it:

```
[PASS] connected as 'omniretail_readonly' to 'omniretail'
[PASS] default_transaction_read_only = on
[PASS] write rejected: 'cannot execute ... in a read-only transaction'
```

A passing security test is evidence. "We granted SELECT only" is a claim.

## 5. Bootstrap file order, and why it is this order

| Step | File | Connects to | Why here |
|---|---|---|---|
| 1 | `01_roles.sql` | `postgres` | Roles are cluster-level; the database cannot grant to a role that does not exist yet |
| 2 | `02_database.sql` | `postgres` | Needs the owner role from step 1; must run **alone** (see below) |
| 3 | `03_schemas_and_grants.sql` | `omniretail` | Runs *inside* the new database so `ALTER DEFAULT PRIVILEGES` binds to it |

**`02_database.sql` contains exactly one statement.** PostgreSQL treats a
multi-statement simple query as a single implicit transaction, and `CREATE
DATABASE` cannot run in a transaction block — so `CREATE DATABASE ...; COMMENT ON
DATABASE ...;` fails with *"cannot run inside a transaction block"* even in
autocommit mode. The `COMMENT ON DATABASE` therefore lives in step 3.

**Idempotency.** `CREATE ROLE`/`CREATE SCHEMA` use existence checks; `CREATE
DATABASE` cannot be made conditional in plain SQL, so the check lives in
`bootstrap_db.py` (it queries `pg_database` and skips the step). Re-running the
bootstrap is safe.

**Passwords never appear in SQL or on a command line.** They are read from the
same `app.core.config` object the application uses, and spliced in as properly
escaped literals using psycopg's own quoting. They are not bind parameters
because PostgreSQL accepts `$1`-style parameters only in DML, not in utility
statements like `ALTER ROLE` — a detail that fails confusingly if you try it.

## 6. Local authentication

`pg_hba.conf` is generated by `pg_ctl.sh` (run `pg_ctl.sh hba` to rewrite it):

```
local   all   postgres                        trust
host    all   postgres  127.0.0.1/32          trust
host    all   all       127.0.0.1/32          scram-sha-256
```

The superuser keeps `trust` because this cluster is loopback-only and exists for
local development; administration and bootstrap must work without a password.
**Every other role must present a real password.** This matters: if loopback were
`trust` for everyone, the read-only role's password would be decorative and a bug
in the AI layer could only ever be caught by privileges, never by authentication.

> This is a **local development** configuration. It is not a deployment pattern:
> a reachable server would need `scram-sha-256` (or better) for all roles plus
> TLS, and the cluster would not sit on a developer's desktop.

## 7. Reproducing the database from nothing

```bash
bash backend/scripts/db/pg_ctl.sh init
bash backend/scripts/db/pg_ctl.sh start
.venv/Scripts/python.exe backend/scripts/db/bootstrap_db.py
.venv/Scripts/python.exe -m alembic upgrade head     # creates the 30 tables
.venv/Scripts/python.exe backend/scripts/db/04_load.py        # loads the rows
.venv/Scripts/python.exe backend/scripts/db/05_verify_integrity.py
.venv/Scripts/python.exe backend/scripts/db/check_db.py     # expect DATABASE READY
```

`bootstrap_db.py` creates the roles, the database, the schemas and the grants.
The tables come from Alembic, not from it — see section 8. The rows come from the
loader — see section 9.

To start over completely:

```bash
bash backend/scripts/db/pg_ctl.sh stop
rm -rf ~/.omniretail/pgdata
```

## 8. Migrations

Alembic owns the **tables**. `backend/scripts/db/bootstrap_db.py` owns the
**roles, the database and the grants**. `backend/scripts/db/04_load.py` owns the
**rows**. Three layers used to be able to create the same schema; now only one
can, and `test_loader_issues_no_ddl` fails the build if the loader starts
issuing DDL again.

```bash
.venv/Scripts/python.exe -m alembic current
.venv/Scripts/python.exe -m alembic upgrade head
.venv/Scripts/python.exe -m alembic revision --autogenerate -m "..."
```

All of these run from the repository root — `alembic.ini` resolves
`script_location` against the current directory, not against itself.

There is exactly one revision, `baseline`, which creates all 30 tables across the
four source schemas. It was generated by autogenerate against an **empty scratch
database** (against the loaded one, autogenerate emits nothing, because the
models and the database already agree), and then **stamped** rather than run
against the real database, because the loader had already created the tables.

Verified by building it: `alembic upgrade head` against an empty database
produces 30 tables, 29 primary keys, 2 unique constraints, 15 foreign keys, all
validated, with `dunnhumby.coupon_redemptions` holding zero constraints — matching
`_load_report.json` exactly. A column-by-column, key-by-key comparison against
the loaded database found **0 structural differences**, and a fresh autogenerate
against both databases produced an empty migration.

`upgrade head` is idempotent: run three times against an already-migrated
database, it exits 0 and changes nothing. `downgrade base` takes the schema from
30 tables to 0 and `upgrade head` puts it back with all 46 constraints intact.
Full detail, including how to reproduce it, is in
[backend/alembic/README.md](../backend/alembic/README.md).

One caveat worth knowing: **an empty autogenerate diff does not mean the models
match the database.** Alembic 1.20.0 has no primary-key comparator at all, so a
primary key added or removed in a model is invisible to it. The authoritative
key checks are `test_primary_keys_match` and `test_baseline_keys_match_the_models`.

## 9. Loading

The loader no longer creates tables. It `TRUNCATE`s them, `COPY`s the data in,
and then re-measures every constraint the migration declared against the rows
that are actually there:

```bash
.venv/Scripts/python.exe backend/scripts/db/04_load.py              # full load
.venv/Scripts/python.exe backend/scripts/db/04_load.py --resume     # skip COPYs that are already right
.venv/Scripts/python.exe backend/scripts/db/04_load.py --verify-only # never COPY; just re-measure keys
```

`--verify-only` is the cheap one: **106 seconds** to re-measure all 46
constraints against 106,704,583 rows, with no COPY and no TRUNCATE, so it cannot
destroy data by construction.

It stops rather than guessing when the database is not migrated, when it is
stamped somewhere other than head, or when a CSV header and its table disagree.

## 10. Known limitations

- No TLS; acceptable only because the cluster binds to loopback.
- No automated backups — a deliberate omission for a disposable development
  cluster, but it would be the first thing to add for anything real.
- The loader verifies constraints but does not *repair* them. If a constraint
  stops holding, the report says so and the fix is a data problem, not a schema
  one.
- Alembic cannot compare primary keys (section 8), so the project carries its own
  tests for that. This is a real gap in the tool, not a configuration choice.
- The cluster is machine-specific and is not containerised; Docker is not
  installed on this machine, so the setup is documented as local rather than
  pretending a container exists.
