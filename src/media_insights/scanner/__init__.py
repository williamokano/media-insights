"""Scanner package exports."""

from __future__ import annotations

from media_insights.scanner.scheduler import ScanScheduler
from media_insights.scanner.service import (
    get_or_create_library,
    handle_missing_path,
    manual_rescan_path,
    reclassify_all,
    scan_all,
    scan_library,
)
from media_insights.scanner.watcher import MediaWatcher

__all__ = [
    "MediaWatcher",
    "ScanScheduler",
    "get_or_create_library",
    "handle_missing_path",
    "manual_rescan_path",
    "reclassify_all",
    "scan_all",
    "scan_library",
]
