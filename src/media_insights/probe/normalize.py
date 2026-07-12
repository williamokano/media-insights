"""Normalized probe data classes shared by ffprobe + pymediainfo + tests."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class TrackInfo:
    position: int            # zero-based stream index
    kind: str                # video | audio | subtitle | data
    codec: str | None = None
    language: str | None = None
    title: str | None = None
    channels: float | None = None
    bit_rate: int | None = None
    is_default: bool = False
    is_forced: bool = False
    is_sdh: bool = False

    # Optional enrichments
    width: int | None = None
    height: int | None = None
    dynamic_range: str | None = None  # SDR / HDR10 / DV / HLG
    color_transfer: str | None = None
    frame_rate: float | None = None

    def to_db(self, file_id: int) -> dict:
        return {
            "file_id": file_id,
            "position": self.position,
            "kind": self.kind,
            "codec": self.codec,
            "language": self.language,
            "title": self.title,
            "channels": self.channels,
            "bit_rate": self.bit_rate,
            "is_default": self.is_default,
            "is_forced": self.is_forced,
            "is_sdh": self.is_sdh,
        }


@dataclass(slots=True)
class ProbeResult:
    container: str | None = None
    duration: float | None = None
    bit_rate: int | None = None
    tracks: list[TrackInfo] = field(default_factory=list)

    @property
    def video_tracks(self) -> list[TrackInfo]:
        return [t for t in self.tracks if t.kind == "video"]

    @property
    def audio_tracks(self) -> list[TrackInfo]:
        return [t for t in self.tracks if t.kind == "audio"]

    @property
    def subtitle_tracks(self) -> list[TrackInfo]:
        return [t for t in self.tracks if t.kind == "subtitle"]

    def primary_video(self) -> TrackInfo | None:
        v = self.video_tracks
        return v[0] if v else None


def _safe_int(v) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _safe_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_channels(layout: str | None) -> float | None:
    """Convert layouts like '5.1' / '7.1' to a number; ignore exotic names."""
    if not layout:
        return None
    if "." in layout:
        try:
            return float(layout)
        except ValueError:
            return None
    return None


def _flags_from_tags(tags: dict, disposition: dict | None = None) -> tuple[bool, bool, bool]:
    disposition = disposition or tags.get("disposition") or {}
    is_default = bool(disposition.get("default"))
    is_forced = bool(disposition.get("forced"))
    is_sdh = bool(disposition.get("hearing_impaired"))
    title = (tags.get("title") or "").lower()
    if not is_forced and "forced" in title:
        is_forced = True
    if not is_sdh and any(t in title for t in ("sdh", "cc", "hi", "hearing impaired")):
        is_sdh = True
    return is_default, is_forced, is_sdh


def parse_ffprobe(data: dict) -> ProbeResult:
    """Translate ffprobe's JSON output into a ProbeResult."""
    fmt = data.get("format") or {}
    container = (fmt.get("format_name") or "").split(",")[0] or None
    result = ProbeResult(
        container=container,
        duration=_safe_float(fmt.get("duration")),
        bit_rate=_safe_int(fmt.get("bit_rate")),
    )

    for idx, stream in enumerate(data.get("streams") or []):
        codec_type = stream.get("codec_type")
        if codec_type not in {"video", "audio", "subtitle", "data"}:
            continue
        tags = stream.get("tags") or {}
        disposition = stream.get("disposition") or {}
        language = tags.get("language") or tags.get("LANGUAGE")
        title = tags.get("title") or tags.get("TITLE")
        is_default, is_forced, is_sdh = _flags_from_tags(tags, disposition)

        track = TrackInfo(
            position=idx,
            kind=codec_type,
            codec=stream.get("codec_name"),
            language=language,
            title=title,
            bit_rate=_safe_int(stream.get("bit_rate")),
            is_default=is_default,
            is_forced=is_forced,
            is_sdh=is_sdh,
        )

        if codec_type == "audio":
            track.channels = _parse_channels(stream.get("channel_layout"))
            if track.channels is None:
                track.channels = _safe_float(stream.get("channels"))
        elif codec_type == "video":
            track.width = _safe_int(stream.get("width"))
            track.height = _safe_int(stream.get("height"))
            track.frame_rate = _safe_frame_rate(stream.get("avg_frame_rate") or stream.get("r_frame_rate"))
            track.dynamic_range, track.color_transfer = _video_dynamic_range(
                stream, tags
            )

        result.tracks.append(track)
    return result


def _safe_frame_rate(rate: str | None) -> float | None:
    if not rate or "/" not in rate:
        return _safe_float(rate)
    num, _, den = rate.partition("/")
    try:
        n = float(num)
        d = float(den) or 1.0
        return n / d
    except (TypeError, ValueError):
        return None


def _video_dynamic_range(stream: dict, tags: dict) -> tuple[str | None, str | None]:
    transfer = stream.get("color_transfer")
    primaries = stream.get("color_primaries")
    side_data = stream.get("side_data_list") or []
    dv = any(s.get("side_data_type") == "Dolby Vision RPU" for s in side_data)
    if dv:
        return "DV", transfer
    if transfer and "arib" in str(transfer).lower():
        return "HLG", transfer
    if transfer in ("smpte2084", "smpte2086"):
        return "HDR10", transfer
    if primaries == "bt2020" and transfer:
        return "HDR", transfer
    if (tags.get("title") or "").lower().find("hdr") >= 0:
        return "HDR", transfer
    return "SDR", transfer
