"""Database engine/session helpers."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from media_insights.models import Base

log = logging.getLogger(__name__)

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def init_engine(url: str) -> Engine:
    """Create the engine, with SQLite-friendly pragmas when applicable."""
    global _engine, _SessionLocal

    if url.startswith("sqlite:///"):
        path = Path(url[len("sqlite:///") :])
        path.parent.mkdir(parents=True, exist_ok=True)

    connect_args: dict = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    _engine = create_engine(url, future=True, connect_args=connect_args, pool_pre_ping=True)

    if url.startswith("sqlite"):
        # WAL = many readers, one writer; ideal for a media server.
        # busy_timeout matters just as much: without it, a writer that finds
        # the database locked fails immediately instead of waiting, and the
        # scanner easily holds a write lock for a moment while probing files.
        @event.listens_for(_engine, "connect")
        def _set_sqlite_pragmas(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA temp_store=MEMORY")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()

    _SessionLocal = sessionmaker(_engine, expire_on_commit=False, autoflush=False)
    log.info("engine initialised url=%s", url)
    return _engine


def ensure_schema() -> None:
    if _engine is None:
        raise RuntimeError("init_engine() must be called before ensure_schema()")
    Base.metadata.create_all(_engine)


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    if _SessionLocal is None:
        raise RuntimeError("init_engine() must be called before get_session()")
    sess = _SessionLocal()
    try:
        yield sess
    finally:
        sess.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Context manager for non-FastAPI code (CLI, watcher, scanner)."""
    if _SessionLocal is None:
        raise RuntimeError("init_engine() must be called before session_scope()")
    sess = _SessionLocal()
    try:
        yield sess
        sess.commit()
    except Exception:
        sess.rollback()
        raise
    finally:
        sess.close()


def engine() -> Engine:
    if _engine is None:
        raise RuntimeError("init_engine() must be called before engine()")
    return _engine


def reset_for_tests() -> None:
    """Used by the test suite to start clean."""
    global _engine, _SessionLocal
    _engine = None
    _SessionLocal = None
