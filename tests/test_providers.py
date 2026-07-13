"""Provider tests, against canned payloads -- never the live network.

The cases here are the ones that actually decide whether a library gets
classified correctly:

  - a real anime resolves and is JP                       -> anime
  - a western cartoon isn't in AniList at all             -> no anime signal
  - TMDB: Animation + JP                                  -> anime
  - TMDB: Animation + US (Rick and Morty, Arcane)         -> explicitly NOT anime
  - a live-action remake of an anime (year mismatch)      -> NOT anime
  - a provider that times out / errors / returns junk     -> degrades, never raises
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from media_insights.matching.providers import lookup_all
from media_insights.matching.providers.anilist import AniListProvider
from media_insights.matching.providers.base import ProviderSignals
from media_insights.matching.providers.tmdb import TmdbProvider
from media_insights.matching.providers.tvdb import TvdbProvider


def _anilist_page(media: list[dict]) -> dict:
    return {"data": {"Page": {"media": media}}}


_FRIEREN = {
    "id": 154587,
    "title": {"romaji": "Sousou no Frieren", "english": "Frieren"},
    "format": "TV",
    "startDate": {"year": 2023},
    "countryOfOrigin": "JP",
    "genres": ["Adventure", "Drama", "Fantasy"],
}

_ONE_PIECE_ANIME = {
    "id": 21,
    "title": {"romaji": "ONE PIECE"},
    "format": "TV",
    "startDate": {"year": 1999},
    "countryOfOrigin": "JP",
    "genres": ["Action", "Adventure"],
}


@pytest.fixture(autouse=True)
def _no_throttle(monkeypatch):
    """The real limiter sleeps 2s between AniList calls (30/min). Not in tests."""
    monkeypatch.setattr("media_insights.matching.providers.http.time.sleep", lambda _: None)
    monkeypatch.setattr(
        "media_insights.matching.providers.http.RateLimiter.wait", lambda self: None
    )


def _stub_request(monkeypatch, handler) -> None:
    monkeypatch.setattr("media_insights.matching.providers.http.httpx.request", handler)


def _json_response(payload: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status_code=status, json=payload, request=httpx.Request("GET", "http://t"))


# --------------------------------------------------------------------------
# AniList
# --------------------------------------------------------------------------


def test_anilist_identifies_a_real_anime(monkeypatch) -> None:
    _stub_request(monkeypatch, lambda *a, **k: _json_response(_anilist_page([_FRIEREN])))
    signals = AniListProvider().lookup("Frieren", 2023, "show")
    assert signals is not None
    assert signals.is_anime is True
    assert signals.origin_country == "JP"
    assert signals.anilist_id == 154587
    assert signals.kind == "show"


def test_anilist_miss_means_not_in_any_anime_database(monkeypatch) -> None:
    """Western cartoons (Secret Level, Arcane, Castlevania) return an empty
    page -- verified against the live API. A miss is itself information."""
    _stub_request(monkeypatch, lambda *a, **k: _json_response(_anilist_page([])))
    assert AniListProvider().lookup("Secret Level", 2024, "show") is None


def test_anilist_live_action_remake_is_not_anime(monkeypatch) -> None:
    """Netflix's live-action One Piece (2023) still resolves against the 1999
    anime on a title search. Without the year check every live-action
    adaptation would be mislabelled anime."""
    _stub_request(monkeypatch, lambda *a, **k: _json_response(_anilist_page([_ONE_PIECE_ANIME])))
    signals = AniListProvider().lookup("One Piece", 2023, "show")
    assert signals is not None
    assert signals.is_anime is False  # years disagree: 1999 vs 2023
    assert signals.year == 1999


def test_anilist_picks_the_candidate_whose_year_matches(monkeypatch) -> None:
    older = dict(_ONE_PIECE_ANIME)
    newer = {**_ONE_PIECE_ANIME, "id": 999, "startDate": {"year": 2023}}
    _stub_request(monkeypatch, lambda *a, **k: _json_response(_anilist_page([older, newer])))
    signals = AniListProvider().lookup("One Piece", 2023, "show")
    assert signals is not None
    assert signals.anilist_id == 999


def test_anilist_non_japanese_origin_is_not_anime(monkeypatch) -> None:
    """AniList also carries Korean/Chinese animation; that isn't `anime` here."""
    donghua = {**_FRIEREN, "countryOfOrigin": "CN"}
    _stub_request(monkeypatch, lambda *a, **k: _json_response(_anilist_page([donghua])))
    signals = AniListProvider().lookup("Some Donghua", 2023, "show")
    assert signals is not None
    assert signals.is_anime is False


