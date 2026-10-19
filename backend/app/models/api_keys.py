"""`app.api_keys` - the application's own table, in the `app` schema.

It has its OWN declarative base, and that is load-bearing, not decoration.
The reason is a SQLAlchemy landmine worth writing down, because the symptom
is wildly disconnected from the cause.

WHAT WENT WRONG
---------------
Every generated source model refers to its relationships with module-qualified
strings, e.g. `Mapped[list["app.models.olist.orders"]]`, because table names
repeat across sources (`olist.orders` and `dataco.orders` are unrelated
tables). When `configure_mappers()` resolves the dotted name it walks the
class registry token by token: `app` -> `models` -> `olist` -> `orders`.

SQLAlchemy resolves the FIRST token against `MetaData._schemas` FIRST, and only
falls through to the class registry if that misses. `MetaData._add_table` adds
every table's schema name to `_schemas`, so as soon as a table lives in a
schema whose name is also a package name, `app` resolves to a
`_GetTable('app', metadata)` object instead of the `app` package. The walk
then does `getattr(_GetTable, 'models')`, which raises
`KeyError: 'app.models'`, and every mapper in the project fails to configure:

    InvalidRequestError: When initializing mapper Mapper[customers(customers)],
    expression 'app.models.olist.orders' failed to locate a name

Registering `app.api_keys` on `Base.metadata` is therefore enough to break all
30 generated models. Keeping this table on a separate `AppBase` keeps
`Base.metadata._schemas` equal to `{olist, dataco, dunnhumby, m5}` - the four
source schemas - and the module-qualified names resolve through the class
registry as they always did.

The alternative - renaming every generated class to be schema-prefixed so bare
names could be used - would also work, but it rewrites 30 classes and their
generator for a problem that one extra twelve-line base solves.

WHY IT IS MODELLED AT ALL
-------------------------
Alembic's autogenerate compares the LIVE database against the target metadata.
When this migration landed (`20261003_2100_api_keys`) the table existed in the
database but in no model, so autogenerate proposed `remove_index` for
`ix_api_keys_prefix` on every run.

That is the "drift alarm" working, and it was right to complain: the schema was
managed by Alembic but invisible to the ORM, so anything that introspects the
metadata - including `test_models_match_catalog.py` and
`test_loader_alembic_split.py` - saw a database with a table it could not
account for.

Modelling it makes `alembic revision --autogenerate` produce an empty
migration again, which is the property those two tests assert. Because it lives
on its own `MetaData`, `alembic/env.py` passes BOTH:

    target_metadata = [Base.metadata, AppBase.metadata]

Alembic accepts a sequence of `MetaData` objects and iterates it internally
(`AutogenContext` calls `util.to_list(self.metadata)`), so both schemas are
compared and the diff is still empty.

WHY IT IS NOT IN THE GENERATED SOURCE MODELS
--------------------------------------------
`backend/scripts/db/06_generate_models.py` generates models for the four SOURCE
schemas only. The `app` schema is application infrastructure, not loaded data,
so it is written by hand here and never regenerated over. `from_models` in the
test suite iterates `SOURCE_SCHEMAS`, so this table is outside what the
generator and the catalog comparison are about - deliberately, and now
consistently.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, MetaData, String, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.database.base import NAMING_CONVENTION


class AppBase(DeclarativeBase):
    """Declarative base for the `app` schema ONLY.

    Splitting this out of `Base` is what keeps the schema name `app` out of
    `Base.metadata._schemas`. See the module docstring for why that matters
    more than it looks.
    """

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class ApiKey(AppBase):
    """One issued API key. The plaintext is never stored.

    EVERY type here mirrors the migration exactly. That is not slavishness:
    Alembic's autogenerate compares the live database against this metadata, and
    any difference here is reported as `modify_type` drift - which is exactly
    the alarm `test_autogenerate_finds_nothing_to_do` exists to keep silent.

    The first draft declared `Text` for the hash, an `ARRAY(String(64))` for the
    scopes and `DateTime(timezone=True)` for the timestamps. All three are
    tidier than what the migration actually creates (`varchar(255)`, a
    comma-separated `varchar(255)`, and `timestamp without time zone`), and all
    three showed up as drift.

    The migration is the authority because the migration is what ran.
    """

    __tablename__ = "api_keys"
    __table_args__ = (
        Index("ix_api_keys_prefix", "key_prefix", unique=True),
        Index("ix_api_keys_active", "is_active"),
        {
            "schema": "app",
            # The comment the MIGRATION set, verbatim. The first draft wrote a
            # tidier one ("Issued API keys for the read-only API"), and
            # autogenerate reported `add_table_comment` on every run - a table
            # comment is part of the schema as far as Alembic is concerned, and
            # the migration is what ran.
            "comment": (
                "Argon2id hashes of API keys. The plaintext is shown once at "
                "issue time and is not recoverable."
            ),
        },
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # The first 12 characters of the key, used for lookup and for display. NOT
    # a security boundary: it is stored in the clear because it has to be, and
    # an attacker who has it learns nothing they do not already have.
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    # Argon2id hash of the full key. The plaintext is printed once at issue
    # time and never persisted, so a lost key is replaced, not recovered.
    # An Argon2id encoded hash is ~97 characters, so varchar(255) is ample.
    key_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    # A comma-separated list, e.g. `admin:read,ai:read`. Stored as text rather
    # than a PostgreSQL array because `parse_scopes` already normalises it and
    # the read path splits it; an array type would buy nothing and would make
    # the column's empty-value representation a second thing to get wrong.
    scopes: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    # Naive timestamps, matching the rest of this database and the migration.
    # UTC is written at the application layer, which is where it is also read.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, server_default=text("now()")
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime())
    created_by: Mapped[str | None] = mapped_column(String(120))