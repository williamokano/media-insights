"""Database engine/session helpers."""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

log = logging.getLogger(__name__)

# A table that has existed since the very first schema; if it's present
# without alembic_version, the DB predates migrations being wired in.
_SENTINEL_TABLE = "libraries"

# The one migration whose schema is guaranteed to match exactly what
# Base.metadata.create_all() used to produce, back before migrations were
# wired into the app (0.0.6 and earlier). A pre-existing database must be
# stamped at THIS revision, never at "head" -- head keeps moving forward as
# new migrations ship, and stamping past a migration a database never
# actually ran silently skips its DDL while claiming to be fully up to date.
_PRE_MIGRATION_SCHEMA_REVISION = "d0f4d45356ad"

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None

# run_migrations() gets called from every scan_library()/scan_all() entry
# point (the scanner is usable standalone, without api.app.configure()
# having run first), so it has to be safe to call often and concurrently.
# Alembic's Config/env.py execution isn't safe to run from multiple threads
# at once, and re-running it on every scan would be wasteful regardless --
# the schema only needs bringing up to date once per process lifetime.
_migration_lock = threading.Lock()
_migrated_urls: set[str] = set()


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


def _migrations_dir() -> Path:
    return Path(__file__).resolve().parent / "migrations"


def _missing_columns(url: str) -> list[tuple[str, str]]:
    """Compare Base.metadata's tables/columns against what physically exists.

    Used as a defense-in-depth check after upgrading: a database that was
    ever stamped straight to "head" by the old (buggy) version of this
    function claims to be fully migrated in alembic_version while actually
    missing columns added by migrations after that mistake.
    """
    from media_insights.models import Base

    eng = create_engine(url, future=True)
    try:
        inspector = inspect(eng)
        existing_tables = set(inspector.get_table_names())
        missing: list[tuple[str, str]] = []
        for table in Base.metadata.tables.values():
            if table.name not in existing_tables:
                continue  # a whole missing table is upgrade(head)'s job, not this check's
            physical_columns = {c["name"] for c in inspector.get_columns(table.name)}
            missing.extend((table.name, column.name) for column in table.columns if column.name not in physical_columns)
        return missing
    finally:
        eng.dispose()


def run_migrations(url: str) -> None:
    """Bring the schema up to date, replacing the old create_all()-only setup.

    Handles two starting points:
      - a brand-new database (no tables at all): runs every migration from
        scratch, same as create_all() used to.
      - a database created before migrations were wired in (tables exist via
        create_all(), no alembic_version table): its schema matches exactly
        _PRE_MIGRATION_SCHEMA_REVISION -- not necessarily "head", which keeps
        moving forward as new migrations ship -- so we stamp it there, then
        let the upgrade below carry it forward through anything since.

    From here on, `alembic upgrade head` is the only thing that ever changes
    the schema, so a database on either starting point converges to the same
    place and stays correct across future column/table additions.

    Idempotent and thread-safe: only the first call for a given URL in this
    process actually touches Alembic; later calls (including concurrent
    ones, e.g. two libraries' scans starting at once) return immediately.
    """
    with _migration_lock:
        if url in _migrated_urls:
            return

        from alembic import command
        from alembic.config import Config as AlembicConfig

        eng = create_engine(url, future=True)
        try:
            existing_tables = set(inspect(eng).get_table_names())
        finally:
            eng.dispose()

        cfg = AlembicConfig()
        cfg.set_main_option("script_location", str(_migrations_dir()))
        cfg.set_main_option("sqlalchemy.url", url)
        cfg.attributes["configure_logger"] = False

        if _SENTINEL_TABLE in existing_tables and "alembic_version" not in existing_tables:
            command.stamp(cfg, _PRE_MIGRATION_SCHEMA_REVISION)
            log.info("pre-existing database stamped at the initial schema revision")

        command.upgrade(cfg, "head")
        log.info("database migrations applied (head)")

        # One-time repair for databases an older, buggy build of this
        # function already stamped straight to head (see CHANGELOG 0.0.9):
        # alembic_version claims head, so the upgrade above was a no-op, but
        # the physical schema is missing whatever that mistake skipped.
        missing = _missing_columns(url)
        if missing:
            log.warning(
                "schema drift detected after upgrade (missing columns: %s) -- "
                "re-anchoring to the initial revision and replaying migrations for real",
                missing,
            )
            command.stamp(cfg, _PRE_MIGRATION_SCHEMA_REVISION)
            command.upgrade(cfg, "head")
            still_missing = _missing_columns(url)
            if still_missing:
                raise RuntimeError(f"schema repair failed, still missing columns: {still_missing}")
            log.info("schema drift repaired")

        _migrated_urls.add(url)


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
    _migrated_urls.clear()
