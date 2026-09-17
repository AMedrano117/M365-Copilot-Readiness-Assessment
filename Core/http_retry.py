"""Bounded retries for read-only HTTP requests used by assessment collectors."""

import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import random

import httpx


TRANSIENT_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
DEFAULT_MAX_RETRIES = 4
DEFAULT_MAX_RETRY_WAIT = 120.0


class RequestRetryError(RuntimeError):
    """A read exhausted its retry allowance without discarding earlier pages."""

    def __init__(self, message, response=None):
        super().__init__(message)
        self.response = response
        self.status_code = response.status_code if response is not None else 0


def retry_after_seconds(value, *, now=None):
    """Parse either Retry-After form; never shorten a valid server-requested delay."""
    if value is None:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        try:
            when = parsedate_to_datetime(str(value))
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            seconds = (when - (now or datetime.now(timezone.utc))).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    return max(seconds, 0.0) if math.isfinite(seconds) else None


async def request_with_retry(send, *, method="GET", max_retries=DEFAULT_MAX_RETRIES,
                             max_retry_wait=DEFAULT_MAX_RETRY_WAIT):
    """Retry GET/HEAD transient failures; other methods execute exactly once.

    The budget limits cumulative retry sleeps, independently of each HTTP client's
    request timeout. A server delay larger than the remaining budget stops the
    read with an explicit error instead of retrying before the server allows it.
    ``send`` creates a fresh awaitable on each call, retaining URL/params/headers.
    """
    if method.upper() not in {"GET", "HEAD"}:
        return await send()
    retries = max(0, int(max_retries))
    waited = 0.0
    for attempt in range(retries + 1):
        response = None
        try:
            response = await send()
        except httpx.TransportError as exc:
            if attempt == retries:
                raise RequestRetryError(
                    f"HTTP read failed after {attempt + 1} attempt(s): {type(exc).__name__}. "
                    "The request retry limit was reached."
                ) from exc
        else:
            if response.status_code not in TRANSIENT_STATUS_CODES or attempt == retries:
                return response

        headers = (getattr(response, "headers", {}) or {}) if response is not None else {}
        delay = retry_after_seconds(headers.get("Retry-After"))
        if delay is None:
            delay = min(2 ** attempt, 16) + random.uniform(0, 0.25)
        remaining = max(0.0, max_retry_wait - waited)
        if delay > remaining:
            raise RequestRetryError(
                f"HTTP read stopped: the requested retry delay ({delay:g}s) exceeds "
                f"the remaining retry wait budget ({remaining:g}s). Retry collection later.",
                response,
            )
        await asyncio.sleep(delay)
        waited += delay


async def get_with_retry(client, path, **kwargs):
    """Use the common policy with a raw HTTP client's GET method."""
    return await request_with_retry(lambda: client.get(path, **kwargs))
