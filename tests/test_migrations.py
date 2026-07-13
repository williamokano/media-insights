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

    item_columns = {c["name"] for c in inspect(create_engine(url)).get_columns("media_items")}
    assert {
        "anilist_id", "provider_source", "provider_is_anime",
        "provider_origin_country", "provider_genres", "provider_checked_at",
    } <= item_columns


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


def _create_genuinely_historical_pre_migration_db(url: str) -> None:
    """Builds the exact schema create_all() used to produce before migrations
    existed (0.0.6 and earlier), with no alembic_version table at all.

    Deliberately does NOT use Base.metadata.create_all() -- that builds from
    *current* models, which already include every column added since, so it
    can never actually simulate a historical pre-migration database. That
    blind spot is exactly how the 0.0.8 stamp-to-head bug shipped undetected:
    a test built this way can't tell "stamp to head" apart from "stamp to
    the revision that actually matches the physical schema", since with
    current models both happen to produce the same columns.

    Instead, runs the real initial migration's DDL (guaranteed historically
    accurate) and then drops the alembic_version bookkeeping table alembic's
    upgrade command creates as a side effect -- a true pre-migration database
    never had that table.
    """
    from alembic import command
    from alembic.config import Config as AlembicConfig

    from media_insights.db import _migrations_dir

    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(_migrations_dir()))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "d0f4d45356ad")

    eng = create_engine(url)
    with eng.connect() as conn:
        conn.execute(text("DROP TABLE alembic_version"))
        conn.commit()
    eng.dispose()


def test_run_migrations_stamps_pre_existing_database_instead_of_recreating(tmp_path: Path) -> None:
    """Simulates every database created before migrations were wired in:
    tables exist, but there's no alembic_version table. run_migrations()
    must detect this, stamp the DB at the revision that actually matches its
    physical schema, then upgrade forward for real -- ending up with every
    column since, not just the ones that existed when create_all() ran.
    """
    url = f"sqlite:///{tmp_path}/test.db"
    _create_genuinely_historical_pre_migration_db(url)
    eng = create_engine(url)
    tables_before = set(inspect(eng).get_table_names())
    columns_before = {c["name"] for c in inspect(eng).get_columns("tracks")}
    assert "alembic_version" not in tables_before
    assert "language_raw" not in columns_before
    eng.dispose()

    run_migrations(url)  # must not raise "table already exists"

    eng = create_engine(url)
    tables_after = set(inspect(eng).get_table_names())
    assert "alembic_version" in tables_after
    assert tables_after - {"alembic_version"} == tables_before
    # The real regression this guards: a genuinely pre-migration database
    # must end up with columns added by migrations *after* the initial one,
    # not be silently stamped past them.
    columns_after = {c["name"] for c in inspect(eng).get_columns("tracks")}
    assert "language_raw" in columns_after

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


def test_run_migrations_repairs_database_previously_stamped_straight_to_head(tmp_path: Path) -> None:
    """Reproduces the exact 0.0.8 incident: an older, buggy build of
    run_migrations() stamped a genuinely pre-migration database straight to
    "head" instead of the revision matching its actual physical schema --
    alembic_version claims the DB is fully migrated, but the physical table
    is missing a column a later migration was supposed to add. The current
    run_migrations() must detect this drift and repair it for real, rather
    than trusting the (wrong) recorded version and treating upgrade(head)
    as a no-op.
    """
    from alembic import command
    from alembic.config import Config as AlembicConfig

    from media_insights.db import _migrations_dir

    url = f"sqlite:///{tmp_path}/test.db"
    _create_genuinely_historical_pre_migration_db(url)

    # Simulate the bug: stamp straight to head (what the old code did),
    # instead of the correct behavior of stamping at the initial revision.
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(_migrations_dir()))
    cfg.set_main_option("sqlalchemy.url", url)
    command.stamp(cfg, "head")

    eng = create_engine(url)
    columns_before = {c["name"] for c in inspect(eng).get_columns("tracks")}
    assert "language_raw" not in columns_before  # the corrupted state: claims head, physically isn't
    eng.dispose()

    run_migrations(url)  # must detect the drift and repair it, not treat this as already done

    eng = create_engine(url)
    columns_after = {c["name"] for c in inspect(eng).get_columns("tracks")}
    assert "language_raw" in columns_after
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
