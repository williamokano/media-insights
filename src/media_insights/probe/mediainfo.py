"""Optional pymediainfo enrichment.

The bundled MediaInfo binary lives inside the manylinux wheels, so no system
package is required. If pymediainfo or libmediainfo is unavailable for any
reason, enrich() is a no-op and the probe result is returned unchanged.
"""

from __future__ import annotations

import logging
from pathlib import Path

from media_insights.probe.normalize import ProbeResult

log = logging.getLogger(__name__)

try:
    from pymediainfo import MediaInfo  # type: ignore
except Exception as exc:  # pragma: no cover
    MediaInfo = None  # type: ignore
    _IMPORT_ERROR: Exception | None = exc
else:
    _IMPORT_ERROR = None


def available() -> bool:
    return MediaInfo is not None


def _strip_prefix(value: str | None, prefix: str) -> str | None:
    if not value:
        return value
    return value[len(prefix):] if value.startswith(prefix) else value


def enrich(result: ProbeResult, path: str | Path) -> ProbeResult:
    if not available():
        return result
    try:
        info = MediaInfo.parse(str(path))
    except Exception as exc:
        log.debug("pymediainfo failed for %s: %s", path, exc)
        return result

    # Backfill HDR strings ffprobe often returns as empty
    for t in result.tracks:
        if t.kind != "video":
            continue
        for track in info.tracks:
            if track.track_type != "Video":
                continue
            if t.dynamic_range in (None, "SDR") and getattr(track, "hdr_format_commercial", None):
                hdr = (track.hdr_format_commercial or "").lower()
                if "dolby vision" in hdr:
                    t.dynamic_range = "DV"
                elif "hdr10+" in hdr:
                    t.dynamic_range = "HDR10+"
                elif "hdr10" in hdr:
                    t.dynamic_range = "HDR10"
                elif "hlg" in hdr:
                    t.dynamic_range = "HLG"
            if t.frame_rate is None and getattr(track, "frame_rate", None):
                try:
                    t.frame_rate = float(track.frame_rate)
                except (TypeError, ValueError):
                    pass
            break  # one video track is enough

    return result


def import_warning() -> str | None:
    if MediaInfo is None:
        return f"pymediainfo unavailable: {_IMPORT_ERROR}"
    return None
