"""Concurrent scans of the same library must serialize, not race.

Regression coverage for a real production incident: scan_library() used to
hold one long-lived transaction for an entire library scan. SQLite allows
exactly one writer, and with no busy_timeout configured, any other writer
that showed up while a scan was in flight (a second scan trigger, the event
dispatcher, a watcher-triggered rescan) failed immediately with "database is
locked" instead of waiting -- which then cascaded into PendingRollbackError
on every subsequent statement in that same poisoned session.
"""

from __future__ import annotations

import threading
import time
from itertools import pairwise

import media_insights.scanner.service as svc
from media_insights.db import session_scope
from media_insights.models import MediaItem
from media_insights.scanner import scan_library
from media_insights.scanner.service import _lock_for
from tests.test_e2e_scan import _config_for


def test_lock_for_is_stable_per_name() -> None:
    a1 = _lock_for("Movies")
    a2 = _lock_for("Movies")
    b = _lock_for("TV")
    assert a1 is a2
    assert a1 is not b


def test_concurrent_scans_of_same_library_serialize_and_dont_error(tmp_tv, monkeypatch) -> None:
    cfg = _config_for(tmp_tv)

    # _process_file() only ever runs while the caller holds _lock_for(name),
    # so instrumenting it (rather than the outer scan_library() call, which
    # also includes time spent blocked waiting for the lock) proves whether
    # the critical section itself is genuinely exclusive.
    original_process_file = svc._process_file
    intervals: list[tuple[float, float]] = []
    intervals_lock = threading.Lock()

    def instrumented(*args, **kwargs):
        start = time.monotonic()
        try:
            return original_process_file(*args, **kwargs)
        finally:
            end = time.monotonic()
            with intervals_lock:
                intervals.append((start, end))

    monkeypatch.setattr(svc, "_process_file", instrumented)

    errors: list[BaseException] = []
    results_lock = threading.Lock()

    def run() -> None:
        try:
            summary = scan_library(cfg, tmp_tv, force=True)
        except BaseException as exc:
            with results_lock:
                errors.append(exc)
            return
        assert summary["errors"] == 0

    threads = [threading.Thread(target=run) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"scan_library raised under concurrency: {errors}"
    assert len(intervals) == 6  # 3 scans x 2 episode files each

    # Since every call happened while its thread held the per-library lock,
    # no two calls -- regardless of which thread -- should ever overlap.
    intervals.sort()
    for (_, end_a), (start_b, _) in pairwise(intervals):
        assert end_a <= start_b, "scans of the same library overlapped under the lock"

    # And the end result matches a single clean scan: no duplicated items
    # from three scans racing to create the same MediaItem.
    with session_scope() as session:
        items = session.query(MediaItem).all()
        assert len(items) == 1
