"""Probe normalization tests using canned ffprobe JSON."""

from __future__ import annotations

from media_insights.probe import parse_ffprobe
from media_insights.probe.normalize import (
    ProbeResult,
)
from media_insights.probe.normalize import (
    parse_ffprobe as direct_parse,
)


def test_parse_ffprobe_basic(sample_ffprobe_streams: dict) -> None:
    result: ProbeResult = parse_ffprobe(sample_ffprobe_streams)
    assert result.container == "matroska"
    assert result.duration == 120.0
    assert len(result.tracks) == 4
    video = result.primary_video()
    assert video is not None
    assert video.codec == "hevc"
    assert video.width == 1920 and video.height == 1080
    assert video.dynamic_range == "HDR10"
    audio = result.audio_tracks
    assert len(audio) == 2
    assert audio[0].language == "ja"
    assert audio[0].language_raw == "jpn"
    assert audio[0].channels == 7.1
    assert audio[1].is_default is True
    assert audio[1].language == "en"
    assert audio[1].language_raw == "en"
    subs = result.subtitle_tracks
    assert len(subs) == 1
    assert subs[0].language == "en"
    assert subs[0].language_raw == "en"


def test_flags_extracted_from_disposition(sample_ffprobe_streams: dict) -> None:
    result = direct_parse(sample_ffprobe_streams)
    audio = result.audio_tracks
    assert audio[0].is_default is False  # no default flag in source
    assert audio[1].is_default is True


def test_parse_handles_garbage() -> None:
    out = direct_parse({})
    assert out.tracks == []
    assert out.container is None
    assert out.duration is None