# --------------------------------------------------------------------------
# TMDB
# --------------------------------------------------------------------------


def _tmdb_handler(search_payload: dict, external_ids: dict | None = None):
    def handler(method, url, **kwargs):
        if "/external_ids" in url:
            return _json_response(external_ids or {})
        return _json_response(search_payload)
    return handler


def test_tmdb_animation_plus_japanese_origin_is_anime(monkeypatch) -> None:
    _stub_request(monkeypatch, _tmdb_handler(
        {"results": [{"id": 1, "name": "Frieren", "genre_ids": [16],
                      "origin_country": ["JP"], "first_air_date": "2023-09-29"}]},
        {"imdb_id": "tt22248376"},
    ))
    signals = TmdbProvider("key").lookup("Frieren", 2023, "show")
    assert signals is not None
    assert signals.is_anime is True
    assert signals.imdb_id == "tt22248376"  # this is how IMDB identity gets in
    assert signals.tmdb_id == 1


def test_tmdb_animation_without_japanese_origin_is_explicitly_not_anime(monkeypatch) -> None:
    """The western-cartoon discriminator, and the answer AniList structurally
    cannot give: Rick and Morty is animated but is definitively not anime."""
    _stub_request(monkeypatch, _tmdb_handler(
        {"results": [{"id": 2, "name": "Rick and Morty", "genre_ids": [16],
                      "origin_country": ["US"], "first_air_date": "2013-12-02"}]},
        {"imdb_id": "tt2861424"},
    ))
    signals = TmdbProvider("key").lookup("Rick and Morty", 2013, "show")
    assert signals is not None
    assert signals.is_anime is False


def test_tmdb_live_action_is_not_anime(monkeypatch) -> None:
    _stub_request(monkeypatch, _tmdb_handler(
        {"results": [{"id": 3, "name": "Pretty Little Liars", "genre_ids": [18],
                      "origin_country": ["US"], "first_air_date": "2010-06-08"}]},
    ))
    signals = TmdbProvider("key").lookup("Pretty Little Liars", 2010, "show")
    assert signals is not None
    assert signals.is_anime is False


def test_tmdb_no_results(monkeypatch) -> None:
    _stub_request(monkeypatch, _tmdb_handler({"results": []}))
    assert TmdbProvider("key").lookup("Nonexistent", None, "show") is None


# --------------------------------------------------------------------------
# TVDB
# --------------------------------------------------------------------------


def _tvdb_handler(search_payload: dict):
    def handler(method, url, **kwargs):
        if url.endswith("/login"):
            return _json_response({"data": {"token": "tok"}})
        return _json_response(search_payload)
    return handler


def test_tvdb_anime_genre(monkeypatch) -> None:
    _stub_request(monkeypatch, _tvdb_handler(
        {"data": [{"name": "Frieren", "year": "2023", "type": "series",
                   "genres": ["Animation", "Anime"], "tvdb_id": "424536"}]}
    ))
    signals = TvdbProvider("key").lookup("Frieren", 2023, "show")
    assert signals is not None
    assert signals.is_anime is True
    assert signals.tvdb_id == 424536


