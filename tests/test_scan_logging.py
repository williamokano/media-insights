"""Observability regression coverage.

scan_library() used to log nothing on the success path -- a scan (manual,
scheduled, or watcher-triggered) was completely silent in the container
logs unless something crashed. These tests pin down the minimum bar: every
scan logs a start line, a finish summary, and per-file added/changed
outcomes, all tagged with *why* it ran (trigger=...).
"""

from __future__ import annotations

import logging

from media_insights.events import Dispatcher, bus
from media_insights.scanner import scan_library
from tests.test_e2e_scan import _config_for


def test_scan_logs_start_and_finish_with_trigger(tmp_library, caplog) -> None:
    with caplog.at_level(logging.INFO, logger="media_insights.scanner.service"):
        summary = scan_library(_config_for(tmp_library), tmp_library, force=True, trigger="cli")

    assert summary["trigger"] == "cli"
    messages = [r.message for r in caplog.records]
    assert any("scan started" in m and "trigger=cli" in m for m in messages)
    assert any("scan finished" in m and "trigger=cli" in m and "added=1" in m for m in messages)
    assert any("file added" in m for m in messages)


def test_default_trigger_is_manual(tmp_library, caplog) -> None:
    with caplog.at_level(logging.INFO, logger="media_insights.scanner.service"):
        summary = scan_library(_config_for(tmp_library), tmp_library, force=True)
    assert summary["trigger"] == "manual"
    assert any("trigger=manual" in r.message for r in caplog.records)


def test_unchanged_files_do_not_log_at_info(tmp_library, caplog) -> None:
    cfg = _config_for(tmp_library)
    scan_library(cfg, tmp_library, force=True)  # first scan: populates the DB

    with caplog.at_level(logging.INFO, logger="media_insights.scanner.service"):
        summary = scan_library(cfg, tmp_library, force=False)

    assert summary["files_unchanged"] == 1
    messages = [r.message for r in caplog.records]
    assert not any("file added" in m or "file changed" in m for m in messages)
    assert any("scan finished" in m and "unchanged=1" in m for m in messages)


def test_scan_logs_new_title_and_classification(tmp_library, caplog) -> None:
    with caplog.at_level(logging.INFO, logger="media_insights.scanner.service"):
        scan_library(_config_for(tmp_library), tmp_library, force=True)

    messages = [r.message for r in caplog.records]
    assert any("new title:" in m and "Interstellar" in m for m in messages)
    assert any("classified:" in m and "movie" in m for m in messages)


def test_scan_does_not_relog_classification_when_unchanged(tmp_library, caplog) -> None:
    cfg = _config_for(tmp_library)
    scan_library(cfg, tmp_library, force=True)  # establishes the classification

    with caplog.at_level(logging.INFO, logger="media_insights.scanner.service"):
        scan_library(cfg, tmp_library, force=False)  # nothing changed

    messages = [r.message for r in caplog.records]
    assert not any("classified:" in m for m in messages)
    assert not any("new title:" in m for m in messages)


def test_match_detail_logged_at_debug(tmp_library, caplog) -> None:
    with caplog.at_level(logging.DEBUG, logger="media_insights.scanner.service"):
        scan_library(_config_for(tmp_library), tmp_library, force=True)

    messages = [r.message for r in caplog.records]
    assert any("matched:" in m and "via=" in m for m in messages)


def test_startup_logs_offline_matching_disclaimer(tmp_path, caplog) -> None:
    from fastapi.testclient import TestClient

    from media_insights.api import configure, create_app
    from media_insights.config import AppConfig, DatabaseConfig, ScheduleConfig, WatcherConfig
    from media_insights.db import init_engine, run_migrations

    db_url = f"sqlite:///{tmp_path}/test.db"
    cfg = AppConfig(
        config_dir=str(tmp_path),
        database=DatabaseConfig(url=db_url),
        watcher=WatcherConfig(enabled=False),
        schedule=ScheduleConfig(enabled=False),
    )
    init_engine(db_url)
    run_migrations(db_url)
    configure(cfg, tmp_path / "config.yaml")
    app = create_app()

    with caplog.at_level(logging.INFO, logger="media_insights.api.app"), TestClient(app):
        pass

    messages = [r.message for r in caplog.records]
    # Offline is still the default, and startup says so -- but it now also says
    # how to turn providers on, rather than claiming network calls are never
    # made (which stopped being unconditionally true once providers landed).
    assert any("offline only" in m and "no network calls" in m for m in messages)
    assert any("providers.enabled=true" in m for m in messages)


def test_startup_logs_which_providers_are_enabled(tmp_path, caplog) -> None:
    from fastapi.testclient import TestClient

    from media_insights.api import configure, create_app
    from media_insights.config import (
        AniListConfig,
        AppConfig,
        DatabaseConfig,
        ProvidersConfig,
        ScheduleConfig,
        WatcherConfig,
    )
    from media_insights.db import init_engine, run_migrations

    db_url = f"sqlite:///{tmp_path}/test.db"
    cfg = AppConfig(
        config_dir=str(tmp_path),
        database=DatabaseConfig(url=db_url),
        watcher=WatcherConfig(enabled=False),
        schedule=ScheduleConfig(enabled=False),
        providers=ProvidersConfig(enabled=True, anilist=AniListConfig(enabled=True)),
    )
    init_engine(db_url)
    run_migrations(db_url)
    configure(cfg, tmp_path / "config.yaml")
    app = create_app()

    with caplog.at_level(logging.INFO, logger="media_insights.api.app"), TestClient(app):
        pass

    messages = [r.message for r in caplog.records]
    assert any("online providers enabled" in m and "anilist" in m for m in messages)


def test_dispatcher_logs_why_events_are_skipped(tmp_path, caplog) -> None:
    from media_insights.config import AppConfig, DatabaseConfig
    from media_insights.db import init_engine, reset_for_tests, run_migrations, session_scope

    db_url = f"sqlite:///{tmp_path}/test.db"
    reset_for_tests()
    init_engine(db_url)
    run_migrations(db_url)
    cfg = AppConfig(config_dir=str(tmp_path), database=DatabaseConfig(url=db_url), webhooks=[])

    with session_scope() as session:
        bus.record_event(
            session, type_="file.changed", subject_id=1, subject_path="/x.mkv",
            old={"codec": "h264"}, new={"codec": "hevc"},
        )

    with caplog.at_level(logging.INFO, logger="media_insights.events.dispatcher"):
        Dispatcher(cfg).drain_once()

    assert any(
        "no webhooks/exec_hooks configured" in r.message and "skipped" in r.message
        for r in caplog.records
    )
