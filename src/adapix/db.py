"""Database engine + session management."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .models import Base


_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def get_engine(settings: Settings | None = None) -> Engine:
    global _engine
    if _engine is None:
        s = settings or Settings()
        _engine = create_engine(s.database_url, future=True)
    return _engine


def init_db(settings: Settings | None = None) -> None:
    """Create all tables. Idempotent."""
    Base.metadata.create_all(bind=get_engine(settings))


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
