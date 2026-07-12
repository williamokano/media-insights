"""Plexmatch parsing tests."""

from __future__ import annotations

from pathlib import Path

from media_insights.discovery.plexmatch import parse


def test_minimal_movie(tmp_path: Path) -> None:
    f = tmp_path / ".plexmatch"
    f.write_text("Title: Interstellar\nYear: 2014\n", encoding="utf-8")
    pm = parse(f)
    assert pm.title == "Interstellar"
    assert pm.year == 2014
    assert not pm.is_identifying


def test_full_tv(tmp_path: Path) -> None:
    f = tmp_path / ".plexmatch"
    f.write_text(
        "Title: Cowboy Bebop\nYear: 1998\nGUID: tvdb://71663\ntvdbid: 71663\n"
        "ep: 01 02 03\n",
        encoding="utf-8",
    )
    pm = parse(f)
    assert pm.tvdb_id == 71663
    assert pm.guid == "tvdb://71663"
    assert pm.episode_numbers == [1, 2, 3]
    assert pm.is_identifying
    assert pm.identified_via == "tvdb"


def test_anidb(tmp_path: Path) -> None:
    f = tmp_path / ".plexmatch"
    f.write_text("Title: Frieren\nanidbid: 17074\n", encoding="utf-8")
    pm = parse(f)
    assert pm.anidb_id == 17074
    assert pm.identified_via == "anidb"


def test_unknown_keys_kept_in_extras(tmp_path: Path) -> None:
    f = tmp_path / ".plexmatch"
    f.write_text("Title: X\nfoo: bar\n# ignored comment\n", encoding="utf-8")
    pm = parse(f)
    assert pm.extras.get("foo") == "bar"
