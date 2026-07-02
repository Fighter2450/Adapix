"""Database engine + session management."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .models import Base


_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def _build_engine(url: str) -> Engine:
    """Create the right SQLAlchemy engine for the given DATABASE_URL.

    Handles two quirks:
    - Railway (and Heroku) emit `postgres://` URLs; SQLAlchemy 2.x only
      accepts `postgresql://`.
    - SQLite needs check_same_thread=False; Postgres needs pool_pre_ping
      so stale connections are detected after a Railway sleep/restart.
    """
    # Normalise Railway/Heroku postgres:// → postgresql://
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]

    if url.startswith("sqlite"):
        return create_engine(
            url,
            future=True,
            connect_args={"check_same_thread": False},
        )
    # PostgreSQL
    return create_engine(
        url,
        future=True,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )


def get_engine(settings: Settings | None = None) -> Engine:
    global _engine
    if _engine is None:
        s = settings or Settings()
        _engine = _build_engine(s.database_url)
    return _engine


# Additive columns that `create_all` won't add to an already-existing table.
# Keep this list in sync when you add a nullable column to an existing model.
_ADDITIVE_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "organizations": [
        ("vapi_phone_number_id", "VARCHAR(128)"),
        ("phone_number", "VARCHAR(32)"),
        ("phone_status", "VARCHAR(32) DEFAULT 'none'"),
    ],
    "email_connections": [
        ("smtp_host", "VARCHAR(255)"),
        ("smtp_port", "INTEGER"),
        ("smtp_password", "TEXT"),
    ],
}


def _run_additive_migrations(engine: Engine) -> None:
    """Lightweight, dependency-free migration: ADD COLUMN for any model column
    missing from an existing table. Works on SQLite + Postgres. Idempotent."""
    insp = inspect(engine)
    for table, cols in _ADDITIVE_COLUMNS.items():
        if not insp.has_table(table):
            continue
        existing = {c["name"] for c in insp.get_columns(table)}
        missing = [(n, ddl) for n, ddl in cols if n not in existing]
        if not missing:
            continue
        with engine.begin() as conn:
            for name, ddl in missing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


def init_db(settings: Settings | None = None) -> None:
    """Create all tables + apply additive column migrations. Idempotent."""
    engine = get_engine(settings)
    Base.metadata.create_all(bind=engine)
    _run_additive_migrations(engine)


@contextmanager
def get_session(settings: Settings | None = None) -> Iterator[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(settings), expire_on_commit=False)
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
