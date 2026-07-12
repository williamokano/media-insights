"""Discovery package exports."""

from __future__ import annotations

from media_insights.discovery.extensions import PLEXMATCH_NAME, SUBTITLE_EXTS, VIDEO_EXTS
from media_insights.discovery.fingerprint import (
    fingerprint,
    fingerprint_changed,
    partial_fingerprint,
    stat_fingerprint,
)
from media_insights.discovery.grouping import FileObservation, group_observations
from media_insights.discovery.plexmatch import PlexMatch
from media_insights.discovery.plexmatch import parse as parse_plexmatch
from media_insights.discovery.subtitles import SidecarInfo, parse_sidecar
from media_insights.discovery.walker import (
    FoundFile,
    collect_subtitle_sidecars,
    iter_video_files,
)

__all__ = [
    "PLEXMATCH_NAME",
    "SUBTITLE_EXTS",
    "VIDEO_EXTS",
    "FileObservation",
    "FoundFile",
    "PlexMatch",
    "SidecarInfo",
    "collect_subtitle_sidecars",
    "fingerprint",
    "fingerprint_changed",
    "group_observations",
    "iter_video_files",
    "parse_plexmatch",
    "parse_sidecar",
    "partial_fingerprint",
    "stat_fingerprint",
]
