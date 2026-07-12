"""Filesystem watcher.

watchdog + debouncing. Network mounts (NFS/SMB) don't emit inotify events
reliably, so we support a PollingObserver fallback and a config flag for the
operator to force it.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from watchdog.events import (
    DirCreatedEvent,
    DirDeletedEvent,
    DirModifiedEvent,
    DirMovedEvent,
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
)
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver

from media_insights.config import AppConfig, WatcherConfig

if TYPE_CHECKING:
    from watchdog.observers.api import BaseObserver, ObservedWatch

log = logging.getLogger(__name__)

PathSet = set[str]


class _DebouncedHandler:
    """Coalesces bursts of events for a single path and runs `on_due` once.

    Why debounce: an Arr upgrade writes the file once, then renames, then
    maybe sets xattrs; without debouncing we'd re-probe three times in a
    second.
    """

    def __init__(self, debounce_seconds: float, on_due: Callable[[Path], None]) -> None:
        self._debounce = debounce_seconds
        self._on_due = on_due
        self._lock = threading.Lock()
        self._pending: dict[str, float] = {}
        self._timer: threading.Timer | None = None
        self._stop = False

    def stop(self) -> None:
        self._stop = True
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None

    def feed(self, path: Path) -> None:
        if self._stop:
            return
        key = str(path)
        with self._lock:
            self._pending[key] = time.monotonic()
            if self._timer is None:
                self._timer = threading.Timer(self._debounce, self._fire)
                self._timer.daemon = True
                self._timer.start()

    def _fire(self) -> None:
        with self._lock:
            due = list(self._pending.keys())
            self._pending.clear()
            self._timer = None
        for key in due:
            self._on_due(Path(key))


class MediaWatcher:
    """Per-library filesystem watcher."""

    def __init__(
        self,
        cfg: AppConfig,
        on_path_changed: Callable[[Path], None],
    ) -> None:
        self._cfg = cfg
        self._on_path_changed = on_path_changed
        self._observer: BaseObserver = self._build_observer(cfg.watcher)
        self._handlers: dict[str, _DebouncedHandler] = {}
        self._watches: dict[str, ObservedWatch] = {}
        self._installed: set[str] = set()
        self._started = False

    @staticmethod
    def _build_observer(cfg: WatcherConfig) -> BaseObserver:
        if cfg.observer == "polling":
            return PollingObserver(timeout=cfg.debounce_seconds)
        if cfg.observer == "inotify":
            return Observer()
        # auto: try inotify; if it fails to start (e.g. no inotify on the host),
        # the caller falls back to PollingObserver.
        return Observer()

    def start(self) -> None:
        if not self._cfg.watcher.enabled:
            log.info("watcher disabled by config")
            return
        try:
            self._observer.start()
        except Exception as exc:
            log.warning("inotify observer failed (%s); falling back to polling", exc)
            self._observer = PollingObserver(timeout=self._cfg.watcher.debounce_seconds)
            self._observer.start()  # type: ignore[assignment]
        self._started = True
        for lib in self._cfg.libraries:
            self._install(lib.path)
        log.info("watcher started, observing %d path(s)", len(self._installed))

    def stop(self) -> None:
        for handler in self._handlers.values():
            handler.stop()
        try:
            self._observer.stop()
            self._observer.join(timeout=2)
        except RuntimeError:
            # observer never started (e.g. disabled in config)
            pass
        self._started = False
        log.info("watcher stopped")

    def install_library(self, path: str) -> None:
        """Start watching a path added after startup. No-op if already watched."""
        if not self._started:
            log.debug("install_library(%s) skipped: watcher not started", path)
            return
        p = str(Path(path))
        if p in self._installed:
            return
        self._install(p)
        if p in self._installed:
            log.info("now watching %s", p)

    def uninstall_library(self, path: str) -> None:
        """Stop watching a path removed after startup. No-op if not watched."""
        p = str(Path(path))
        handler = self._handlers.pop(p, None)
        if handler:
            handler.stop()
        watch = self._watches.pop(p, None)
        if watch is not None:
            try:
                self._observer.unschedule(watch)
            except Exception as exc:
                log.debug("unschedule(%s) failed (already gone?): %s", p, exc)
        if p in self._installed:
            self._installed.discard(p)
            log.info("stopped watching %s", p)

    def _install(self, path: str) -> None:
        p = Path(path)
        if not p.exists():
            log.warning("skip watch: %s does not exist", p)
            return
        handler = _DebouncedHandler(self._cfg.watcher.debounce_seconds, self._on_path_changed)
        self._handlers[str(p)] = handler
        watch = self._observer.schedule(
            _WatchdogBridge(handler), str(p), recursive=self._cfg.watcher.recursive  # type: ignore[arg-type]
        )
        self._watches[str(p)] = watch
        self._installed.add(str(p))


class _WatchdogBridge:
    """Adapt watchdog events to our debounced handler."""

    def __init__(self, handler: _DebouncedHandler) -> None:
        self._handler = handler

    def dispatch(self, event) -> None:  # type: ignore[no-untyped-def]
        if isinstance(
            event,
            (FileCreatedEvent, FileModifiedEvent, FileMovedEvent, FileDeletedEvent,
             DirCreatedEvent, DirModifiedEvent, DirMovedEvent, DirDeletedEvent),
        ):
            self._handler.feed(Path(str(event.src_path)))
            # A move leaves the old path stale and creates a new one;
            # feed the destination too so both sides get reconciled.
            dest = getattr(event, "dest_path", None)
            if dest:
                self._handler.feed(Path(str(dest)))
