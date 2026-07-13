"""Migration wiring regression coverage.

Before this, the app only ever called Base.metadata.create_all() at
startup, which creates missing tables but never alters existing ones.
Alembic migrations existed in the repo but were never actually invoked by
the running application -- meaning the moment a future release added a
column to an existing table, every already-running deployment would start
failing with "no such column" on upgrade, with no recovery path.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from media_insights.db import reset_for_tests, run_migrations


@pytest.fixture(autouse=True)
def _reset():
    reset_for_tests()
    yield
    reset_for_tests()


def test_run_migrations_creates_full_schema_on_empty_db(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path}/test.db"
    run_migrations(url)

    eng = create_engine(url)
    tables = set(inspect(eng).get_table_names())
    track_columns = {c["name"] for c in inspect(eng).get_columns("tracks")}
    eng.dispose()

    assert {"libraries", "media_items", "seasons", "media_files", "tracks", "change_events"} <= tables
    assert "alembic_version" in tables
    assert "language_raw" in track_columns


def test_language_raw_backfill_copies_existing_language_value(tmp_path: Path) -> None:
    """Rows created before the language_raw column existed should end up
    with language_raw == their old language value on upgrade -- the best
    available backfill (see the migration's docstring and
    scanner/service.py for why this isn't a perfect reconstruction). The
    migration must copy, not renormalize: language stays 'jpn' here, not
    'ja' -- renormalization only happens on the next re-probe.
    """
    from alembic import command
    from alembic.config import Config as AlembicConfig

    from media_insights.db import _migrations_dir

    url = f"sqlite:///{tmp_path}/test.db"
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(_migrations_dir()))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "d0f4d45356ad")  # schema as it existed before language_raw

    eng = create_engine(url)
    with eng.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO tracks (file_id, position, kind, language, "
                "is_default, is_forced, is_sdh, is_external) "
                "VALUES (1, 0, 'audio', 'jpn', 0, 0, 0, 0)"
            )
        )
        conn.commit()
    eng.dispose()

    command.upgrade(cfg, "head")

    eng = create_engine(url)
    with eng.connect() as conn:
        row = conn.execute(text("SELECT language, language_raw FROM tracks")).fetchone()
        assert row is not None
        assert row[0] == "jpn"
        assert row[1] == "jpn"
    eng.dispose()


def test_run_migrations_is_idempotent(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path}/test.db"
    run_migrations(url)
    run_migrations(url)  # must not raise, must not try to re-create tables


def test_run_migrations_stamps_pre_existing_database_instead_of_recreating(tmp_path: Path) -> None:
    """Simulates every database created before migrations were wired in:
    tables exist (from the old Base.metadata.create_all() codepath), but
    there's no alembic_version table. run_migrations() must detect this and
    stamp the DB as up to date rather than replay CREATE TABLE statements
    that would collide with tables that already exist.
    """
    from media_insights.models import Base

    url = f"sqlite:///{tmp_path}/test.db"
    eng = create_engine(url)
    Base.metadata.create_all(eng)  # the old, pre-migration startup path
    tables_before = set(inspect(eng).get_table_names())
    assert "alembic_version" not in tables_before
    eng.dispose()

    run_migrations(url)  # must not raise "table already exists"

    eng = create_engine(url)
    tables_after = set(inspect(eng).get_table_names())
    assert "alembic_version" in tables_after
    # The schema itself wasn't touched -- same tables as before, now tracked.
    assert tables_after - {"alembic_version"} == tables_before

    # And the database is genuinely usable afterward, not just "marked" ok.
    # created_at has no server-side DEFAULT (it's populated by the ORM), so
    # a raw insert has to supply it explicitly.
    with eng.connect() as conn:
        conn.execute(text(
            "INSERT INTO libraries (name, path, kind, created_at) "
            "VALUES ('L', '/x', 'auto', '2026-01-01T00:00:00+00:00')"
        ))
        conn.commit()
        row = conn.execute(text("SELECT name FROM libraries")).fetchone()
        assert row is not None
        assert row[0] == "L"
    eng.dispose()


def test_run_migrations_concurrent_calls_do_not_race(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path}/test.db"
    errors: list[BaseException] = []
    lock = threading.Lock()

    def run() -> None:
        try:
            run_migrations(url)
        except BaseException as exc:
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"run_migrations raised under concurrency: {errors}"
    eng = create_engine(url)
    assert "libraries" in set(inspect(eng).get_table_names())
    eng.dispose()
