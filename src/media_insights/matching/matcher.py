"""Match a FileObservation to a MediaItem identity.

Resolution order:
  1. .plexmatch metadata (highest trust)
  2. guessit parse of the file/folder name (medium trust)
  3. Library hint (kind=auto when nothing else signals)

Anything that survives without an external ID lands as `match_status=unmatched`
in the database, surfaced by the API/UI for manual resolution.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from media_insights.config import LibraryConfig
from media_insights.discovery.grouping import FileObservation
from media_insights.discovery.plexmatch import PlexMatch
from media_insights.discovery.plexmatch import parse as parse_plexmatch
from media_insights.matching.parser import ParsedTitle
from media_insights.matching.parser import parse as parse_guessit

log = logging.getLogger(__name__)


@dataclass(slots=True)
class MatchResult:
    title: str
    year: int | None
    kind: str  # movie | show
    season: int | None
    episode_numbers: list[int]
    match_status: str  # matched | unmatched | manual
    imdb_id: str | None = None
    tmdb_id: int | None = None
    tvdb_id: int | None = None
    anidb_id: int | None = None
    identified_via: str | None = None  # plexmatch | guessit | provider | manual
    library_kind_hint: str = "auto"
    episode_title: str | None = None


def _combine_plexmatch_match(
    pm: PlexMatch, parsed: ParsedTitle, lib: LibraryConfig
) -> MatchResult | None:
    if not pm.is_identifying:
        return None
    kind = _kind(pm, parsed, lib)
    season = None
    episodes: list[int] = []
    if pm.episode_numbers:
        episodes = list(pm.episode_numbers)
    elif parsed.season is not None or parsed.episodes:
        season = parsed.season
        episodes = parsed.episodes
    return MatchResult(
        title=pm.title or parsed.title or _fallback_title(parsed, lib),
        year=pm.year or parsed.year,
        kind=kind,
        season=season,
        episode_numbers=episodes,
        match_status="matched",
        imdb_id=pm.imdb_id,
        tmdb_id=pm.tmdb_id,
        tvdb_id=pm.tvdb_id,
        anidb_id=pm.anidb_id,
        identified_via="plexmatch",
        library_kind_hint=lib.kind,
        episode_title=parsed.episode_title,
    )


def _kind(pm: PlexMatch, parsed: ParsedTitle, lib: LibraryConfig) -> str:
    if lib.kind in ("movie", "tv", "anime"):
        return "show" if lib.kind in ("tv", "anime") else "movie"
    if parsed.kind == "movie":
        return "movie"
    if parsed.kind == "show":
        return "show"
    return "show"  # default to show; the classifier can override later


def _fallback_title(parsed: ParsedTitle, lib: LibraryConfig) -> str:
    if parsed.title:
        return parsed.title
    return lib.name


def match_observation(obs: FileObservation, lib: LibraryConfig) -> MatchResult:
    """Match a single FileObservation against its library."""
    pm = parse_plexmatch(obs.found.plexmatch_path) if obs.found.plexmatch_path else PlexMatch()
    parsed = parse_guessit(obs.found.path.name)
    if not parsed.title:
        # Try parsing the parent folder name as a fallback for movies/anime.
        parent_parsed = parse_guessit(obs.found.parent.name)
        if parent_parsed.title:
            parsed = parent_parsed

    pm_match = _combine_plexmatch_match(pm, parsed, lib)
    if pm_match is not None:
        return pm_match

    if parsed.title:
        # guessit found *something*. Until an external ID is attached, this
        # item is `unresolved` -- surfaced on the queue but distinct from
        # `unmatched`, which means we have *no* information at all.
        status = "unresolved"
        identified_via = "guessit"
    else:
        status = "unmatched"
        identified_via = None

    return MatchResult(
        title=parsed.title or _fallback_title(parsed, lib),
        year=parsed.year,
        kind=_kind(pm, parsed, lib),
        season=parsed.season,
        episode_numbers=parsed.episodes,
        match_status=status,
        identified_via=identified_via,
        library_kind_hint=lib.kind,
        episode_title=parsed.episode_title,
    )
