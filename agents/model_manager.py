"""
Model selection and retry/fallback logic for Gemini calls made through ADK.

Design notes (read before touching this file):
- No network or API calls happen at import time. The previous version of this
  project called a model-selection function at module import, which made a
  live Gemini request before Flask had even started — that's what caused the
  "hangs before Flask starts" bug. Everything here is either a pure function
  or reads from environment variables only.
- The fallback chain is intentionally ordered from cheapest/fastest to most
  capable, only as a SECONDARY axis. The PRIMARY reason this list exists is
  availability: gemini-2.5 models occasionally return 503 (overloaded) or 429
  (quota exhausted) and we want the application to keep working rather than
  surface an error to the user.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
from typing import Callable, TypeVar

logger = logging.getLogger("ai_finance_analyst.model_manager")

T = TypeVar("T")

# ---------------------------------------------------------------------------
# Fallback chain
# ---------------------------------------------------------------------------
# Override via env var MODEL_FALLBACK_CHAIN="model-a,model-b,model-c"
# Default chain matches what was recommended during the original debugging
# session: try the cheap/fast model first, then step up.
DEFAULT_FALLBACK_CHAIN = [
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
]


def get_fallback_chain() -> list[str]:
    """Returns the ordered list of model IDs to try, env-overridable."""
    raw = os.getenv("MODEL_FALLBACK_CHAIN", "").strip()
    if not raw:
        return list(DEFAULT_FALLBACK_CHAIN)
    chain = [m.strip() for m in raw.split(",") if m.strip()]
    return chain or list(DEFAULT_FALLBACK_CHAIN)


def get_primary_model() -> str:
    """Primary model ID used to build the agent pipeline.

    Pure / no I/O — safe to call at import time, unlike the old
    get_model() that made a live API call.
    """
    return os.getenv("PRIMARY_MODEL_ID", get_fallback_chain()[0])


# ---------------------------------------------------------------------------
# Retry classification
# ---------------------------------------------------------------------------
# These status codes / substrings are what Gemini returns for transient
# availability problems. We retry on these; everything else (4xx auth errors,
# bad requests, etc.) fails fast instead of burning retry budget.
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
RETRYABLE_MESSAGE_MARKERS = (
    "503",
    "UNAVAILABLE",
    "429",
    "RESOURCE_EXHAUSTED",
    "overloaded",
    "rate limit",
    "deadline exceeded",
    "timeout",
)


def is_retryable_error(exc: BaseException) -> bool:
    """Best-effort classification of whether an exception is transient."""
    status_code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if isinstance(status_code, int) and status_code in RETRYABLE_STATUS_CODES:
        return True

    message = str(exc).lower()
    return any(marker.lower() in message for marker in RETRYABLE_MESSAGE_MARKERS)


def is_quota_error(exc: BaseException) -> bool:
    """True specifically for 429 / quota-exhausted errors (no point retrying
    the SAME model on these — only a model/key switch helps)."""
    status_code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if status_code == 429:
        return True
    message = str(exc).lower()
    return "429" in message or "resource_exhausted" in message or "quota" in message


# ---------------------------------------------------------------------------
# Retry helper with exponential backoff + jitter
# ---------------------------------------------------------------------------
async def call_with_retry(
    fn: Callable[[], "asyncio.Future[T] | T"],
    *,
    max_attempts: int = 3,
    base_delay_seconds: float = 1.0,
    max_delay_seconds: float = 8.0,
    on_retry: Callable[[int, BaseException], None] | None = None,
) -> T:
    """Calls an async callable with exponential backoff on retryable errors.

    `fn` should be a zero-arg callable returning an awaitable (e.g. a
    closure / lambda wrapping the real call so each attempt is fresh).
    """
    last_exc: BaseException | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            result = fn()
            if asyncio.iscoroutine(result) or isinstance(result, asyncio.Future):
                return await result  # type: ignore[return-value]
            return result  # type: ignore[return-value]
        except Exception as exc:  # noqa: BLE001 - we deliberately classify broadly
            last_exc = exc
            if not is_retryable_error(exc) or attempt == max_attempts:
                raise

            delay = min(max_delay_seconds, base_delay_seconds * (2 ** (attempt - 1)))
            delay += random.uniform(0, delay * 0.25)  # jitter to avoid thundering herd

            if on_retry:
                on_retry(attempt, exc)
            logger.warning(
                "Retryable error on attempt %s/%s (%s). Backing off %.2fs.",
                attempt,
                max_attempts,
                exc.__class__.__name__,
                delay,
            )
            await asyncio.sleep(delay)

    # Should be unreachable, but keeps type-checkers happy.
    assert last_exc is not None
    raise last_exc
