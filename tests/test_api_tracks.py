"""GET /api/tracks and GET /api/items missing_*_language filter tests.

Seeds Library/MediaItem/Season/MediaFile/Track directly via the ORM --
no ffmpeg/scanner dependency, so these always run.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from media_insights.db import session_scope
from media_insights.models import Library, MediaFile, MediaItem, Season, Track


def _setup_app() -> TestClient:
    import tempfile

    from media_insights.api import configure, create_app
    from media_insights.config import (
        AppConfig,
        DatabaseConfig,
        FingerprintConfig,
        ScheduleConfig,
        WatcherConfig,
    )
    from media_insights.db import init_engine, run_migrations

    tmpdir = tempfile.mkdtemp(prefix="mi-api-tracks-")
    db_url = f"sqlite:///{tmpdir}/test.db"
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
    init_engine(db_url)
    run_migrations(db_url)
    configure(cfg)
    return TestClient(create_app())


def _seed() -> dict:
    """One library, two items, each with one season/file. Item A has English
    audio + English subtitle; item B has Japanese audio and no subtitles."""
    with session_scope() as session:
        library = Library(name="Movies", path="/data/movies", kind="movie")
        session.add(library)
        session.flush()

        item_a = MediaItem(library_id=library.id, kind="movie", title="Alpha", year=2020)
        item_b = MediaItem(library_id=library.id, kind="movie", title="Beta", year=2021)
        session.add_all([item_a, item_b])
        session.flush()

        season_a = Season(item_id=item_a.id, number=None)
        season_b = Season(item_id=item_b.id, number=None)
        session.add_all([season_a, season_b])
        session.flush()

        file_a = MediaFile(season_id=season_a.id, path="/data/movies/Alpha.mkv")
        file_b = MediaFile(season_id=season_b.id, path="/data/movies/Beta.mkv")
        session.add_all([file_a, file_b])
        session.flush()

        session.add_all(
            [
                Track(
                    file_id=file_a.id, position=0, kind="audio", codec="aac",
                    language="en", language_raw="eng",
                ),
                Track(
                    file_id=file_a.id, position=1, kind="subtitle", codec="subrip",
                    language="en", language_raw="eng", is_forced=True,
                ),
                Track(
                    file_id=file_b.id, position=0, kind="audio", codec="aac",
                    language="ja", language_raw="jpn",
                ),
            ]
        )
        session.commit()
        return {
            "library_id": library.id,
            "item_a_id": item_a.id,
            "item_b_id": item_b.id,
            "file_a_id": file_a.id,
            "file_b_id": file_b.id,
        }


def test_list_tracks_empty_db() -> None:
    client = _setup_app()
    r = client.get("/api/tracks")
    assert r.status_code == 200
    assert r.json() == {"tracks": []}


def test_list_tracks_filter_by_kind() -> None:
    client = _setup_app()
    ids = _seed()
    r = client.get("/api/tracks", params={"kind": "subtitle"})
    assert r.status_code == 200
    tracks = r.json()["tracks"]
    assert len(tracks) == 1
    assert tracks[0]["kind"] == "subtitle"
    assert tracks[0]["file_id"] == ids["file_a_id"]
    assert tracks[0]["item_id"] == ids["item_a_id"]
    assert tracks[0]["item_title"] == "Alpha"
    assert tracks[0]["file_path"] == "/data/movies/Alpha.mkv"
    assert tracks[0]["library_id"] == ids["library_id"]


def test_list_tracks_filter_by_language_normalized() -> None:
    client = _setup_app()
    _seed()
    r = client.get("/api/tracks", params={"language": "ja"})
    tracks = r.json()["tracks"]
    assert len(tracks) == 1
    assert tracks[0]["language"] == "ja"
    assert tracks[0]["language_raw"] == "jpn"
    assert tracks[0]["language_display"] == "Japanese"


def test_list_tracks_filter_by_language_raw() -> None:
    client = _setup_app()
    _seed()
    r = client.get("/api/tracks", params={"language_raw": "jpn"})
    tracks = r.json()["tracks"]
    assert len(tracks) == 1
    assert tracks[0]["language_raw"] == "jpn"


def test_list_tracks_filter_by_is_forced() -> None:
    client = _setup_app()
    _seed()
    r = client.get("/api/tracks", params={"is_forced": "true"})
    tracks = r.json()["tracks"]
    assert len(tracks) == 1
    assert tracks[0]["kind"] == "subtitle"


def test_list_tracks_empty_string_library_and_item_treated_as_unset() -> None:
    """?library=&item= (e.g. from a client that always sends the param,
    empty when unset) must behave like the param was omitted, not 422."""
    client = _setup_app()
    _seed()
    r = client.get("/api/tracks", params={"library": "", "item": ""})
    assert r.status_code == 200
    assert len(r.json()["tracks"]) == 3


def test_items_empty_string_library_treated_as_unset() -> None:
    client = _setup_app()
    _seed()
    r = client.get("/api/items", params={"library": ""})
    assert r.status_code == 200
    assert len(r.json()["items"]) == 2


def test_list_tracks_filter_by_library_and_item() -> None:
    client = _setup_app()
    ids = _seed()
    r = client.get("/api/tracks", params={"library": ids["library_id"]})
    assert len(r.json()["tracks"]) == 3
    r = client.get("/api/tracks", params={"item": ids["item_b_id"]})
    tracks = r.json()["tracks"]
    assert len(tracks) == 1
    assert tracks[0]["item_id"] == ids["item_b_id"]


def test_list_tracks_pagination() -> None:
    client = _setup_app()
    _seed()
    r = client.get("/api/tracks", params={"limit": 1, "offset": 0})
    assert len(r.json()["tracks"]) == 1
    r = client.get("/api/tracks", params={"limit": 1, "offset": 2})
    assert len(r.json()["tracks"]) == 1
    r = client.get("/api/tracks", params={"limit": 10, "offset": 3})
    assert r.json()["tracks"] == []


def test_items_missing_subtitle_language_filter() -> None:
    client = _setup_app()
    ids = _seed()
    r = client.get("/api/items", params={"missing_subtitle_language": "en"})
    assert r.status_code == 200
    titles = {item["title"] for item in r.json()["items"]}
    # Alpha has an English subtitle; Beta has none -- only Beta is "missing".
    assert titles == {"Beta"}
    assert ids  # keep ids referenced for clarity


def test_items_missing_audio_language_filter() -> None:
    client = _setup_app()
    _seed()
    r = client.get("/api/items", params={"missing_audio_language": "ja"})
    titles = {item["title"] for item in r.json()["items"]}
    # Only Beta has Japanese audio, so it's excluded; Alpha is "missing" it.
    assert titles == {"Alpha"}


def test_get_file_returns_split_track_lists_not_flat_tracks() -> None:
    client = _setup_app()
    ids = _seed()
    r = client.get(f"/api/files/{ids['file_a_id']}")
    assert r.status_code == 200
    data = r.json()
    assert "tracks" not in data
    assert len(data["audio_tracks"]) == 1
    assert len(data["subtitle_tracks"]) == 1
    assert data["video_tracks"] == []
    assert data["audio_tracks"][0]["language_display"] == "English"


def test_get_item_returns_split_track_lists() -> None:
    client = _setup_app()
    ids = _seed()
    r = client.get(f"/api/items/{ids['item_a_id']}")
    assert r.status_code == 200
    files = r.json()["files"]
    assert len(files) == 1
    assert "tracks" not in files[0]
    assert len(files[0]["audio_tracks"]) == 1
    assert len(files[0]["subtitle_tracks"]) == 1
