"""Transactional outbox for change events.

Why an outbox instead of calling the webhook in-line:
  - the scanner must never block on a slow / dead endpoint,
  - the user asked for "send the old data and the new data before we update
    the data" -- writing the snapshot into the same transaction as the
    MediaFile row guarantees the snapshot exists the moment the new state
    lands,
  - delivery retries survive crashes.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from collections.abc import Iterable
from typing import Any

from sqlalchemy.orm import Session

from media_insights.models import ChangeEvent

log = logging.getLogger(__name__)


def record_event(
    session: Session,
    *,
    type_: str,
    subject_id: int | None,
    subject_path: str | None,
    old: dict[str, Any] | None,
    new: dict[str, Any] | None,
) -> ChangeEvent:
    """Persist an outbox row. Caller controls the transaction."""
    event = ChangeEvent(
        type=type_,
        subject_id=subject_id,
        subject_path=subject_path,
        old_payload=old,
        new_payload=new,
        delivery_status="pending",
    )
    session.add(event)
    return event


def _json_default(obj: Any) -> Any:
    if isinstance(obj, (dt.datetime, dt.date)):
        return obj.isoformat()
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    return str(obj)


def serialise_event(event: ChangeEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "type": event.type,
        "subject_id": event.subject_id,
        "subject_path": event.subject_path,
        "created_at": event.created_at.isoformat() if event.created_at else None,
        "old": event.old_payload,
        "new": event.new_payload,
    }


def due_events(session: Session, max_attempts: int = 10) -> Iterable[ChangeEvent]:
    """Pending events that haven't exceeded retry budget."""
    return (
        session.query(ChangeEvent)
        .filter(
            ChangeEvent.delivery_status == "pending",
            ChangeEvent.delivery_attempts < max_attempts,
        )
        .order_by(ChangeEvent.created_at.asc())
        .all()
    )


def mark_sent(event: ChangeEvent) -> None:
    event.delivery_status = "sent"
    event.delivery_attempts += 1
    event.delivered_at = dt.datetime.now(dt.UTC)
    event.last_error = None


def mark_failed(event: ChangeEvent, error: str) -> None:
    event.delivery_attempts += 1
    event.last_error = error[:2000]


def format_payload(event: ChangeEvent) -> str:
    return json.dumps(serialise_event(event), default=_json_default)
