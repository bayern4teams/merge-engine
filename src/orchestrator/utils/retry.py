"""
Retry logic wrapper using tenacity.

Provides a configurable retry decorator that LangGraph nodes can use
for transient errors (network timeouts, rate limits, etc.).
"""

from __future__ import annotations

import asyncio
import functools
import logging
from collections.abc import Callable
from typing import Any, TypeVar

from tenacity import (
    after_log,
    before_log,
    retry_if_exception_type,
    stop_after_attempt,
    wait_chain,
    wait_exponential,
    wait_fixed,
)

logger = logging.getLogger("merge-engine.retry")

# Type alias for async-compatible callables
F = TypeVar("F", bound=Callable[..., Any])


class MergeEngineError(RuntimeError):
    """Base for every failure the pipeline classifies and then acts on.

    The subclass is the decision: it says whether to retry, to stop, or to
    ask a human. Nothing in the pipeline should catch a bare ``Exception``
    and guess — call :func:`classify_exception` and let the class decide.
    """

    #: What the orchestrator does when this class reaches a node boundary.
    #: ``retry`` re-runs the node, ``escalate`` stops at the error gate for a
    #: human, ``fail`` ends the run.
    disposition: str = "fail"
    retryable: bool = False


class TransientError(MergeEngineError):
    """A recoverable failure: network blip, timeout, rate limit, deadlock.

    Retrying the identical call is expected to eventually succeed.
    """

    disposition = "retry"
    retryable = True

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class PermanentError(MergeEngineError):
    """A failure that retrying cannot fix: bad SQL, auth refused, missing table.

    Retrying wastes time and hides the real cause, so it is never retried.
    """

    disposition = "fail"


class DataQualityError(MergeEngineError):
    """The pipeline reached data it cannot honestly process.

    An unparseable row, a value that will not fit its destination column, a
    table that returned fewer rows than it claimed. This is the class that
    must never be swallowed: the run is *able* to continue, which is exactly
    why continuing silently produces a confidently wrong merge. It escalates
    to a human instead.
    """

    disposition = "escalate"

    def __init__(
        self,
        message: str,
        *,
        entity: str | None = None,
        origin_id: str | None = None,
        table: str | None = None,
        rows_affected: int | None = None,
    ) -> None:
        super().__init__(message)
        self.entity = entity
        self.origin_id = origin_id
        self.table = table
        self.rows_affected = rows_affected

    def context(self) -> dict[str, Any]:
        """The where-and-how-much, for the error gate to render."""
        return {
            "entity": self.entity,
            "origin_id": self.origin_id,
            "table": self.table,
            "rows_affected": self.rows_affected,
        }


class UserInputError(MergeEngineError):
    """The request or configuration cannot be acted on as written.

    Raised by preflight and by request parsing — the fix is the user's, not
    the pipeline's, so failing immediately with a clear message beats
    discovering it five minutes into an extraction.
    """

    disposition = "fail"


# Substrings that identify a retryable failure coming out of a raw driver
# exception. Drivers raise their own hierarchies (psycopg.OperationalError,
# pymongo.AutoReconnect, clickhouse_connect errors), and depending on the
# driver the same network blip can surface as almost anything — so the
# message is the most portable signal available.
_TRANSIENT_MARKERS = (
    "timeout",
    "timed out",
    "connection reset",
    "connection refused",
    "connection closed",
    "broken pipe",
    "temporarily unavailable",
    "too many connections",
    "server closed the connection",
    "rate limit",
    "429",
    "503",
    "504",
    "deadlock detected",
    "could not connect",
    "network is unreachable",
    "eof detected",
    "ssl connection has been closed",
    "operation now in progress",
)

# Substrings that identify a failure retrying will never fix.
_PERMANENT_MARKERS = (
    "authentication failed",
    "password authentication",
    "permission denied",
    "access denied",
    "not authorized",
    "does not exist",
    "no such table",
    "unknown database",
    "syntax error",
    "invalid input syntax",
    "undefined column",
    "duplicate key",
    "violates",
    "role ",
    "must be owner",
)


def classify_exception(exc: BaseException) -> MergeEngineError:
    """Map any exception onto the taxonomy.

    Already-classified errors pass through unchanged so a caller can wrap
    without flattening a deliberate classification.
    """
    if isinstance(exc, MergeEngineError):
        return exc

    text = f"{type(exc).__name__}: {exc}".lower()

    # Permanent wins ties: "permission denied" inside a connection error is
    # a credentials problem, and hammering it three more times just delays
    # the message the user actually needs.
    if any(marker in text for marker in _PERMANENT_MARKERS):
        return PermanentError(str(exc) or type(exc).__name__)

    if isinstance(exc, (TimeoutError, ConnectionError)) or any(
        marker in text for marker in _TRANSIENT_MARKERS
    ):
        return TransientError(str(exc) or type(exc).__name__)

    # Unrecognised. Treated as permanent on purpose — an unknown failure that
    # gets retried three times looks like flakiness, while one that stops
    # immediately gets read and fixed.
    return PermanentError(str(exc) or type(exc).__name__)


def _build_wait_strategy(backoff: str, min_backoff: float, max_backoff: float):
    """Build a tenacity wait strategy from configuration."""
    if backoff == "exponential":
        return wait_chain(
            *[wait_exponential(multiplier=1, min=min_backoff, max=max_backoff) for _ in range(3)]
        )
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

            @functools.wraps(func)
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

            @functools.wraps(func)
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
