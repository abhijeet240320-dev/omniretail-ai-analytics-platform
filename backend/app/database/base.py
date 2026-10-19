"""Declarative base and connection plumbing for the OmniRetail models.

WHY THERE IS BARELY ANY NAMING CONVENTION HERE
----------------------------------------------
The obvious approach is to teach SQLAlchemy the grammar `backend/load/loader.py`
used - `pk_<schema>_<table>`, `fk_<schema>_<table>_<column>` - so constraint
names could be derived rather than stated. That does not work.

SQLAlchemy's naming convention has no `schema` token. The available tokens are
`table_name`, `constraint_name`, `column_X_key`, `column_X_name`,
`column_X_label`, `referred_table_name` and `referred_column_X_name`. Writing
`%(schema)s` raises KeyError at import time rather than degrading quietly, which
is at least honest, but it means the grammar simply cannot be expressed here.

So the generated models do not infer constraint names at all. The generator reads
each constraint's real name out of `pg_constraint` and writes it onto the model
explicitly. That is strictly better than a convention: a convention can drift
from the database, whereas a name read from the catalog cannot. If somebody
renames a constraint in PostgreSQL, the next generator run follows the rename
instead of quietly disagreeing with it.

The unique constraints made this unavoidable anyway - they are numbered
sequentially (`uq_m5_calendar_1`) and say nothing about which columns they cover.

The models themselves are GENERATED, not hand-written - see
`backend/scripts/db/06_generate_models.py`. They are checked in, because
application code should not need a live database to import, but they are derived
from the catalog so they cannot quietly disagree with it.
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Only indexes are named by convention; every primary key, unique constraint and
# foreign key carries the name the database actually gave it.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for every OmniRetail table.

    `schema` is deliberately NOT set here. Each table declares its own schema,
    because the whole point of the layer is that a model belongs to exactly one
    source company - there is no shared default that could quietly merge them.
    """

    metadata = MetaData(naming_convention=NAMING_CONVENTION)