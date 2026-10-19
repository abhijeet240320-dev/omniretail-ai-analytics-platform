"""api_keys: credential storage for the FastAPI layer

Revision ID: api_keys
Revises: baseline
Create Date: 2026-10-03 21:00:00+00:00

WHY A NEW SCHEMA RATHER THAN A TABLE IN `public`
-----------------------------------------------
`public` holds only `alembic_version`. The four source schemas hold data from
four external publishers and are documented in `docs/data_dictionary.md` as
faithful copies of those sources. Adding an application table to any of them
would put something in the warehouse that no publisher supplied, and a reader
auditing "what came from Olist?" would find an API credential table in it.

`app` is a fifth schema that holds nothing but the API's own infrastructure.
It is empty except for this table.

WHY MIGRATIONS AND NOT A BOOTSTRAP SCRIPT
-----------------------------------------
The ownership split (see `backend/alembic/README.md`) is: bootstrap owns roles,
Alembic owns the schema, the loader owns rows. A credential table is schema, so
it belongs here. `bootstrap_db.py` deliberately grants nothing about it - it
cannot know which roles the API will have.

The GRANT that makes it readable by the API role is at the bottom, and it is
SELECT only. See the comment there for why that is not a weakening of the
read-only guarantee.

KEY STORAGE
-----------
Only an Argon2id hash is stored. The plaintext key exists exactly once, when
`backend/scripts/db/08_issue_api_key.py` prints it, and is never recoverable
afterwards. That is the point: a database dump does not yield usable
credentials.

There is no `key_hash` unique index because the hash is salted - two identical
keys would produce different hashes. Uniqueness is on `key_prefix`, which is the
first 12 characters of the PUBLIC key and is what makes a lookup O(1) instead of
verifying a hash against every row.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "api_keys"
down_revision = "baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS app")

    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        # The public, non-secret half of the key. Shown in the UI, used to find
        # the row without hashing every candidate.
        sa.Column("key_prefix", sa.String(length=16), nullable=False),
        # Argon2id. Nullable=False because an unhashed key is a defect, not a
        # convenience - there is no code path that writes NULL here.
        sa.Column("key_hash", sa.String(length=255), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        # Comma-separated scopes. A single string rather than a join table
        # because the list is short, never queried by scope, and a join table
        # would add a second thing to migrate for no query benefit.
        sa.Column("scopes", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.String(length=120), nullable=True),
        # schema= MUST come last: every argument above it is positional, and a
        # keyword argument in the middle is a SyntaxError. Without it Alembic
        # creates the table in the connection's default schema (`public`), and
        # then the COMMENT, the GRANT and the downgrade all fail because the
        # table is not where this migration says it is.
        schema="app",
    )

    op.create_index(
        "ix_api_keys_prefix", "api_keys", ["key_prefix"], schema="app", unique=True
    )
    op.create_index("ix_api_keys_active", "api_keys", ["is_active"], schema="app")

    op.execute(
        "COMMENT ON SCHEMA app IS "
        "'Application infrastructure. Not a data source: no row here came from "
        "olist, dataco, dunnhumby or m5.'"
    )
    op.execute(
        "COMMENT ON TABLE app.api_keys IS "
        "'Argon2id hashes of API keys. The plaintext is shown once at issue time "
        "and is not recoverable.'"
    )

    # The API runs as omniretail_readonly, so it must be able to READ this table
    # to authenticate at all. This is the one place that role is granted
    # anything outside the four source schemas.
    #
    # TWO grants are needed, and forgetting the first is a mistake worth naming:
    # SELECT on the table is useless without USAGE on the SCHEMA. The error is
    # `permission denied for schema app`, not `permission denied for table
    # api_keys`, so it reads like a schema problem when it is a missing grant.
    #
    # This does not weaken the read-only guarantee, and the reasoning matters:
    # the grant is SELECT only, and what SELECT reveals is a salted hash, which
    # is not a credential. An attacker who reads this table gains the ability to
    # brute-force candidate keys offline - which is exactly what Argon2's cost
    # parameters exist to make expensive, and why they are set rather than left
    # at the library's fastest setting.
    #
    # The alternative - a separate auth role with its own credential - adds a
    # second connection pool and a second password for no security gain here,
    # because the rows are hashes either way.
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omniretail_readonly') THEN "
        "EXECUTE 'GRANT USAGE ON SCHEMA app TO omniretail_readonly'; "
        "EXECUTE 'GRANT SELECT ON app.api_keys TO omniretail_readonly'; "
        "END IF; END $$;"
    )


def downgrade() -> None:
    # Ordered deliberately: the table's indexes and grants go with it, then the
    # schema. Dropping the schema first would cascade the table anyway, but
    # leaving an index behind in a dropped schema is confusing to read in
    # pg_catalog.
    op.execute("DROP TABLE IF EXISTS app.api_keys CASCADE")
    # DROP SCHEMA removes the schema-level USAGE grant with it, so there is
    # nothing else to revoke first.
    op.execute("DROP SCHEMA IF EXISTS app CASCADE")