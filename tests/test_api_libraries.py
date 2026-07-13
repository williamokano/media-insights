"""API tests for library management (POST/PUT/DELETE /api/libraries)."""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from media_insights.api import configure, create_app
from media_insights.config import AppConfig, DatabaseConfig, ScheduleConfig, WatcherConfig
from media_insights.db import init_engine, run_migrations


def _setup_app() -> tuple[TestClient, Path, Path, Path]:
    """Real config.yaml on disk + real data dirs, so persistence is observable."""
    tmpdir = Path(tempfile.mkdtemp(prefix="mi-api-lib-"))
    data_dir = tmpdir / "data"
    movies_dir = data_dir / "movies"
    movies_dir.mkdir(parents=True)
    tv_dir = data_dir / "tv"
    tv_dir.mkdir(parents=True)

    config_path = tmpdir / "config.yaml"
    config_path.write_text(
        "# managed by tests\n"
        f"config_dir: {tmpdir}\n"
        f"data_dir: {data_dir}\n"
        "libraries: []\n",
        encoding="utf-8",
    )

    db_url = f"sqlite:///{tmpdir}/test.db"
    cfg = AppConfig(
        config_dir=str(tmpdir),
        data_dir=str(data_dir),
        log_level="WARNING",
        database=DatabaseConfig(url=db_url),
        watcher=WatcherConfig(enabled=False),
        schedule=ScheduleConfig(enabled=False),
        libraries=[],
    )
    init_engine(db_url)
    run_migrations(db_url)
    configure(cfg, config_path)
    return TestClient(create_app()), config_path, movies_dir, tv_dir


def _create_movies(client: TestClient, movies_dir: Path) -> int:
    r = client.post("/api/libraries", json={"name": "Movies", "path": str(movies_dir), "kind": "movie"})
    assert r.status_code == 201
    return r.json()["id"]


def test_create_library_persists_to_yaml_and_db() -> None:
    client, config_path, _, tv_dir = _setup_app()

    r = client.post("/api/libraries", json={"name": "TV", "path": str(tv_dir), "kind": "tv"})
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "TV"
    assert body["configured"] is True

    # visible immediately via GET, before any scan has run
    r = client.get("/api/libraries")
    names = {lib["name"] for lib in r.json()["libraries"]}
    assert "TV" in names

    text = config_path.read_text(encoding="utf-8")
    assert "name: TV" in text
    assert "# managed by tests" in text  # comment survived


def test_create_library_duplicate_name_conflicts() -> None:
    client, _, movies_dir, tv_dir = _setup_app()
    _create_movies(client, movies_dir)
    r = client.post("/api/libraries", json={"name": "Movies", "path": str(tv_dir), "kind": "tv"})
    assert r.status_code == 409


def test_create_library_when_config_file_does_not_exist_yet() -> None:
    """Fresh install repro: no config.yaml on disk at all before the first add."""
    tmpdir = Path(tempfile.mkdtemp(prefix="mi-api-lib-nofile-"))
    movies_dir = tmpdir / "data" / "movies"
    movies_dir.mkdir(parents=True)
    config_path = tmpdir / "config.yaml"
    assert not config_path.exists()

    db_url = f"sqlite:///{tmpdir}/test.db"
    cfg = AppConfig(
        config_dir=str(tmpdir),
        watcher=WatcherConfig(enabled=False),
        schedule=ScheduleConfig(enabled=False),
        database=DatabaseConfig(url=db_url),
        libraries=[],
    )
    init_engine(db_url)
    run_migrations(db_url)
    configure(cfg, config_path)
    client = TestClient(create_app())

    r = client.post("/api/libraries", json={"name": "Movies", "path": str(movies_dir), "kind": "movie"})
    assert r.status_code == 201
    assert config_path.is_file()
    assert "name: Movies" in config_path.read_text(encoding="utf-8")


def test_create_library_when_config_file_is_empty() -> None:
    """Same repro, but with an empty file already sitting there."""
    tmpdir = Path(tempfile.mkdtemp(prefix="mi-api-lib-empty-"))
    movies_dir = tmpdir / "data" / "movies"
    movies_dir.mkdir(parents=True)
    config_path = tmpdir / "config.yaml"
    config_path.touch()

    db_url = f"sqlite:///{tmpdir}/test.db"
    cfg = AppConfig(
        config_dir=str(tmpdir),
        watcher=WatcherConfig(enabled=False),
        schedule=ScheduleConfig(enabled=False),
        database=DatabaseConfig(url=db_url),
        libraries=[],
    )
    init_engine(db_url)
    run_migrations(db_url)
    configure(cfg, config_path)
    client = TestClient(create_app())

    r = client.post("/api/libraries", json={"name": "Movies", "path": str(movies_dir), "kind": "movie"})
    assert r.status_code == 201
    assert "name: Movies" in config_path.read_text(encoding="utf-8")


def test_create_library_missing_path_rejected() -> None:
    client, _, _, _ = _setup_app()
    r = client.post("/api/libraries", json={"name": "Ghost", "path": "/no/such/dir", "kind": "auto"})
    assert r.status_code == 400
    assert "does not exist" in r.json()["detail"]


def test_create_library_invalid_kind_rejected() -> None:
    client, _, _, tv_dir = _setup_app()
    r = client.post("/api/libraries", json={"name": "X", "path": str(tv_dir), "kind": "sitcom"})
    assert r.status_code == 422


def test_update_library_renames_and_persists() -> None:
    client, config_path, movies_dir, _ = _setup_app()
    lib_id = _create_movies(client, movies_dir)

    r = client.put(
        f"/api/libraries/{lib_id}",
        json={"name": "Films", "path": str(movies_dir), "kind": "movie"},
    )
    assert r.status_code == 200
    assert r.json()["name"] == "Films"

    text = config_path.read_text(encoding="utf-8")
    assert "name: Films" in text
    assert "name: Movies" not in text


def test_update_unknown_library_404() -> None:
    client, _, _, tv_dir = _setup_app()
    r = client.put("/api/libraries/9999", json={"name": "X", "path": str(tv_dir), "kind": "auto"})
    assert r.status_code == 404


def test_delete_soft_keeps_data_marks_unconfigured() -> None:
    client, config_path, movies_dir, _ = _setup_app()
    lib_id = _create_movies(client, movies_dir)

    r = client.delete(f"/api/libraries/{lib_id}")
    assert r.status_code == 204

    r = client.get("/api/libraries")
    libs = r.json()["libraries"]
    assert len(libs) == 1  # DB row kept
    assert libs[0]["configured"] is False

    text = config_path.read_text(encoding="utf-8")
    assert "name: Movies" not in text  # removed from YAML


def test_delete_purge_removes_row_and_yaml() -> None:
    client, config_path, movies_dir, _ = _setup_app()
    lib_id = _create_movies(client, movies_dir)

    r = client.delete(f"/api/libraries/{lib_id}?purge=true")
    assert r.status_code == 204

    r = client.get("/api/libraries")
    assert r.json()["libraries"] == []
    text = config_path.read_text(encoding="utf-8")
    assert "name: Movies" not in text


def test_delete_unknown_library_404() -> None:
    client, _, _, _ = _setup_app()
    r = client.delete("/api/libraries/9999")
    assert r.status_code == 404
