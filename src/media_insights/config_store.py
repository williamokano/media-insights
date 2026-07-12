"""Persist library definitions back into config.yaml.

Editing libraries through the API or Web UI mutates the in-memory AppConfig
and writes the change straight back to disk. We use ruamel.yaml (round-trip
mode) instead of plain PyYAML so we don't sacrifice every comment in the
operator's config.yaml the moment someone adds a library from the UI --
only the `libraries:` section itself gets rewritten.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from media_insights.config import AppConfig, LibraryConfig

_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.indent(mapping=2, sequence=4, offset=2)

# Guards the read-modify-write cycle against concurrent API requests. The
# in-memory cfg.libraries mutation happens inside the same critical section
# as the disk write, so the file and the running config can't drift.
_lock = threading.Lock()


class ConfigFileError(RuntimeError):
    """config.yaml can't be found or parsed on disk."""


class LibraryExistsError(ValueError):
    """A library with that name is already configured."""


class LibraryNotFoundError(KeyError):
    """No configured library has that name."""


def _load_document(path: Path) -> Any:
    if not path.is_file():
        raise ConfigFileError(
            f"no config file at {path}; create one (see config.example.yaml) "
            "before managing libraries through the API or Web UI"
        )
    with path.open("r", encoding="utf-8") as fh:
        doc = _yaml.load(fh)
    if doc is None:
        raise ConfigFileError(f"{path} is empty")
    return doc


def _write_document(path: Path, doc: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        _yaml.dump(doc, fh)
    tmp.replace(path)


def _persist_libraries(path: Path, libraries: list[LibraryConfig]) -> None:
    doc = _load_document(path)
    doc["libraries"] = [
        {"name": lib.name, "path": lib.path, "kind": lib.kind} for lib in libraries
    ]
    _write_document(path, doc)


def add_library(cfg: AppConfig, config_path: Path, new_lib: LibraryConfig) -> None:
    """Append a library to cfg (in place) and persist it to config.yaml."""
    with _lock:
        if any(lib.name == new_lib.name for lib in cfg.libraries):
            raise LibraryExistsError(f"library {new_lib.name!r} already exists")
        cfg.libraries.append(new_lib)
        try:
            _persist_libraries(config_path, cfg.libraries)
        except Exception:
            cfg.libraries.remove(new_lib)
            raise


def update_library(
    cfg: AppConfig, config_path: Path, current_name: str, updated: LibraryConfig
) -> None:
    """Replace the library named `current_name` with `updated`, in place + on disk."""
    with _lock:
        idx = next((i for i, lib in enumerate(cfg.libraries) if lib.name == current_name), None)
        if idx is None:
            raise LibraryNotFoundError(current_name)
        if updated.name != current_name and any(
            lib.name == updated.name for lib in cfg.libraries
        ):
            raise LibraryExistsError(f"library {updated.name!r} already exists")
        previous = cfg.libraries[idx]
        cfg.libraries[idx] = updated
        try:
            _persist_libraries(config_path, cfg.libraries)
        except Exception:
            cfg.libraries[idx] = previous
            raise


def remove_library(cfg: AppConfig, config_path: Path, name: str) -> LibraryConfig:
    """Drop a library from cfg (in place) and persist to config.yaml."""
    with _lock:
        idx = next((i for i, lib in enumerate(cfg.libraries) if lib.name == name), None)
        if idx is None:
            raise LibraryNotFoundError(name)
        removed = cfg.libraries.pop(idx)
        try:
            _persist_libraries(config_path, cfg.libraries)
        except Exception:
            cfg.libraries.insert(idx, removed)
            raise
        return removed
