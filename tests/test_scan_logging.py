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


def test_dispatcher_logs_why_events_are_skipped(tmp_path, caplog) -> None:
    from media_insights.config import AppConfig, DatabaseConfig
    from media_insights.db import init_engine, reset_for_tests, session_scope
    from media_insights.models import Base

    db_url = f"sqlite:///{tmp_path}/test.db"
    reset_for_tests()
    eng = init_engine(db_url)
    Base.metadata.create_all(eng)
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
