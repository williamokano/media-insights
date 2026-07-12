"""End-to-end scanner test using real ffmpeg-generated media."""

from __future__ import annotations

from media_insights.db import session_scope
from media_insights.models import MediaFile, MediaItem, Track
from media_insights.scanner import scan_library


def test_scan_movie_extracts_metadata(tmp_library) -> None:
    summary = scan_library(_config_for(tmp_library), tmp_library, force=True)
    assert summary["files_added"] == 1
    assert summary["files_unchanged"] == 0
    with session_scope() as session:
        item = session.query(MediaItem).one()
        assert item.title.lower() == "interstellar"
        assert item.classification_label == "movie"
        files = session.query(MediaFile).all()
        assert files
        tracks = session.query(Track).all()
        assert any(t.kind == "audio" for t in tracks)


def test_scan_tv_two_episodes(tmp_tv) -> None:
    cfg = _config_for(tmp_tv)
    scan_library(cfg, tmp_tv, force=True)
    with session_scope() as session:
        items = session.query(MediaItem).all()
        assert len(items) == 1
        item = items[0]
        assert item.kind == "show"
        files = session.query(MediaFile).all()
        assert len(files) == 2
        eps = sorted(f.episode_numbers[0] for f in files)
        assert eps == [1, 2]


def test_scan_anime_classified_correctly(tmp_anime) -> None:
    cfg = _config_for(tmp_anime)
    scan_library(cfg, tmp_anime, force=True)
    with session_scope() as session:
        item = session.query(MediaItem).one()
        assert item.classification_label == "anime"
        assert any("japanese" in r.lower() for r in item.classification_reasons)
        # External sidecar should be tracked
        tracks = session.query(Track).filter(Track.kind == "subtitle", Track.is_external.is_(True)).all()
        assert tracks
        langs = {t.language for t in tracks}
        assert "en" in langs


def test_second_scan_is_idempotent(tmp_library) -> None:
    cfg = _config_for(tmp_library)
    scan_library(cfg, tmp_library, force=True)
    summary = scan_library(cfg, tmp_library, force=False)
    assert summary["files_unchanged"] == 1
    assert summary["files_added"] == 0


def _config_for(lib) -> object:
    import tempfile

    from media_insights.config import (
        AppConfig,
        DatabaseConfig,
        FingerprintConfig,
        ScheduleConfig,
        WatcherConfig,
    )
    from media_insights.db import init_engine, reset_for_tests
    from media_insights.models import Base

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
