"""Config loading and env override tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from media_insights.config import load_config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in list(__import__("os").environ):
        if key.startswith("MI_"):
            monkeypatch.delenv(key, raising=False)


def test_defaults_without_file(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "missing.yaml")
    assert cfg.config_dir == "/config"
    assert cfg.watcher.enabled is True
    assert cfg.libraries == []


def test_yaml_values(tmp_path: Path) -> None:
    f = tmp_path / "config.yaml"
    f.write_text(
        "config_dir: /tmp/cfg\n"
        "database:\n  url: 'sqlite:///{config_dir}/x.db'\n"
        "libraries:\n  - name: A\n    path: /tmp/a\n    kind: anime\n",
        encoding="utf-8",
    )
    cfg = load_config(f)
    assert cfg.config_dir == "/tmp/cfg"
    assert cfg.database.url == "sqlite:////tmp/cfg/x.db"
    assert cfg.libraries[0].kind == "anime"


def test_nested_env_overrides(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MI_DATABASE__URL", "postgresql://envhost/db")
    monkeypatch.setenv("MI_WATCHER__OBSERVER", "polling")
    monkeypatch.setenv("MI_SERVER__PORT", "9999")
    monkeypatch.setenv("MI_LOG_LEVEL", "DEBUG")
    cfg = load_config(tmp_path / "missing.yaml")
    assert cfg.database.url == "postgresql://envhost/db"
    assert cfg.watcher.observer == "polling"
    assert cfg.server.port == 9999
    assert cfg.log_level == "DEBUG"


def test_env_overrides_beat_yaml(tmp_path: Path, monkeypatch) -> None:
    f = tmp_path / "config.yaml"
    f.write_text("log_level: INFO\nwatcher:\n  observer: inotify\n", encoding="utf-8")
    monkeypatch.setenv("MI_WATCHER__OBSERVER", "polling")
    cfg = load_config(f)
    assert cfg.watcher.observer == "polling"
    assert cfg.log_level == "INFO"  # untouched YAML value survives


def test_unknown_env_roots_ignored(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MI_TOTALLY_UNRELATED", "boom")
    cfg = load_config(tmp_path / "missing.yaml")  # must not raise
    assert cfg.log_level == "INFO"


def test_list_fields_not_env_overridable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MI_LIBRARIES", "nope")
    cfg = load_config(tmp_path / "missing.yaml")
    assert cfg.libraries == []
