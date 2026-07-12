"""Provider interface for online metadata lookups.

Default install is offline-only; the local matcher still works without any
provider. This Protocol exists so TMDB / TVDB / AniList can be slotted in later
without touching the matcher.
"""

from __future__ import annotations

from typing import Protocol

from media_insights.matching.parser import ParsedTitle


class LookupResult:
    """A confident candidate from a metadata provider."""

    __slots__ = ("anidb_id", "imdb_id", "kind", "score", "title", "tmdb_id", "tvdb_id", "year")

    def __init__(
        self,
        title: str,
        year: int | None,
        kind: str,
        score: float,
        imdb_id: str | None = None,
        tmdb_id: int | None = None,
        tvdb_id: int | None = None,
        anidb_id: int | None = None,
    ) -> None:
        self.title = title
        self.year = year
        self.kind = kind
        self.score = score
        self.imdb_id = imdb_id
        self.tmdb_id = tmdb_id
        self.tvdb_id = tvdb_id
        self.anidb_id = anidb_id


class Provider(Protocol):
    name: str

    def lookup(self, parsed: ParsedTitle) -> list[LookupResult]:
        """Return ranked candidates; empty list when nothing matches."""
