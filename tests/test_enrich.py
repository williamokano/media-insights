"""Provider enrichment end-to-end.

The case that matters: an English-dubbed anime with no Japanese audio track,
no fansub tag and no external ID, sitting in a `kind: tv` library. There is
*nothing* in the file to give it away -- local evidence alone will always call
it tv. Only a provider can identify it, which is the entire reason providers
exist here.
"""

from __future__ import annotations

import tempfile

import pytest
from fastapi.testclient import TestClient

from media_insights.api import configure, create_app
from media_insights.config import (
    AniListConfig,
    AppConfig,
    DatabaseConfig,
    ProvidersConfig,
    ScheduleConfig,
    WatcherConfig,
)
from media_insights.db import init_engine, reset_for_tests, run_migrations, session_scope
from media_insights.matching.providers.base import ProviderSignals
from media_insights.models import Library, MediaFile, MediaItem, Season, Track
from media_insights.scanner import enrich_all


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Nothing in this file may touch the network."""
    monkeypatch.setattr(
        "media_insights.matching.providers.http.RateLimiter.wait", lambda self: None
    )


def _cfg(*, providers_enabled: bool = True) -> AppConfig:
    tmpdir = tempfile.mkdtemp(prefix="mi-enrich-")
    db_url = f"sqlite:///{tmpdir}/test.db"
    reset_for_tests()
    init_engine(db_url)
    run_migrations(db_url)
    return AppConfig(
        config_dir=tmpdir,
        data_dir=tmpdir,
        log_level="WARNING",
        database=DatabaseConfig(url=db_url),
        watcher=WatcherConfig(enabled=False),
        schedule=ScheduleConfig(enabled=False),
        providers=ProvidersConfig(
            enabled=providers_enabled, anilist=AniListConfig(enabled=True)
        ),
        libraries=[],
    )


def _seed_dubbed_anime_in_tv_library() -> int:
    """English audio, English subs, plain filename -- locally indistinguishable
    from any western show."""
    with session_scope() as session:
        library = Library(name="TV Shows", path="/data/tv", kind="tv")
        session.add(library)
        session.flush()
        item = MediaItem(
            library_id=library.id, kind="show", title="Frieren", year=2023,
            match_status="unresolved", classification_label="tv",
            classification_confidence=0.5, classification_reasons=["library kind hint = tv"],
        )
        session.add(item)
        session.flush()
        season = Season(item_id=item.id, number=1)
        session.add(season)
        session.flush()
        file = MediaFile(season_id=season.id, path="/data/tv/Frieren/Frieren - S01E01.mkv")
        session.add(file)
        session.flush()
        session.add_all([
            Track(file_id=file.id, position=0, kind="audio", codec="aac",
                  language="en", language_raw="eng", is_default=True),
            Track(file_id=file.id, position=1, kind="subtitle", codec="subrip",
                  language="en", language_raw="eng"),
        ])
        session.commit()
        return item.id


def _fake_anilist(monkeypatch, signals: ProviderSignals | None) -> None:
    class Fake:
        name = "anilist"

        def lookup(self, title, year, kind):
            return signals

    monkeypatch.setattr(
        "media_insights.scanner.service.enabled_providers", lambda cfg: [Fake()]
    )


def test_provider_identifies_a_dubbed_anime_with_no_local_evidence(monkeypatch) -> None:
    cfg = _cfg()
    item_id = _seed_dubbed_anime_in_tv_library()
    _fake_anilist(monkeypatch, ProviderSignals(
        source="anilist", title="Sousou no Frieren", year=2023, kind="show",
        is_anime=True, origin_country="JP", anilist_id=154587,
    ))

    result = enrich_all(cfg)
    assert result["enabled"] is True
    assert result["enriched"] == 1
    assert result["relabelled"] == 1

    with session_scope() as session:
        item = session.get(MediaItem, item_id)
        assert item is not None
        # Locally this looked exactly like a western TV show. Only the provider
        # could tell the difference.
        assert item.classification_label == "anime"
        assert item.provider_source == "anilist"
        assert item.provider_is_anime is True
        assert item.anilist_id == 154587
        assert any("anilist" in r.lower() for r in item.classification_reasons or [])


def test_provider_saying_not_anime_keeps_a_western_cartoon_as_tv(monkeypatch) -> None:
    cfg = _cfg()
    _seed_dubbed_anime_in_tv_library()  # same shape; the provider's answer differs
    _fake_anilist(monkeypatch, ProviderSignals(
        source="tmdb", title="Rick and Morty", year=2013, kind="show",
        is_anime=False, origin_country="US",
    ))

    enrich_all(cfg)
    with session_scope() as session:
        item = session.query(MediaItem).one()
        assert item.classification_label == "tv"


def test_provider_miss_is_cached_so_it_is_not_requeried(monkeypatch) -> None:
    """AniList allows 30 requests/minute. A title that isn't in any provider's
    database must not be looked up again on every single scan."""
    cfg = _cfg()
    _seed_dubbed_anime_in_tv_library()

    calls = {"n": 0}

    class Counting:
        name = "anilist"

        def lookup(self, title, year, kind):
            calls["n"] += 1
            return None  # a miss

    monkeypatch.setattr(
        "media_insights.scanner.service.enabled_providers", lambda cfg: [Counting()]
    )

    enrich_all(cfg)
    assert calls["n"] == 1
    enrich_all(cfg)  # within the TTL: must not hit the provider again
    assert calls["n"] == 1
    enrich_all(cfg, force=True)  # explicit override does re-query
    assert calls["n"] == 2


def test_enrichment_fills_external_ids_and_marks_matched(monkeypatch) -> None:
    cfg = _cfg()
    item_id = _seed_dubbed_anime_in_tv_library()
    _fake_anilist(monkeypatch, ProviderSignals(
        source="tmdb", title="Frieren", year=2023, kind="show", is_anime=True,
        origin_country="JP", imdb_id="tt22248376", tmdb_id=209867,
    ))

    enrich_all(cfg)
    with session_scope() as session:
        item = session.get(MediaItem, item_id)
        assert item is not None
        assert item.imdb_id == "tt22248376"
        assert item.tmdb_id == 209867
        assert item.match_status == "matched"  # drains the unmatched queue


def test_manual_override_survives_enrichment(monkeypatch) -> None:
    cfg = _cfg()
    item_id = _seed_dubbed_anime_in_tv_library()
    with session_scope() as session:
        item = session.get(MediaItem, item_id)
        assert item is not None
        item.classification_override = True
        item.classification_label = "tv"
        session.commit()

    _fake_anilist(monkeypatch, ProviderSignals(
        source="anilist", is_anime=True, origin_country="JP", kind="show", year=2023,
    ))
    enrich_all(cfg)

    with session_scope() as session:
        item = session.get(MediaItem, item_id)
        assert item is not None
        assert item.classification_label == "tv"  # the human still wins


def test_providers_disabled_is_a_no_op() -> None:
    cfg = _cfg(providers_enabled=False)
    _seed_dubbed_anime_in_tv_library()
    result = enrich_all(cfg)
    assert result["enabled"] is False
    assert result["examined"] == 0
    with session_scope() as session:
        assert session.query(MediaItem).one().classification_label == "tv"


def test_enrich_endpoint_reports_disabled() -> None:
    cfg = _cfg(providers_enabled=False)
    configure(cfg, cfg.config_dir + "/config.yaml")
    client = TestClient(create_app())
    body = client.post("/api/enrich").json()
    assert body["enabled"] is False
