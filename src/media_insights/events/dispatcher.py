"""Background dispatcher: drains the outbox into webhooks and exec hooks."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from media_insights.config import AppConfig
from media_insights.db import session_scope
from media_insights.events import bus
from media_insights.events import exec as exec_hook
from media_insights.events import webhook as webhook_hook
from media_insights.models import ChangeEvent as EventModel

log = logging.getLogger(__name__)


class Dispatcher:
    """A simple polling dispatcher that drains pending events.

    Designed to be started once during the FastAPI lifespan and run for the
    lifetime of the process. Every dispatch is wrapped in its own transaction
    so a single bad event can't poison the queue.
    """

    def __init__(self, cfg: AppConfig, poll_seconds: float = 5.0) -> None:
        self._cfg = cfg
        self._poll = poll_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="mi-dispatcher", daemon=True)
        self._thread.start()
        log.info("dispatcher started (poll=%ss)", self._poll)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self._poll + 5)
        log.info("dispatcher stopped")

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.drain_once()
            except Exception as exc:
                log.exception("dispatcher tick failed: %s", exc)
            self._stop.wait(self._poll)

    def drain_once(self) -> int:
        """Dispatch all currently pending events. Returns count processed."""
        with session_scope() as session:
            events = list(bus.due_events(session, max_attempts=max(h.max_attempts for h in self._cfg.webhooks) if self._cfg.webhooks else 10))
        processed = 0
        for event in events:
            self._dispatch(event)
            processed += 1
        return processed

    def _dispatch(self, event: EventModel) -> None:
        attempts = max((h.max_attempts for h in self._cfg.webhooks), default=10)
        any_success = False
        last_error: str | None = None
        for webhook in self._cfg.webhooks:
            if not webhook.url:
                continue
            try:
                webhook_hook.deliver(webhook, event)
            except Exception as exc:
                log.warning("webhook %s failed for event %s: %s", webhook.name, event.id, exc)
                last_error = str(exc)
            else:
                any_success = True
        for hook in self._cfg.exec_hooks:
            if not hook.command:
                continue
            try:
                exec_hook.deliver(hook, event)
            except Exception as exc:
                log.warning("exec hook %s failed for event %s: %s", hook.name, event.id, exc)
                last_error = str(exc)
            else:
                any_success = True

        with session_scope() as session:
            row = session.get(EventModel, event.id)
            if row is None:
                return
            if any_success:
                bus.mark_sent(row)
                row.delivery_attempts = min(row.delivery_attempts + 1, attempts)
            else:
                bus.mark_failed(row, last_error or "no delivery succeeded")
                if row.delivery_attempts >= attempts:
                    row.delivery_status = "failed"
                    log.error("event %s gave up after %s attempts", event.id, row.delivery_attempts)


# Type alias kept to avoid circular imports in tests
DispatchFn = Callable[[EventModel], None]
