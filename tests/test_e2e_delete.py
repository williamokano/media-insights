"""Deletion handling end-to-end."""

from __future__ import annotations

from pathlib import Path

from media_insights.db import session_scope
from media_insights.models import ChangeEvent, MediaFile, MediaItem
from media_insights.scanner import handle_missing_path, scan_library
from tests.test_e2e_change import _config_for


def test_deep_scan_prunes_missing_files(tmp_tv) -> None:
    cfg = _config_for(tmp_tv)
    scan_library(cfg, tmp_tv, force=True)

    target = next(Path(tmp_tv.path).rglob("*E01*.mkv"))
    target.unlink()

    summary = scan_library(cfg, tmp_tv, force=False)
    assert summary["files_removed"] == 1

    with session_scope() as session:
        remaining = session.query(MediaFile).all()
        assert len(remaining) == 1
        assert "E02" in remaining[0].path
        event = (
            session.query(ChangeEvent)
            .filter(ChangeEvent.type == "file.removed")
            .one()
        )
        assert event.subject_path == str(target)
        assert event.old_payload is not None
        assert event.old_payload["path"] == str(target)
        assert event.new_payload is None


def test_deleting_last_file_prunes_item(tmp_library) -> None:
    cfg = _config_for(tmp_library)
    scan_library(cfg, tmp_library, force=True)

    target = next(Path(tmp_library.path).rglob("*.mkv"))
    target.unlink()
    scan_library(cfg, tmp_library, force=False)

    with session_scope() as session:
        assert session.query(MediaFile).count() == 0
        assert session.query(MediaItem).count() == 0


def test_handle_missing_path_single_file(tmp_tv) -> None:
    cfg = _config_for(tmp_tv)
    scan_library(cfg, tmp_tv, force=True)

    target = next(Path(tmp_tv.path).rglob("*E02*.mkv"))
    target.unlink()

    assert handle_missing_path(cfg, str(target)) is True
    assert handle_missing_path(cfg, str(target)) is False  # already gone

    with session_scope() as session:
        assert session.query(MediaFile).filter(MediaFile.path == str(target)).count() == 0
        events = session.query(ChangeEvent).filter(ChangeEvent.type == "file.removed").all()
        assert len(events) == 1
