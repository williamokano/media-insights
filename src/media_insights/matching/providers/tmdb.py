"""TMDB provider: general matching, origin country, and IMDB IDs.

This is also how "IMDB data" gets in: IMDB has no free official API, but TMDB's
`/external_ids` endpoint returns the canonical `imdb_id` for a title, which is
the reliable way to cross-reference it.

The anime judgement here is a two-part test, and both parts matter:
`Animation` genre AND a Japanese origin country. `Animation` alone is what
western cartoons look like (Rick and Morty, The Simpsons, Arcane), so TMDB is
the provider that can say "animated, but explicitly NOT anime" -- an answer
AniList structurally cannot give, since it only indexes anime in the first
place.
"""

from __future__ import annotations

import logging

from media_insights.matching.providers.base import ProviderSignals
from media_insights.matching.providers.http import RateLimiter, request_json, year_matches

log = logging.getLogger(__name__)

BASE = "https://api.themoviedb.org/3"

# TMDB's published limit is generous (~50/s); stay well under it anyway.
_RATE_LIMIT_PER_MINUTE = 120

_ANIMATION_GENRE_ID = 16
_ANIMATION_GENRE_NAME = "animation"


def _is_read_access_token(credential: str) -> bool:
    """TMDB issues two different credentials and shows the wrong one first.

    - the v3 "API Key": a short hex string, passed as ?api_key=...
    - the v4 "API Read Access Token": a JWT, which must be sent as
      `Authorization: Bearer ...` and 401s if passed as api_key.

    The settings page presents the read access token most prominently, so it's
    the one people actually copy. Accept either rather than silently 401ing on
    every lookup and degrading to "no metadata".
    """
    return credential.startswith("eyJ") or credential.count(".") == 2


class TmdbProvider:
    name = "tmdb"

    def __init__(self, api_key: str, timeout: float = 10.0) -> None:
        self._credential = api_key.strip()
        self._use_bearer = _is_read_access_token(self._credential)
        self._timeout = timeout
        self._limiter = RateLimiter(_RATE_LIMIT_PER_MINUTE)

    def _get(self, path: str, **params: object) -> dict | None:
        headers: dict[str, str] = {}
        if self._use_bearer:
            headers["Authorization"] = f"Bearer {self._credential}"
        else:
            params = {"api_key": self._credential, **params}
        return request_json(
            "GET",
            f"{BASE}{path}",
            provider=self.name,
            timeout=self._timeout,
            limiter=self._limiter,
            params=params,
            headers=headers,
        )

    def check(self) -> str | None:
        """Verify the credential actually works. Returns an error, or None if OK."""
        payload = self._get("/configuration")
        if payload is None:
            kind = "read access token (v4)" if self._use_bearer else "api key (v3)"
            return f"TMDB rejected the {kind} (or is unreachable)"
        return None

    def lookup(self, title: str, year: int | None, kind: str | None) -> ProviderSignals | None:
        media_type = "movie" if kind == "movie" else "tv"
        payload = self._get(f"/search/{media_type}", query=title)
        if not payload:
            return None
        results = payload.get("results") or []
        if not results:
            return None

        best = _best_candidate(results, year, media_type)
        if best is None:
            return None

        tmdb_id = best.get("id")
        genre_ids = best.get("genre_ids") or []
        origin = _origin_country(best, media_type)
        is_animation = _ANIMATION_GENRE_ID in genre_ids

        # Animated + Japanese origin = anime. Animated + anywhere else = a
        # western cartoon, which is a definite "not anime", not a shrug.
        if is_animation:
            is_anime: bool | None = origin == "JP"
        else:
            is_anime = False

        imdb_id = self._imdb_id(media_type, tmdb_id) if tmdb_id else None

        return ProviderSignals(
            source=self.name,
            title=best.get("name") or best.get("title"),
            year=_year_of(best, media_type),
            kind="movie" if media_type == "movie" else "show",
            is_anime=is_anime,
            origin_country=origin,
            genres=[_ANIMATION_GENRE_NAME] if is_animation else [],
            tmdb_id=tmdb_id,
            imdb_id=imdb_id,
        )

    def _imdb_id(self, media_type: str, tmdb_id: int) -> str | None:
        payload = self._get(f"/{media_type}/{tmdb_id}/external_ids")
        if not payload:
            return None
        imdb_id = payload.get("imdb_id")
        return imdb_id or None


def _year_of(candidate: dict, media_type: str) -> int | None:
    raw = candidate.get("release_date" if media_type == "movie" else "first_air_date") or ""
    try:
        return int(raw[:4])
    except (TypeError, ValueError):
        return None


def _origin_country(candidate: dict, media_type: str) -> str | None:
    countries = candidate.get("origin_country") or []
    if countries:
        return str(countries[0])
    # Movies don't carry origin_country in search results; the original
    # language is the closest available proxy.
    if media_type == "movie" and candidate.get("original_language") == "ja":
        return "JP"
    return None


def _best_candidate(results: list[dict], year: int | None, media_type: str) -> dict | None:
    if year is not None:
        exact = [c for c in results if year_matches(year, _year_of(c, media_type), tolerance=1)]
        if exact:
            return exact[0]
    return results[0]
