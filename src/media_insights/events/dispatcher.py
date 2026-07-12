"""Background dispatcher: drains the outbox into webhooks and exec hooks."""

from __future__ import annotations

import logging
import threading

from media_insights.config import AppConfig, ExecHookConfig, WebhookConfig
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

    With no hooks configured, pending events are marked `skipped` — they stay
    in the database as an audit log but never count as delivery failures.
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

    def _configured_hooks(self) -> tuple[list[WebhookConfig], list[ExecHookConfig]]:
        webhooks = [w for w in self._cfg.webhooks if w.url]
        exec_hooks = [h for h in self._cfg.exec_hooks if h.command]
        return webhooks, exec_hooks

    def drain_once(self) -> int:
        """Dispatch all currently pending events. Returns count processed."""
        webhooks, exec_hooks = self._configured_hooks()
        max_attempts = max((w.max_attempts for w in webhooks), default=10)

        with session_scope() as session:
            events = list(bus.due_events(session, max_attempts=max_attempts))
            if not webhooks and not exec_hooks:
                # No delivery targets: keep the events as audit rows, but
                # don't burn retry attempts or flag them as failures.
                for event in events:
                    event.delivery_status = "skipped"
                return len(events)

        processed = 0
        for event in events:
            self._dispatch(event, webhooks, exec_hooks, max_attempts)
            processed += 1
        return processed

    def _dispatch(
        self,
        event: EventModel,
        webhooks: list[WebhookConfig],
        exec_hooks: list[ExecHookConfig],
        max_attempts: int,
    ) -> None:
        any_success = False
        last_error: str | None = None
        for webhook in webhooks:
            try:
                webhook_hook.deliver(webhook, event)
            except Exception as exc:
                log.warning("webhook %s failed for event %s: %s", webhook.name, event.id, exc)
                last_error = str(exc)
            else:
                any_success = True
        for hook in exec_hooks:
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
            else:
                bus.mark_failed(row, last_error or "no delivery succeeded")
                if row.delivery_attempts >= max_attempts:
                    row.delivery_status = "failed"
                    log.error("event %s gave up after %s attempts", event.id, row.delivery_attempts)
