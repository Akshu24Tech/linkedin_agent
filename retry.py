"""
retry.py
─────────
Retry with exponential backoff for API calls.

Gemini free tier: 15 req/min → errors on burst
Notion API: occasional 500s / timeouts
LinkedIn: session drops

Usage:
    from retry import with_retry

    result = with_retry(lambda: some_api_call(), retries=3, label="Gemini")
"""

import time
import functools
from typing import Callable, TypeVar
from logger import setup_logger

log = setup_logger(__name__)
T = TypeVar("T")


def with_retry(
    fn: Callable[[], T],
    retries: int = 3,
    base_delay: float = 5.0,
    label: str = "API call",
) -> T:
    """
    Call fn() with exponential backoff on failure.

    Delays: 5s → 10s → 20s (doubles each retry)
    Raises the last exception if all retries fail.
    """
    last_error = None

    for attempt in range(1, retries + 1):
        try:
            return fn()
        except Exception as e:
            last_error = e
            error_str = str(e).lower()

            # Detect rate limit specifically
            is_rate_limit = any(x in error_str for x in [
                "rate", "quota", "429", "resource_exhausted", "too many"
            ])

            if attempt == retries:
                log.error(f"[retry] {label} failed after {retries} attempts: {e}")
                raise

            delay = base_delay * (2 ** (attempt - 1))
            if is_rate_limit:
                delay = max(delay, 60.0)  # At least 60s for rate limits
                log.warning(f"[retry] {label} rate limited. Waiting {delay:.0f}s... (attempt {attempt}/{retries})")
            else:
                log.warning(f"[retry] {label} error: {e}. Retrying in {delay:.0f}s... (attempt {attempt}/{retries})")

            time.sleep(delay)

    raise last_error


def retry_decorator(retries: int = 3, base_delay: float = 5.0, label: str = ""):
    """
    Decorator version:
        @retry_decorator(retries=3, label="Notion save")
        def save_to_notion(...): ...
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            fn_label = label or fn.__name__
            return with_retry(
                lambda: fn(*args, **kwargs),
                retries=retries,
                base_delay=base_delay,
                label=fn_label,
            )
        return wrapper
    return decorator
