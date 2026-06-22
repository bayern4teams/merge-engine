"""
Retry logic wrapper using tenacity.

Provides a configurable retry decorator that LangGraph nodes can use
for transient errors (network timeouts, rate limits, etc.).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, TypeVar

from tenacity import (
    RetryCallState,
    retry_if_exception_type,
    stop_after_attempt,
    wait_chain,
    wait_combine,
    wait_exponential,
    wait_fixed,
    before_log,
    after_log,
)

logger = logging.getLogger("merge-engine.retry")

# Type alias for async-compatible callables
F = TypeVar("F", bound=Callable[..., Any])


class TransientError(Exception):
    """Raised when a recoverable transient error occurs."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class PermanentError(Exception):
    """Raised when a non-recoverable error occurs."""

    pass


def _build_wait_strategy(backoff: str, min_backoff: float, max_backoff: float):
    """Build a tenacity wait strategy from configuration."""
    if backoff == "exponential":
        return wait_chain(*[
            wait_exponential(multiplier=1, min=min_backoff, max=max_backoff)
            for _ in range(3)
        ])
    elif backoff == "linear":
        return wait_fixed(min_backoff)
    else:
        return wait_exponential(multiplier=1, min=min_backoff, max=max_backoff)


def retry_on_transient(
    max_attempts: int = 3,
    backoff: str = "exponential",
    min_backoff: float = 1.0,
    max_backoff: float = 30.0,
    retryable_exceptions: tuple[type[Exception], ...] | None = None,
) -> Callable[[F], F]:
    """Decorator that retries a function on transient errors.

    Parameters
    ----------
    max_attempts : int
        Maximum number of attempts before raising.
    backoff : str
        Backoff strategy: "exponential" or "linear".
    min_backoff : float
        Minimum delay between retries in seconds.
    max_backoff : float
        Maximum delay between retries in seconds.
    retryable_exceptions : tuple, optional
        Exception types to treat as retryable. Defaults to (TransientError,).

    Returns
    -------
    Callable[[F], F]
        A decorator wrapping the target function.
    """
    if retryable_exceptions is None:
        retryable_exceptions = (TransientError,)

    wait_strategy = _build_wait_strategy(backoff, min_backoff, max_backoff)

    def decorator(func: F) -> F:
        is_async = asyncio.iscoroutinefunction(func)

        if is_async:

            async def wrapper(*args: Any, **kwargs: Any) -> Any:  # type: ignore[no-untyped-def]
                from tenacity import AsyncRetrying

                async def _async_func():
                    return await func(*args, **kwargs)

                async for attempt in AsyncRetrying(
                    stop=stop_after_attempt(max_attempts),
                    wait=wait_strategy,
                    retry=retry_if_exception_type(retryable_exceptions),
                    before=before_log(logger, logging.DEBUG),
                    after=after_log(logger, logging.DEBUG),
                    reraise=True,
                ):
                    with attempt:
                        result = await _async_func()
                        return result
                # Should not reach here due to reraise, but for type safety
                raise RuntimeError(f"Function {func.__name__} failed after {max_attempts} attempts")

            return wrapper  # type: ignore[return-value]
        else:

            def wrapper(*args: Any, **kwargs: Any) -> Any:
                from tenacity import Retrying

                for attempt in Retrying(
                    stop=stop_after_attempt(max_attempts),
                    wait=wait_strategy,
                    retry=retry_if_exception_type(retryable_exceptions),
                    before=before_log(logger, logging.DEBUG),
                    after=after_log(logger, logging.DEBUG),
                    reraise=True,
                ):
                    with attempt:
                        result = func(*args, **kwargs)
                        return result
                raise RuntimeError(f"Function {func.__name__} failed after {max_attempts} attempts")

            return wrapper  # type: ignore[return-value]

    return decorator