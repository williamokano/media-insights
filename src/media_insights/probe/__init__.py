"""Probe public surface."""

from __future__ import annotations

from pathlib import Path

from media_insights.probe.ffprobe import ProbeError
from media_insights.probe.ffprobe import probe as ffprobe
from media_insights.probe.mediainfo import enrich, import_warning
from media_insights.probe.normalize import ProbeResult, TrackInfo, parse_ffprobe

__all__ = [
    "ProbeError",
    "ProbeResult",
    "TrackInfo",
    "enrich",
    "ffprobe",
    "import_warning",
    "parse_ffprobe",
]


def probe(path: str | Path, ffprobe_bin: str = "", use_mediainfo: bool = True) -> ProbeResult:
    """ffprobe then MediaInfo enrichment on top."""
    result = ffprobe(path, ffprobe_bin=ffprobe_bin)
    if use_mediainfo:
        return enrich(result, path)
    return result
