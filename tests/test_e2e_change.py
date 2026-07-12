"""Change detection end-to-end."""

from __future__ import annotations

from pathlib import Path

from media_insights.config import (
    AppConfig,
    DatabaseConfig,
    FingerprintConfig,
    ScheduleConfig,
    WatcherConfig,
)
from media_insights.db import init_engine, reset_for_tests, session_scope
from media_insights.models import Base, ChangeEvent
from media_insights.scanner import manual_rescan_path, scan_library
from tests.fixtures.media_factory import rewrite_with_different_codec


def _config_for(lib) -> AppConfig:
    import tempfile
    tmpdir = tempfile.mkdtemp(prefix="mi-e2e-")
    db_url = f"sqlite:///{tmpdir}/test.db"
    reset_for_tests()
    eng = init_engine(db_url)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    return AppConfig(
        config_dir=tmpdir,
        data_dir=tmpdir,
        log_level="WARNING",
        database=DatabaseConfig(url=db_url),
        fingerprint=FingerprintConfig(strategy="partial", chunk_bytes=65536),
        watcher=WatcherConfig(enabled=False),
        schedule=ScheduleConfig(enabled=False),
        libraries=[lib],
    )


def test_rewrite_yields_change_event(tmp_tv) -> None:
    cfg = _config_for(tmp_tv)
    scan_library(cfg, tmp_tv, force=True)
    target = next(Path(tmp_tv.path).rglob("*.mkv"))
    rewrite_with_different_codec(target)
    manual_rescan_path(cfg, str(target))

    with session_scope() as session:
        events = (
            session.query(ChangeEvent)
            .filter(ChangeEvent.subject_path == str(target))
            .all()
        )
        assert events
        last = events[-1]
        assert last.type == "file.changed"
        assert last.old_payload is not None
        assert last.new_payload is not None
        assert last.old_payload["video_codec"] != last.new_payload["video_codec"]
