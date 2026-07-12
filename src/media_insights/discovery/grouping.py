"""Group discovered files into MediaItem -> Season -> MediaFile trees.

The matcher produces a `ParsedTitle`; this module maps the parsed title plus
the source folder hierarchy onto the Library -> MediaItem -> Season -> File
shape the database expects.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from media_insights.discovery.walker import FoundFile


@dataclass(slots=True)
class FileObservation:
    """Everything we know about one file before persisting it."""

    found: FoundFile
    plexmatch_season_number: int | None = None
    plexmatch_episode_numbers: list[int] = field(default_factory=list)
    # guessing produces per-file episode metadata
    guessed_season: int | None = None
    guessed_episodes: list[int] = field(default_factory=list)
    guessed_title: str | None = None
    guessed_year: int | None = None
    guessed_kind: str | None = None  # "movie" or "show"


def group_observations(observations: Iterable[FileObservation]) -> dict[str, list[FileObservation]]:
    """Group observations by the title key they ended up under.

    The matcher assigns each observation to one logical title; this is a
    passthrough that keeps the scanner's lifecycle simple.
    """
    grouped: dict[str, list[FileObservation]] = {}
    for obs in observations:
        title_key = obs.guessed_title or obs.found.parent.name or "Unknown"
        grouped.setdefault(title_key, []).append(obs)
    return grouped
