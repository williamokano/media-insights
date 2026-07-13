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


def _mk_tracks(audio_langs: list[str], sub_langs: list[str], *, default_audio: int = 0):
    """Stand-in tracks. `default_audio` is the index of the default audio
    track -- which one the player would actually pick, i.e. the primary."""
    tracks = []
    for i, lang in enumerate(audio_langs):
        class T:
            kind = "audio"
            language = lang
            file_id = 1
            position = i
            is_default = i == default_audio
        tracks.append(T())
    for i, lang in enumerate(sub_langs):
        class T:
            kind = "subtitle"
            language = lang
            file_id = 1
            position = len(audio_langs) + i
            is_default = False
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


def test_anime_misfiled_in_tv_library_is_still_detected_as_anime() -> None:
    """The bug this classifier exists to catch.

    After a drive migration, anime ended up inside the TV Shows folder. The
    library hint used to be weighted 0.7 -- more than every real signal
    combined -- so a blatantly-anime file (Japanese audio, English subs) in a
    kind=tv library was scored tv=0.90 vs anime=0.70 and mislabeled `tv`. The
    folder must never outvote the evidence, or misfiled titles can never be
    found.
    """
    match = MatchResult(
        title="Frieren", year=2023, kind="show", season=1,
        episode_numbers=[1], match_status="unresolved",
        library_kind_hint="tv",  # misfiled: it's sitting in /TV Shows
    )
    files = _mk_files(28)
    tracks = _mk_tracks(["ja"], ["en"])  # Japanese audio + English subs
    cls = classify(match, files, tracks)
    assert cls.label == "anime"
    assert any("japanese" in r.lower() for r in cls.reasons)
    # And the overruled folder is stated on the verdict, so it's auditable.
    assert any("overrode library kind hint" in r.lower() for r in cls.reasons)


def test_western_show_with_a_japanese_dub_track_is_not_anime() -> None:
    """Caught by dry-running the classifier against the real library.

    Amazon's `Secret Level` is a western animated anthology that ships
    English/German/Spanish/Japanese dubs with English as the default. The old
    audio test asked "is ANY audio track Japanese?" -- despite being named
    `_has_japanese_primary` -- so it flagged the show as anime. Only the
    primary (default) audio track is evidence of origin.
    """
    match = MatchResult(
        title="Secret Level", year=2024, kind="show", season=1,
        episode_numbers=[1], match_status="unresolved", library_kind_hint="tv",
    )
    files = _mk_files(15)
    # English default, with a Japanese dub buried among the others.
    tracks = _mk_tracks(["en", "de", "es", "ja"], ["en"], default_audio=0)
    cls = classify(match, files, tracks)
    assert cls.label == "tv"


def test_japanese_default_audio_among_dubs_is_still_anime() -> None:
    """The mirror: a dual-audio anime whose default track is Japanese."""
    match = MatchResult(
        title="Frieren", year=2023, kind="show", season=1,
        episode_numbers=[1], match_status="unresolved", library_kind_hint="tv",
    )
    tracks = _mk_tracks(["ja", "en"], ["en"], default_audio=0)
    cls = classify(match, _mk_files(28), tracks)
    assert cls.label == "anime"


def test_movie_misfiled_in_tv_library_is_still_detected_as_movie() -> None:
    match = MatchResult(
        title="Interstellar", year=2014, kind="movie", season=None,
        episode_numbers=[], match_status="unresolved",
        library_kind_hint="tv",  # misfiled: a movie sitting in /TV Shows
    )
    files = _mk_files(1)
    tracks = _mk_tracks(["en"], ["en"])
    cls = classify(match, files, tracks)
    assert cls.label == "movie"


def test_live_action_in_anime_library_is_detected_as_tv() -> None:
    """The mirror case: a live-action show misfiled into /Anime."""
    match = MatchResult(
        title="Pretty Little Liars", year=2010, kind="show", season=7,
        episode_numbers=[1], match_status="unresolved",
        library_kind_hint="anime",  # misfiled: sitting in /Anime
        tvdb_id=131791,
    )
    files = _mk_files(20)
    tracks = _mk_tracks(["en"], ["en"])  # no Japanese anything
    cls = classify(match, files, tracks)
    assert cls.label == "tv"


def test_hint_still_decides_when_there_is_no_evidence() -> None:
    """The hint is a tiebreaker, not dead weight: with nothing else to go on
    (no audio tracks, no parsed kind, no ids), the folder still decides."""
    match = MatchResult(
        title="Something Obscure", year=None, kind="unknown", season=None,
        episode_numbers=[], match_status="unmatched", library_kind_hint="tv",
    )
    cls = classify(match, _mk_files(0), _mk_tracks([], []))
    assert cls.label == "tv"
    assert any("tiebreaker" in r.lower() for r in cls.reasons)


def test_confidence_is_a_ratio_against_the_alternatives() -> None:
    """Confidence used to be the raw winning score clipped to 1.0, which said
    nothing about how close the runner-up was."""
    match = MatchResult(
        title="Frieren", year=2023, kind="show", season=1,
        episode_numbers=[1], match_status="unresolved", library_kind_hint="anime",
        anidb_id=17074,
    )
    cls = classify(match, _mk_files(28), _mk_tracks(["ja"], ["en"]))
    assert cls.label == "anime"
    assert 0.0 < cls.confidence <= 1.0
    # Overwhelming anime evidence, essentially nothing for the others.
    assert cls.confidence > 0.8


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
