"""Dispatcher outbox behavior."""

from __future__ import annotations

from media_insights.config import AppConfig, DatabaseConfig, WebhookConfig
from media_insights.db import init_engine, reset_for_tests, session_scope
from media_insights.events import Dispatcher, bus
from media_insights.models import Base, ChangeEvent


def _setup(tmp_path, webhooks: list[WebhookConfig]) -> AppConfig:
    db_url = f"sqlite:///{tmp_path}/test.db"
    reset_for_tests()
    eng = init_engine(db_url)
    Base.metadata.create_all(eng)
    return AppConfig(
        config_dir=str(tmp_path),
        database=DatabaseConfig(url=db_url),
        webhooks=webhooks,
    )


def _record_one() -> None:
    with session_scope() as session:
        bus.record_event(
            session, type_="file.changed", subject_id=1, subject_path="/x.mkv",
            old={"codec": "h264"}, new={"codec": "hevc"},
        )


def test_no_hooks_marks_skipped_not_failed(tmp_path) -> None:
    cfg = _setup(tmp_path, webhooks=[WebhookConfig(url="")])
    _record_one()
    dispatcher = Dispatcher(cfg)
    dispatcher.drain_once()
    with session_scope() as session:
        event = session.query(ChangeEvent).one()
        assert event.delivery_status == "skipped"
        assert event.delivery_attempts == 0
        assert event.last_error is None


def test_dead_webhook_marks_failed_after_attempts(tmp_path) -> None:
    cfg = _setup(
        tmp_path,
        webhooks=[WebhookConfig(url="http://127.0.0.1:1/nope", timeout_seconds=0.2, max_attempts=2)],
    )
    _record_one()
    dispatcher = Dispatcher(cfg)
    dispatcher.drain_once()
    dispatcher.drain_once()
    dispatcher.drain_once()  # beyond max_attempts: nothing due anymore
    with session_scope() as session:
        event = session.query(ChangeEvent).one()
        assert event.delivery_status == "failed"
        assert event.delivery_attempts == 2
        assert event.last_error
