"""Subtitle sidecar parsing tests."""

from __future__ import annotations

from pathlib import Path

from media_insights.discovery.subtitles import parse_sidecar


def test_simple_language() -> None:
    sc = Path("Movie.en.srt")
    info = parse_sidecar("Movie", sc)
    assert info.language == "en"
    assert not info.is_forced


def test_forced_flag() -> None:
    sc = Path("Movie.en.forced.srt")
    info = parse_sidecar("Movie", sc)
    assert info.language == "en"
    assert info.is_forced


def test_sdh_flag() -> None:
    sc = Path("Movie.eng.sdh.ass")
    info = parse_sidecar("Movie", sc)
    assert info.language == "en"
    assert info.is_sdh


def test_brazilian_portuguese() -> None:
    sc = Path("Movie.pt-BR.srt")
    info = parse_sidecar("Movie", sc)
    assert info.language == "pt-BR"


def test_unknown_token_falls_back_to_language() -> None:
    sc = Path("Movie.klingon.srt")
    info = parse_sidecar("Movie", sc)
    assert info.language == "klingon"


def test_default_flag() -> None:
    sc = Path("Movie.ja.default.srt")
    info = parse_sidecar("Movie", sc)
    assert info.is_default
    assert info.language == "ja"
