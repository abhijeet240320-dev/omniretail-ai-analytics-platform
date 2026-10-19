"""Engine and session factory.

Built from the same `Settings` object the ETL, the loader and the scripts use, so
there is exactly one answer to "which database?" in the project. `database_url`
is a SQLAlchemy URL and is handed to SQLAlchemy unchanged, unlike the scripts
that talk to psycopg directly.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    """Process-wide engine.

    Created lazily rather than at import time so that importing the models (in a
    test, a notebook, or `alembic upgrade head`) does not require a reachable
    database.
    """
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,  # the cluster is restarted by hand during development
            future=True,
        )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _session_factory


def session_scope() -> Session:
    """A session for a single unit of work."""
    return get_session_factory()()