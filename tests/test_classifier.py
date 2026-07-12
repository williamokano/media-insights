"""Classifier tests."""

from __future__ import annotations

from media_insights.classify import classify
from media_insights.matching.matcher import MatchResult


def _mk_files(n: int):
    files = []
    for _ in range(n):
        # Lightweight stand-in; classifier only inspects tracks + match.
        class F:
            pass
        files.append(F())
    return files


def _mk_tracks(audio_langs: list[str], sub_langs: list[str]):
    tracks = []
    for lang in audio_langs:
        class T:
            kind = "audio"
            language = lang
        tracks.append(T())
    for lang in sub_langs:
        class T:
            kind = "subtitle"
            language = lang
        tracks.append(T())
    return tracks


def test_movie_from_library_hint() -> None:
    match = MatchResult(
        title="Interstellar", year=2014, kind="movie", season=None,
        episode_numbers=[], match_status="matched", library_kind_hint="movie",
    )
    files = _mk_files(1)
    tracks = _mk_tracks(["en"], ["en"])
    cls = classify(match, files, tracks)
    assert cls.label == "movie"
    assert cls.confidence > 0.5


def test_anime_from_japanese_audio() -> None:
    match = MatchResult(
        title="Frieren", year=None, kind="show", season=None,
        episode_numbers=[1], match_status="matched", library_kind_hint="anime",
        anidb_id=17074,
    )
    files = _mk_files(2)
    tracks = _mk_tracks(["jpn"], ["en"])
    cls = classify(match, files, tracks)
    assert cls.label == "anime"
    assert any("japanese" in r.lower() for r in cls.reasons)


def test_fansub_release_name_signals_anime_without_japanese_audio() -> None:
    """A dubbed release in an auto library: the release name must carry it."""
    from media_insights.matching.parser import parse as parse_title

    raw_name = "[SubsPlease] Frieren - 01 (1080p) [ABCDEF].mkv"
    match = MatchResult(
        title="Frieren", year=None, kind="show", season=None,
        episode_numbers=[1], match_status="unresolved", library_kind_hint="auto",
    )
    files = _mk_files(1)
    tracks = _mk_tracks(["en"], [])  # dubbed: no Japanese audio signal
    cls = classify(match, files, tracks, parsed=parse_title(raw_name), raw_name=raw_name)
    assert cls.label == "anime"
    assert any("subsplease" in r.lower() or "bracket" in r.lower() for r in cls.reasons)


def test_tv_default_for_library_hint_tv() -> None:
    match = MatchResult(
        title="Cowboy Bebop", year=1998, kind="show", season=1,
        episode_numbers=[1], match_status="matched", library_kind_hint="tv",
        tvdb_id=71663,
    )
    files = _mk_files(3)
    tracks = _mk_tracks(["en"], ["en"])
    cls = classify(match, files, tracks)
    assert cls.label == "tv"


def test_manual_override_stored_via_caller() -> None:
    # Manual override behaviour is enforced by the caller; classify returns its
    # best guess but the override flag prevents re-application.
    match = MatchResult(
        title="X", year=None, kind="show", season=1, episode_numbers=[],
        match_status="matched", library_kind_hint="tv",
    )
    files = _mk_files(1)
    tracks = _mk_tracks(["en"], [])
    cls = classify(match, files, tracks, manual_override=True)
    assert cls.label == "tv"
