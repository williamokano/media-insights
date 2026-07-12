"""API tests using FastAPI TestClient."""

from __future__ import annotations

from fastapi.testclient import TestClient


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
    from media_insights.db import ensure_schema, init_engine

    tmpdir = tempfile.mkdtemp(prefix="mi-api-")
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
    ensure_schema()
    configure(cfg)
    return TestClient(create_app())


def test_healthz() -> None:
    client = _setup_app()
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_libraries_empty_initially() -> None:
    client = _setup_app()
    r = client.get("/api/libraries")
    assert r.status_code == 200
    assert r.json() == {"libraries": []}


def test_scan_with_no_libraries() -> None:
    client = _setup_app()
    r = client.post("/api/scan")
    assert r.status_code == 200
    assert r.json() == {"libraries": []}


def test_rescan_unknown_path_returns_400() -> None:
    client = _setup_app()
    r = client.post("/api/rescan", json={"path": "/nope/missing.mkv"})
    assert r.status_code == 400
