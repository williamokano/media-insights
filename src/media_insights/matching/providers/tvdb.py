"""TVDB v4 provider: series identity + an explicit `Anime` genre.

v4 is token-based: the API key is exchanged at /login for a bearer token
(valid ~a month), which is then sent on every request. The token is cached in
memory for the process lifetime; a 401 simply means the next lookup logs in
again.

TVDB tags anime with a literal `Anime` genre, which makes its answer directly
usable -- but it is only consulted as corroboration, since its coverage of
anime is patchier than AniList's.
"""

from __future__ import annotations

import logging
import threading

from media_insights.matching.providers.base import ProviderSignals
from media_insights.matching.providers.http import RateLimiter, request_json, year_matches

log = logging.getLogger(__name__)

BASE = "https://api4.thetvdb.com/v4"

_RATE_LIMIT_PER_MINUTE = 120


class TvdbProvider:
    name = "tvdb"

    def __init__(self, api_key: str, pin: str = "", timeout: float = 10.0) -> None:
        self._api_key = api_key
        self._pin = pin
        self._timeout = timeout
        self._limiter = RateLimiter(_RATE_LIMIT_PER_MINUTE)
        self._token: str | None = None
        self._lock = threading.Lock()

    def _login(self) -> str | None:
        body: dict[str, str] = {"apikey": self._api_key}
        if self._pin:
            body["pin"] = self._pin
        payload = request_json(
            "POST",
            f"{BASE}/login",
            provider=self.name,
            timeout=self._timeout,
            limiter=self._limiter,
            json=body,
        )
        if not payload:
            return None
        token = (payload.get("data") or {}).get("token")
        if not token:
            log.warning("tvdb: login returned no token")
            return None
        return str(token)

    def _bearer(self) -> str | None:
        with self._lock:
            if self._token is None:
                self._token = self._login()
            return self._token

    def check(self) -> str | None:
        """Verify the api key can actually log in."""
        with self._lock:
            self._token = None  # force a fresh login rather than trusting a cached token
        return None if self._bearer() else "TVDB rejected the api key (or is unreachable)"

    def lookup(self, title: str, year: int | None, kind: str | None) -> ProviderSignals | None:
        token = self._bearer()
        if not token:
            return None

        params: dict[str, object] = {
            "query": title,
            "type": "movie" if kind == "movie" else "series",
        }
        if year is not None:
            params["year"] = year

        payload = request_json(
            "GET",
            f"{BASE}/search",
            provider=self.name,
            timeout=self._timeout,
            limiter=self._limiter,
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )
        if payload is None:
            # Most likely an expired token: drop it so the next call re-logs in.
            with self._lock:
                self._token = None
            return None

        results = payload.get("data") or []
        if not results:
            return None

        best = _best_candidate(results, year)
        if best is None:
            return None

        genres = [str(g).lower() for g in (best.get("genres") or [])]
        is_anime = "anime" in genres if genres else None

        return ProviderSignals(
            source=self.name,
            title=best.get("name"),
            year=_year_of(best),
            kind="movie" if best.get("type") == "movie" else "show",
            is_anime=is_anime,
            origin_country=(best.get("country") or None),
            genres=genres,
            tvdb_id=_int_or_none(best.get("tvdb_id")),
            imdb_id=(best.get("remote_ids") or {}).get("imdb") if isinstance(best.get("remote_ids"), dict) else None,
        )


def _int_or_none(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _year_of(candidate: dict) -> int | None:
    return _int_or_none(candidate.get("year"))


def _best_candidate(results: list[dict], year: int | None) -> dict | None:
    if year is not None:
        exact = [c for c in results if year_matches(year, _year_of(c), tolerance=1)]
        if exact:
            return exact[0]
    return results[0]
