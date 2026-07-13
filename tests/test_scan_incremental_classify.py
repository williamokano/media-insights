"""Classification must not wait for the whole scan to finish.

Found against a real library: a 16 TB anime library was mid-scan with 65
titles indexed and *every one of them* showing no classification, because
_reclassify_library() only ran after the entire file loop. On a library that
takes hours, that means every title reads as unclassified for the whole scan
-- and if the process restarts before it completes, nothing is ever classified
at all.
"""

from __future__ import annotations

import tempfile

from media_insights.config import (
    AppConfig,
    DatabaseConfig,
    LibraryConfig,
    ScheduleConfig,
    WatcherConfig,
)
from media_insights.db import init_engine, reset_for_tests, run_migrations, session_scope
from media_insights.models import MediaItem
from media_insights.scanner import service


def _cfg(tmpdir: str) -> AppConfig:
    db_url = f"sqlite:///{tmpdir}/test.db"
    reset_for_tests()
    init_engine(db_url)
    run_migrations(db_url)
    return AppConfig(
        config_dir=tmpdir,
        data_dir=tmpdir,
        log_level="WARNING",
        database=DatabaseConfig(url=db_url),
        watcher=WatcherConfig(enabled=False),
        schedule=ScheduleConfig(enabled=False),
        libraries=[],
    )


def test_titles_are_classified_before_the_scan_finishes(monkeypatch, tmp_path) -> None:
    """Simulate a scan that is interrupted partway through: the titles it has
    already indexed must already carry a classification."""
    tmpdir = tempfile.mkdtemp(prefix="mi-incr-")
    cfg = _cfg(tmpdir)
    lib = LibraryConfig(name="Anime", path=str(tmp_path), kind="auto")

    # Reclassify every 2 files so the test doesn't need hundreds of them.
    monkeypatch.setattr(service, "_RECLASSIFY_EVERY_N_FILES", 2)

    files = [tmp_path / f"Show S01E0{i}.mkv" for i in range(1, 6)]
    for f in files:
        f.write_bytes(b"x")

    class _Found:
        def __init__(self, path):
            self.path = path
            self.parent = path.parent
            self.plexmatch_path = None

    processed = {"n": 0}

    def fake_iter(path, recursive=True):
        for f in files:
            yield _Found(f)

    def fake_process(session, cfg_, library, found, *, force, summary=None):
        """Index the file without probing, then blow up partway through -- the
        titles indexed so far must already be classified."""
        processed["n"] += 1
        service._item_record(
            session,
            library,
            service.match_observation(
                service.FileObservation(found=found), service._as_libcfg(library)
            ),
        )
        if processed["n"] == 4:
            raise KeyboardInterrupt("scan interrupted, as a restart would")
        return "files_added"

    monkeypatch.setattr(service, "iter_video_files", fake_iter)
    monkeypatch.setattr(service, "_process_file", fake_process)

    try:
        service.scan_library(cfg, lib)
    except KeyboardInterrupt:
        pass  # the scan never reached its final reclassify pass

    with session_scope() as session:
        items = session.query(MediaItem).all()
        assert items, "the scan should have indexed at least one title"
        # The whole point: these were classified mid-scan, not at the end.
        assert all(item.classification_label is not None for item in items), (
            "titles indexed before the scan finished are still unclassified"
        )
