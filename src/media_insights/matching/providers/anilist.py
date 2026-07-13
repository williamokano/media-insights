"""AniList provider: the anime oracle.

No API key and no signup -- AniList's GraphQL endpoint is public. It indexes
*only* anime, so a hit is itself the signal: western cartoons (Avatar,
Castlevania, Arcane, Secret Level) simply return nothing, which is precisely
the discrimination local file evidence cannot make.

Two things keep that signal honest:
  - `countryOfOrigin` must be JP. AniList carries some Korean/Chinese
    donghua/aeni too, which are not what `anime` means here.
  - The year must line up. AniList resolves "One Piece" to the 1999 anime even
    when the file is Netflix's 2023 live-action series; without the year check
    every live-action adaptation would be mislabelled anime.
"""

from __future__ import annotations

import logging

from media_insights.matching.providers.base import ProviderSignals
from media_insights.matching.providers.http import RateLimiter, request_json, year_matches

log = logging.getLogger(__name__)

ENDPOINT = "https://graphql.anilist.co"

# Verified against the live API's x-ratelimit-limit header: 30/minute.
_RATE_LIMIT_PER_MINUTE = 30

_QUERY = """
query ($search: String) {
  Page(perPage: 5) {
    media(search: $search, type: ANIME) {
      id
      title { romaji english }
      format
      startDate { year }
      countryOfOrigin
      genres
    }
  }
}
"""

# AniList formats that are films rather than series.
_MOVIE_FORMATS = {"MOVIE"}


class AniListProvider:
    name = "anilist"

    def __init__(self, timeout: float = 10.0) -> None:
        self._timeout = timeout
        self._limiter = RateLimiter(_RATE_LIMIT_PER_MINUTE)

    def lookup(self, title: str, year: int | None, kind: str | None) -> ProviderSignals | None:
        payload = request_json(
            "POST",
            ENDPOINT,
            provider=self.name,
            timeout=self._timeout,
            limiter=self._limiter,
            json={"query": _QUERY, "variables": {"search": title}},
            headers={"Content-Type": "application/json"},
        )
        if not payload:
            return None

        try:
            candidates = payload["data"]["Page"]["media"] or []
        except (KeyError, TypeError):
            return None
        if not candidates:
            return None  # not in an anime database at all -- a real signal

        best = _best_candidate(candidates, year)
        if best is None:
            return None

        start_year = (best.get("startDate") or {}).get("year")
        country = best.get("countryOfOrigin")

        # A hit only counts as anime if it's genuinely Japanese *and* the
        # years agree -- otherwise we're most likely looking at the
        # live-action adaptation of an anime, not the anime.
        is_anime = country == "JP" and year_matches(year, start_year, tolerance=1)

        fmt = best.get("format")
        return ProviderSignals(
            source=self.name,
            title=(best.get("title") or {}).get("romaji"),
            year=start_year,
            kind="movie" if fmt in _MOVIE_FORMATS else "show",
            is_anime=is_anime,
            origin_country=country,
            genres=list(best.get("genres") or []),
            anilist_id=best.get("id"),
        )


def _best_candidate(candidates: list[dict], year: int | None) -> dict | None:
    """Prefer the candidate whose year actually matches the file's.

    Searching "One Piece" returns the 1999 series, a 2027 entry and a movie;
    picking the first result blindly would be a coin flip.
    """
    if year is not None:
        exact = [
            c for c in candidates
            if year_matches(year, (c.get("startDate") or {}).get("year"), tolerance=1)
        ]
        if exact:
            return exact[0]
    return candidates[0]
