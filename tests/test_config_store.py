"""config_store: comment-preserving library persistence."""

from __future__ import annotations

from pathlib import Path

import pytest

from media_insights.config import AppConfig, LibraryConfig
from media_insights.config_store import (
    ConfigFileError,
    LibraryExistsError,
    LibraryNotFoundError,
    add_library,
    remove_library,
    update_library,
)

SAMPLE_YAML = """\
# top-level comment kept across edits
config_dir: /config  # inline comment kept too
data_dir: /data

libraries:
  - name: Movies
    path: /data/movies
    kind: movie
"""


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(SAMPLE_YAML, encoding="utf-8")
    return p


@pytest.fixture
def cfg() -> AppConfig:
    return AppConfig(libraries=[LibraryConfig(name="Movies", path="/data/movies", kind="movie")])


def test_add_library_persists_and_preserves_comments(config_file: Path, cfg: AppConfig) -> None:
    add_library(cfg, config_file, LibraryConfig(name="TV", path="/data/tv", kind="tv"))

    assert [lib.name for lib in cfg.libraries] == ["Movies", "TV"]
    text = config_file.read_text(encoding="utf-8")
    assert "# top-level comment kept across edits" in text
    assert "# inline comment kept too" in text
    assert "name: TV" in text
    assert "path: /data/tv" in text


def test_add_library_rejects_duplicate_name(config_file: Path, cfg: AppConfig) -> None:
    with pytest.raises(LibraryExistsError):
        add_library(cfg, config_file, LibraryConfig(name="Movies", path="/x", kind="movie"))
    # in-memory config must be untouched by the rejected call
    assert len(cfg.libraries) == 1


def test_update_library_renames_and_persists(config_file: Path, cfg: AppConfig) -> None:
    update_library(cfg, config_file, "Movies", LibraryConfig(name="Films", path="/data/movies", kind="movie"))
    assert cfg.libraries[0].name == "Films"
    text = config_file.read_text(encoding="utf-8")
    assert "name: Films" in text
    assert "name: Movies" not in text


def test_update_library_rejects_rename_collision(config_file: Path, cfg: AppConfig) -> None:
    add_library(cfg, config_file, LibraryConfig(name="TV", path="/data/tv", kind="tv"))
    with pytest.raises(LibraryExistsError):
        update_library(cfg, config_file, "TV", LibraryConfig(name="Movies", path="/data/tv", kind="tv"))
    assert [lib.name for lib in cfg.libraries] == ["Movies", "TV"]


def test_update_library_missing_name_raises(config_file: Path, cfg: AppConfig) -> None:
    with pytest.raises(LibraryNotFoundError):
        update_library(cfg, config_file, "Nope", LibraryConfig(name="X", path="/x", kind="auto"))


def test_remove_library_persists(config_file: Path, cfg: AppConfig) -> None:
    removed = remove_library(cfg, config_file, "Movies")
    assert removed.name == "Movies"
    assert cfg.libraries == []
    text = config_file.read_text(encoding="utf-8")
    assert "libraries:" in text
    assert "name: Movies" not in text
    # comments untouched even when the list becomes empty
    assert "# top-level comment kept across edits" in text


def test_remove_library_missing_raises(config_file: Path, cfg: AppConfig) -> None:
    with pytest.raises(LibraryNotFoundError):
        remove_library(cfg, config_file, "Ghost")


def test_missing_config_file_raises_and_does_not_mutate(tmp_path: Path, cfg: AppConfig) -> None:
    missing = tmp_path / "does-not-exist.yaml"
    with pytest.raises(ConfigFileError):
        add_library(cfg, missing, LibraryConfig(name="X", path="/x", kind="auto"))
    # rollback: in-memory list must not carry the entry the write couldn't persist
    assert [lib.name for lib in cfg.libraries] == ["Movies"]


def test_empty_config_file_raises(tmp_path: Path, cfg: AppConfig) -> None:
    empty = tmp_path / "empty.yaml"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ConfigFileError):
        add_library(cfg, empty, LibraryConfig(name="X", path="/x", kind="auto"))
