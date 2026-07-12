"""Lenient .plexmatch parser.

Recognises the documented Plex fields plus a few commonly used extensions:

  Title:    Cowboy Bebop
  Year:     1998
  GUID:     tvdb://71663            (the only fully qualified form)
  imdb:     tt0213338
  tmdbid:   621
  tvdbid:   71663
  anidbid:  23
  ep:       01 03                   (multi-episode files)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

GUID_PREFIXES = ("imdb://", "tmdb://", "tvdb://", "anidb://")


@dataclass(slots=True)
class PlexMatch:
    title: str | None = None
    year: int | None = None
    guid: str | None = None  # raw GUID://id form, kept for traceability
    imdb_id: str | None = None
    tmdb_id: int | None = None
    tvdb_id: int | None = None
    anidb_id: int | None = None
    episode_numbers: list[int] = field(default_factory=list)
    extras: dict[str, str] = field(default_factory=dict)

    @property
    def is_identifying(self) -> bool:
        return bool(
            self.guid or self.imdb_id or self.tmdb_id or self.tvdb_id or self.anidb_id
        )

    @property
    def identified_via(self) -> str | None:
        if self.tvdb_id is not None:
            return "tvdb"
        if self.tmdb_id is not None:
            return "tmdb"
        if self.imdb_id is not None:
            return "imdb"
        if self.anidb_id is not None:
            return "anidb"
        if self.guid:
            return "guid"
        return None


def parse(path: str | Path) -> PlexMatch:
    pm = PlexMatch()
    p = Path(path)
    if not p.is_file():
        return pm
    for raw_line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if not value:
            continue
        _apply_field(pm, key, value)
    return pm


def _apply_field(pm: PlexMatch, key: str, value: str) -> None:
    if key == "title":
        pm.title = value
    elif key == "year":
        try:
            pm.year = int(value)
        except ValueError:
            pass
    elif key == "guid":
        pm.guid = value if value.startswith(GUID_PREFIXES) else None
    elif key == "imdb":
        pm.imdb_id = value
    elif key == "tmdbid":
        try:
            pm.tmdb_id = int(value)
        except ValueError:
            pass
    elif key == "tvdbid":
        try:
            pm.tvdb_id = int(value)
        except ValueError:
            pass
    elif key == "anidbid":
        try:
            pm.anidb_id = int(value)
        except ValueError:
            pass
    elif key in {"ep", "episode", "episodenumber"}:
        for token in value.replace(",", " ").split():
            try:
                pm.episode_numbers.append(int(token))
            except ValueError:
                continue
    else:
        pm.extras[key] = value