def test_tvdb_without_anime_genre(monkeypatch) -> None:
    _stub_request(monkeypatch, _tvdb_handler(
        {"data": [{"name": "The Boys", "year": "2019", "type": "series",
                   "genres": ["Drama"], "tvdb_id": "355567"}]}
    ))
    signals = TvdbProvider("key").lookup("The Boys", 2019, "show")
    assert signals is not None
    assert signals.is_anime is False


def test_tvdb_login_failure_degrades_to_none(monkeypatch) -> None:
    def handler(method, url, **kwargs):
        return _json_response({"message": "unauthorized"}, status=401)
    _stub_request(monkeypatch, handler)
    assert TvdbProvider("bad-key").lookup("Frieren", 2023, "show") is None


# --------------------------------------------------------------------------
# Soft failure: a provider must never break a scan
# --------------------------------------------------------------------------


def test_network_error_degrades_to_none(monkeypatch) -> None:
    def boom(*a, **k):
        raise httpx.ConnectError("no route to host")
    _stub_request(monkeypatch, boom)
    assert AniListProvider().lookup("Frieren", 2023, "show") is None


def test_timeout_degrades_to_none(monkeypatch) -> None:
    def slow(*a, **k):
        raise httpx.ReadTimeout("too slow")
    _stub_request(monkeypatch, slow)
    assert AniListProvider().lookup("Frieren", 2023, "show") is None


def test_server_error_degrades_to_none(monkeypatch) -> None:
    _stub_request(monkeypatch, lambda *a, **k: _json_response({"err": 1}, status=503))
    assert AniListProvider().lookup("Frieren", 2023, "show") is None


def test_malformed_payload_degrades_to_none(monkeypatch) -> None:
    _stub_request(monkeypatch, lambda *a, **k: _json_response({"unexpected": "shape"}))
    assert AniListProvider().lookup("Frieren", 2023, "show") is None


def test_lookup_all_survives_a_provider_that_raises() -> None:
    """Belt and braces: even a provider that throws outright is ignored, not
    allowed to take the scan down with it."""

    class Exploding:
        name = "boom"

        def lookup(self, title, year, kind):
            raise RuntimeError("provider bug")

    class Working:
        name = "ok"

        def lookup(self, title, year, kind):
            return ProviderSignals(source="ok", is_anime=True, origin_country="JP")

    merged = lookup_all([Exploding(), Working()], "Frieren", 2023, "show")  # type: ignore[list-item]
    assert merged is not None
    assert merged.is_anime is True


# --------------------------------------------------------------------------
# Merging
# --------------------------------------------------------------------------


def test_lookup_all_merges_ids_across_providers() -> None:
    class A:
        name = "anilist"

        def lookup(self, title, year, kind):
            return ProviderSignals(source="anilist", is_anime=True, anilist_id=1, origin_country="JP")

    class B:
        name = "tmdb"

        def lookup(self, title, year, kind):
            return ProviderSignals(source="tmdb", is_anime=True, imdb_id="tt1", tmdb_id=2)

    merged = lookup_all([A(), B()], "Frieren", 2023, "show")  # type: ignore[list-item]
    assert merged is not None
    assert merged.anilist_id == 1
    assert merged.imdb_id == "tt1"  # filled from the second provider
    assert merged.tmdb_id == 2


def test_lookup_all_first_opinion_on_is_anime_wins() -> None:
    """AniList is registered first precisely because it's the authority."""

    class Authoritative:
        name = "anilist"

        def lookup(self, title, year, kind):
            return ProviderSignals(source="anilist", is_anime=True, origin_country="JP")

    class Vaguer:
        name = "tvdb"

        def lookup(self, title, year, kind):
            return ProviderSignals(source="tvdb", is_anime=False)

    merged = lookup_all([Authoritative(), Vaguer()], "Frieren", 2023, "show")  # type: ignore[list-item]
    assert merged is not None
    assert merged.is_anime is True
