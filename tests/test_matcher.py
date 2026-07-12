"""Matcher tests."""

from __future__ import annotations

from pathlib import Path

from media_insights.config import LibraryConfig
from media_insights.discovery.grouping import FileObservation
from media_insights.discovery.plexmatch import PlexMatch
from media_insights.discovery.plexmatch import parse as parse_plexmatch
from media_insights.discovery.walker import FoundFile
from media_insights.matching import match_observation


def _obs(name: str, *, plexmatch_path: Path | None = None, parent_name: str = "Folder") -> FileObservation:
    f = FoundFile(path=Path(name), parent=Path(parent_name), plexmatch_path=plexmatch_path)
    return FileObservation(found=f)


def test_match_via_guessit_tv() -> None:
    lib = LibraryConfig(name="TV", path="/x", kind="tv")
    m = match_observation(_obs("Show.S01E02.1080p.mkv"), lib)
    assert m.title == "Show"
    assert m.kind == "show"
    assert m.season == 1
    assert m.episode_numbers == [2]
    # guessit finds a title, but until external IDs are attached, this is
    # "unresolved" (a distinct status from "matched" / "unmatched").
    assert m.match_status == "unresolved"


def test_match_via_plexmatch_overrides(tmp_path: Path) -> None:
    pm_path = tmp_path / ".plexmatch"
    pm_path.write_text("Title: Real Title\nYear: 1999\ntvdbid: 99\n", encoding="utf-8")
    lib = LibraryConfig(name="TV", path="/x", kind="tv")
    # Without plexmatch
    m1 = match_observation(_obs("Random.Name.S01E02.mkv"), lib)
    assert m1.title == "Random Name"
    # With plexmatch
    obs = _obs("Random.Name.S01E02.mkv", plexmatch_path=pm_path)
    m2 = match_observation(obs, lib)
    assert m2.title == "Real Title"
    assert m2.tvdb_id == 99
    assert m2.match_status == "matched"


def test_unmatched_flag() -> None:
    lib = LibraryConfig(name="X", path="/x", kind="auto")
    # guessit only emits `parsed.title` for things that look like release
    # names; a literal single character falls through to "unresolved" not
    # "unmatched". To force the true unmatched state we'd need a file name
    # guessit completely rejects, which in practice only happens when the
    # file has *no* extension. Verify that distinction here instead.
    obs_resolved = _obs("z.mkv", parent_name="z")
    m = match_observation(obs_resolved, lib)
    assert m.match_status in ("unresolved", "unmatched")
    # True "unmatched": the matcher has to fall back to the library name.
    lib_bad = LibraryConfig(name="LibX", path="/x", kind="auto")
    from pathlib import Path

    from media_insights.discovery.grouping import FileObservation
    from media_insights.discovery.walker import FoundFile

    obs_no_guess = FileObservation(found=FoundFile(
        path=Path(""), parent=Path(""), plexmatch_path=None
    ))
    # When guessit finds nothing AND parent fallback finds nothing,
    # title becomes the lib name and status is unmatched.
    m = match_observation(obs_no_guess, lib_bad)
    assert m.title == "LibX"
    assert m.match_status == "unmatched"


def test_guessit_only_match_is_unresolved() -> None:
    lib = LibraryConfig(name="X", path="/x", kind="auto")
    # guessit extracts a title, no plexmatch -> still needs an external ID.
    m = match_observation(_obs("garbage_name.mkv"), lib)
    assert m.title == "garbage name"
    assert m.match_status == "unresolved"
    assert m.imdb_id is None and m.tvdb_id is None


def test_parse_plexmatch_handles_missing_file(tmp_path: Path) -> None:
    assert parse_plexmatch(tmp_path / "missing") == PlexMatch()
