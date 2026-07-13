"""Shared HTTP plumbing for providers: throttling, retries, soft failure.

Two rules everything here exists to enforce:

1. **A provider must never fail a scan.** Metadata is an enhancement; the
   library indexes fine without it. Every network error, timeout, bad status
   or malformed payload degrades to `None` and a log line, never an exception
   that escapes into the scanner.

2. **Respect the rate limits.** AniList allows 30 requests/minute (verified
   against the live API's `x-ratelimit-limit` header) -- low enough that a
   first pass over a ~100-title library will hit it. Requests are throttled
   client-side, and a 429 is honoured via `Retry-After` rather than hammered.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3
_BACKOFF_SECONDS = 2.0
# Cap how long we'll sit on a 429. A provider telling us to wait ten minutes
# is not worth blocking a scan for -- give up and try again on the next run.
_MAX_RETRY_AFTER = 60.0


class RateLimiter:
    """Simple client-side throttle: at most `per_minute` requests a minute.

    Deliberately a fixed minimum spacing rather than a token bucket -- bursting
    up to the limit and then stalling for the rest of the minute is exactly the
    behaviour that trips providers' abuse detection.
    """

    def __init__(self, per_minute: int) -> None:
        self._min_interval = 60.0 / max(1, per_minute)
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            sleep_for = self._min_interval - (now - self._last)
            if sleep_for > 0:
                time.sleep(sleep_for)
            self._last = time.monotonic()


def _retry_after(response: httpx.Response) -> float:
    raw = response.headers.get("retry-after")
    try:
        return min(float(raw), _MAX_RETRY_AFTER) if raw else _BACKOFF_SECONDS
    except (TypeError, ValueError):
        return _BACKOFF_SECONDS


def request_json(
    method: str,
    url: str,
    *,
    provider: str,
    timeout: float,
    limiter: RateLimiter | None = None,
    **kwargs: Any,
) -> dict | None:
    """Perform a request and return parsed JSON, or None on any failure.

    Never raises: a provider being down, slow, rate-limiting us, or returning
    junk must degrade to "no metadata", not break the scan that called it.
    """
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        if limiter is not None:
            limiter.wait()
        try:
            response = httpx.request(method, url, timeout=timeout, **kwargs)
        except httpx.HTTPError as exc:
            log.warning("%s: request failed (%s/%s): %s", provider, attempt, _MAX_ATTEMPTS, exc)
            if attempt == _MAX_ATTEMPTS:
                return None
            time.sleep(_BACKOFF_SECONDS * attempt)
            continue

        if response.status_code == 429:
            delay = _retry_after(response)
            log.warning("%s: rate limited, waiting %.1fs", provider, delay)
            time.sleep(delay)
            continue

        if response.status_code >= 500:
            log.warning("%s: server error %s", provider, response.status_code)
            if attempt == _MAX_ATTEMPTS:
                return None
            time.sleep(_BACKOFF_SECONDS * attempt)
            continue

        if response.status_code == 404:
            return None  # a legitimate "no such title", not an error

        if response.status_code >= 400:
            log.warning("%s: %s %s", provider, response.status_code, response.text[:200])
            return None

        try:
            payload = response.json()
        except ValueError:
            log.warning("%s: response was not JSON", provider)
            return None
        return payload if isinstance(payload, dict) else None

    log.warning("%s: giving up after %s attempts", provider, _MAX_ATTEMPTS)
    return None


def year_matches(parsed_year: int | None, provider_year: int | None, *, tolerance: int = 1) -> bool:
    """Whether two years are close enough to be the same production.

    This is what separates a real anime from its live-action remake: AniList
    happily resolves "One Piece" to the 1999 anime even when the file on disk
    is Netflix's 2023 live-action series. Without a year check, every such
    adaptation gets mislabelled anime.
    """
    if parsed_year is None or provider_year is None:
        return True  # nothing to contradict; treated as a non-signal
    return abs(parsed_year - provider_year) <= tolerance
