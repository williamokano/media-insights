"""Exec hook delivery: writes the event JSON to the script's stdin."""

from __future__ import annotations

import logging
import shlex
import subprocess

from media_insights.events.bus import ChangeEvent, format_payload

log = logging.getLogger(__name__)


def deliver(hook, event: ChangeEvent) -> None:  # type: ignore[no-untyped-def]
    """Pipe the event payload as JSON to the configured command's stdin."""
    if not hook.command:
        return
    cmd = shlex.split(hook.command)
    log.debug("exec hook %s -> %s", hook.name, cmd)
    completed = subprocess.run(
        cmd,
        input=format_payload(event),
        text=True,
        capture_output=True,
        timeout=hook.timeout_seconds,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"exec hook {hook.name} failed (exit {completed.returncode}): "
            f"{completed.stderr.strip()[:300]}"
        )
