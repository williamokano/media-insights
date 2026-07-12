"""Webhook delivery: HMAC-signed POST with httpx."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

import httpx

from media_insights.config import WebhookConfig
from media_insights.events.bus import ChangeEvent, format_payload

log = logging.getLogger(__name__)

SIGNATURE_HEADER = "X-Media-Insights-Signature"


def _sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def deliver(hook: WebhookConfig, event: ChangeEvent) -> None:
    """POST the event. Caller catches failures and updates the outbox row."""
    if not hook.url:
        return
    body = format_payload(event).encode()
    headers = {"Content-Type": "application/json"}
    if hook.secret:
        headers[SIGNATURE_HEADER] = _sign(body, hook.secret)
    headers["X-Media-Insights-Event"] = event.type
    headers["X-Media-Insights-Event-Id"] = str(event.id)

    log.debug("POST %s event=%s", hook.url, event.type)
    with httpx.Client(timeout=hook.timeout_seconds) as client:
        resp = client.post(hook.url, content=body, headers=headers)
    if resp.status_code >= 400:
        raise RuntimeError(f"webhook returned HTTP {resp.status_code}: {resp.text[:300]}")


def preview(hook: WebhookConfig, event: ChangeEvent) -> dict:
    """Describe a payload without delivering. Used by the Web UI."""
    body = format_payload(event).encode()
    headers = {
        "Content-Type": "application/json",
        "X-Media-Insights-Event": event.type,
        "X-Media-Insights-Event-Id": str(event.id),
    }
    if hook.secret:
        headers[SIGNATURE_HEADER] = _sign(body, hook.secret)
    return {
        "url": hook.url,
        "headers": headers,
        "body": json.loads(body),
    }
