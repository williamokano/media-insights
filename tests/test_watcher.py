"""MediaWatcher dynamic install/uninstall."""

from __future__ import annotations

from pathlib import Path

from media_insights.config import AppConfig, WatcherConfig
from media_insights.scanner.watcher import MediaWatcher


def _watcher(**kwargs) -> MediaWatcher:
    cfg = AppConfig(
        watcher=WatcherConfig(enabled=True, observer="polling", debounce_seconds=0.1, **kwargs),
        libraries=[],
    )
    return MediaWatcher(cfg, on_path_changed=lambda p: None)


def test_install_and_uninstall_library(tmp_path: Path) -> None:
    watcher = _watcher()
    watcher.start()
    try:
        d = tmp_path / "lib"
        d.mkdir()
        watcher.install_library(str(d))
        assert str(d) in watcher._installed

        # idempotent: installing twice doesn't duplicate state
        watcher.install_library(str(d))
        assert list(watcher._installed).count(str(d)) == 1

        watcher.uninstall_library(str(d))
        assert str(d) not in watcher._installed
    finally:
        watcher.stop()


def test_uninstall_unknown_path_is_noop(tmp_path: Path) -> None:
    watcher = _watcher()
    watcher.start()
    try:
        watcher.uninstall_library(str(tmp_path / "never-installed"))  # must not raise
    finally:
        watcher.stop()


def test_install_before_start_is_noop(tmp_path: Path) -> None:
    watcher = _watcher()
    d = tmp_path / "lib"
    d.mkdir()
    watcher.install_library(str(d))  # watcher never started
    assert str(d) not in watcher._installed


def test_install_disabled_watcher_is_noop(tmp_path: Path) -> None:
    cfg = AppConfig(watcher=WatcherConfig(enabled=False), libraries=[])
    watcher = MediaWatcher(cfg, on_path_changed=lambda p: None)
    watcher.start()  # no-op per config
    d = tmp_path / "lib"
    d.mkdir()
    watcher.install_library(str(d))
    assert str(d) not in watcher._installed
    watcher.stop()
