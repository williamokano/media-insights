"""Reclassification + the misfiled worklist.

Both exist to serve the same real situation: a drive migration mixed anime
and TV into the same folders, so the library a title sits in is not
trustworthy evidence of what it is. Classification has to be able to
disagree with the folder, and the disagreements have to be findable.
"""

from __future__ import annotations

import tempfile

from fastapi.testclient import TestClient

from media_insights.api import configure, create_app
from media_insights.config import (
    AppConfig,
    DatabaseConfig,
    FingerprintConfig,
    ScheduleConfig,
    WatcherConfig,
)
from media_insights.db import init_engine, reset_for_tests, run_migrations, session_scope
from media_insights.models import ChangeEvent, Library, MediaFile, MediaItem, Season, Track


def _setup() -> tuple[TestClient, AppConfig]:
    tmpdir = tempfile.mkdtemp(prefix="mi-reclass-")
    db_url = f"sqlite:///{tmpdir}/test.db"
    reset_for_tests()
    init_engine(db_url)
    run_migrations(db_url)
    cfg = AppConfig(
        config_dir=tmpdir,
        data_dir=tmpdir,
        log_level="WARNING",
        database=DatabaseConfig(url=db_url),
        fingerprint=FingerprintConfig(),
        watcher=WatcherConfig(enabled=False),
        schedule=ScheduleConfig(enabled=False),
        libraries=[],
    )
    configure(cfg, tmpdir + "/config.yaml")
    return TestClient(create_app()), cfg


def _seed_anime_inside_tv_library(*, stale_label: str = "tv") -> int:
    """An anime (Japanese audio, English subs) sitting in a kind=tv library,
    carrying the stale `tv` label the old folder-dominant classifier gave it."""
    with session_scope() as session:
        library = Library(name="TV Shows", path="/data/tv", kind="tv")
        session.add(library)
        session.flush()

        item = MediaItem(
            library_id=library.id, kind="show", title="Frieren", year=2023,
            match_status="unresolved", classification_label=stale_label,
            classification_confidence=0.9, classification_reasons=["library kind hint = tv"],
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
                  language="ja", language_raw="jpn"),
            Track(file_id=file.id, position=1, kind="subtitle", codec="subrip",
                  language="en", language_raw="eng"),
        ])
        session.commit()
        return item.id


def test_reclassify_relabels_anime_misfiled_in_tv_library() -> None:
    client, _ = _setup()
    item_id = _seed_anime_inside_tv_library()

    r = client.post("/api/reclassify")
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == 1
    assert body["relabelled"] == 1
    assert body["changes"][0]["from"] == "tv"
    assert body["changes"][0]["to"] == "anime"

    with session_scope() as session:
        item = session.get(MediaItem, item_id)
        assert item is not None
        assert item.classification_label == "anime"


def test_reclassify_records_a_change_event() -> None:
    client, _ = _setup()
    _seed_anime_inside_tv_library()
    client.post("/api/reclassify")

    with session_scope() as session:
        events = session.query(ChangeEvent).filter(ChangeEvent.type == "item.reclassified").all()
        assert len(events) == 1
        assert events[0].old_payload["classification_label"] == "tv"
        assert events[0].new_payload["classification_label"] == "anime"


def test_reclassify_is_idempotent_and_reports_no_change_second_time() -> None:
    client, _ = _setup()
    _seed_anime_inside_tv_library()

    assert client.post("/api/reclassify").json()["relabelled"] == 1
    # Nothing on disk or in the rules changed, so the second pass is a no-op.
    assert client.post("/api/reclassify").json()["relabelled"] == 0


def test_reclassify_never_overrides_a_manual_verdict() -> None:
    client, _ = _setup()
    item_id = _seed_anime_inside_tv_library()
    with session_scope() as session:
        item = session.get(MediaItem, item_id)
        assert item is not None
        item.classification_override = True
        item.classification_label = "tv"  # the human said tv; that's final
        session.commit()

    assert client.post("/api/reclassify").json()["relabelled"] == 0
    with session_scope() as session:
        item = session.get(MediaItem, item_id)
        assert item is not None
        assert item.classification_label == "tv"


def test_misfiled_api_lists_the_disagreement() -> None:
    client, _ = _setup()
    _seed_anime_inside_tv_library()
    client.post("/api/reclassify")  # now labelled anime, still in a tv library

    body = client.get("/api/items", params={"misfiled": "true"}).json()
    assert len(body["items"]) == 1
    assert body["items"][0]["title"] == "Frieren"
    assert body["items"][0]["classification"]["label"] == "anime"


def test_misfiled_excludes_titles_that_agree_with_their_library() -> None:
    client, _ = _setup()
    _seed_anime_inside_tv_library()
    client.post("/api/reclassify")

    # Move the library's declared kind to anime: now they agree, so it's not misfiled.
    with session_scope() as session:
        library = session.query(Library).one()
        library.kind = "anime"
        session.commit()

    body = client.get("/api/items", params={"misfiled": "true"}).json()
    assert body["items"] == []


def test_misfiled_ignores_auto_libraries() -> None:
    """A kind=auto library asserts nothing, so nothing in it can be misfiled."""
    client, _ = _setup()
    _seed_anime_inside_tv_library()
    client.post("/api/reclassify")
    with session_scope() as session:
        library = session.query(Library).one()
        library.kind = "auto"
        session.commit()

    assert client.get("/api/items", params={"misfiled": "true"}).json()["items"] == []


def test_misfiled_page_renders() -> None:
    client, _ = _setup()
    _seed_anime_inside_tv_library()
    client.post("/api/reclassify")

    r = client.get("/misfiled")
    assert r.status_code == 200
    assert "Frieren" in r.text
    assert "<title>Misfiled" in r.text


def test_misfiled_page_empty_state() -> None:
    client, _ = _setup()
    r = client.get("/misfiled")
    assert r.status_code == 200
    assert "Nothing misfiled" in r.text
