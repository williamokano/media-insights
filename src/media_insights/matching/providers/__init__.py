"""Provider registry.

Providers are consulted in priority order and their answers merged, rather
than taking the first hit: they know different things. AniList is the
authority on *is this anime*, TMDB is the one that can say *animated but
explicitly not anime* (a western cartoon) and supplies the IMDB id, TVDB
corroborates and supplies the tvdb id.

`enabled_providers()` returns an empty list unless the user has explicitly
turned providers on -- this tool indexes a library perfectly well with no
network access at all, and that stays the default.
"""

from __future__ import annotations

import logging

from media_insights.config import ProvidersConfig
from media_insights.matching.providers.anilist import AniListProvider
from media_insights.matching.providers.base import Provider, ProviderSignals
from media_insights.matching.providers.tmdb import TmdbProvider
from media_insights.matching.providers.tvdb import TvdbProvider

log = logging.getLogger(__name__)

__all__ = [
    "AniListProvider",
    "Provider",
    "ProviderSignals",
    "TmdbProvider",
    "TvdbProvider",
    "enabled_providers",
    "lookup_all",
]


def enabled_providers(cfg: ProvidersConfig) -> list[Provider]:
    if not cfg.enabled:
        return []

    providers: list[Provider] = []
    if cfg.anilist.enabled:
        providers.append(AniListProvider(timeout=cfg.timeout_seconds))
    if cfg.tmdb.enabled:
        if cfg.tmdb.api_key:
            providers.append(TmdbProvider(cfg.tmdb.api_key, timeout=cfg.timeout_seconds))
        else:
            log.warning("tmdb is enabled but no api_key is set; skipping it")
    if cfg.tvdb.enabled:
        if cfg.tvdb.api_key:
            providers.append(
                TvdbProvider(cfg.tvdb.api_key, pin=cfg.tvdb.pin, timeout=cfg.timeout_seconds)
            )
        else:
            log.warning("tvdb is enabled but no api_key is set; skipping it")
    return providers


def lookup_all(
    providers: list[Provider], title: str, year: int | None, kind: str | None
) -> ProviderSignals | None:
    """Query every provider and merge what they know into one answer.

    Merge rules:
      - `is_anime`: the first provider with an actual opinion (True or False)
        wins, in registry order -- AniList first, since a hit there is the
        strongest evidence available and a miss there is meaningful too.
      - IDs and descriptive fields: filled from whichever provider has them,
        first one wins, so nothing is lost by a later provider being vaguer.
    """
    merged: ProviderSignals | None = None

    for provider in providers:
        try:
            signals = provider.lookup(title, year, kind)
        except Exception:
            # A provider must never break a scan. http.py already swallows the
            # expected failures; this is the belt-and-braces backstop for
            # anything a provider does wrong on its own.
            log.exception("provider %s raised on %r; ignoring it", provider.name, title)
            continue
        if signals is None:
            continue
        if merged is None:
            merged = signals
            merged.source = provider.name
            continue
        _fill_gaps(merged, signals)

    return merged


def _fill_gaps(merged: ProviderSignals, extra: ProviderSignals) -> None:
    if merged.is_anime is None and extra.is_anime is not None:
        merged.is_anime = extra.is_anime
    for field_name in ("title", "year", "kind", "origin_country",
                       "imdb_id", "tmdb_id", "tvdb_id", "anilist_id"):
        if getattr(merged, field_name) is None:
            value = getattr(extra, field_name)
            if value is not None:
                setattr(merged, field_name, value)
    if not merged.genres and extra.genres:
        merged.genres = list(extra.genres)
