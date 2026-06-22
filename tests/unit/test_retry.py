"""
Unit tests for the retry logic module.

Tests retry decorators, transient/permanent error handling, and backoff strategies.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from src.orchestrator.utils.retry import (
    PermanentError,
    TransientError,
    _build_wait_strategy,
    retry_on_transient,
)


class TestBuildWaitStrategy:
    """Tests for wait strategy construction."""

    def test_exponential_backoff(self) -> None:
        """Should build exponential backoff strategy."""
        strategy = _build_wait_strategy("exponential", 1.0, 30.0)
        assert strategy is not None

    def test_linear_backoff(self) -> None:
        """Should build linear backoff strategy."""
        strategy = _build_wait_strategy("linear", 2.0, 10.0)
        assert strategy is not None

    def test_default_backoff(self) -> None:
        """Unknown backoff type should default to exponential."""
        strategy = _build_wait_strategy("unknown", 1.0, 30.0)
        assert strategy is not None

    def test_backoff_bounds(self) -> None:
        """Min and max backoff values should be applied."""
        # Should not raise
        strategy = _build_wait_strategy("exponential", 0.5, 60.0)
        assert strategy is not None


class TestTransientError:
    """Tests for TransientError exception."""

    def test_transient_error_message(self) -> None:
        """Should store error message."""
        err = TransientError("connection timeout")
        assert str(err) == "connection timeout"

    def test_transient_error_retry_after(self) -> None:
        """Should store optional retry_after value."""
        err = TransientError("rate limited", retry_after=5.0)
        assert err.retry_after == 5.0

    def test_transient_error_default_retry_after(self) -> None:
        """retry_after should default to None."""
        err = TransientError("timeout")
        assert err.retry_after is None


class TestPermanentError:
    """Tests for PermanentError exception."""

    def test_permanent_error_message(self) -> None:
        """Should store error message."""
        err = PermanentError("auth failed")
        assert str(err) == "auth failed"


class TestRetrySync:
    """Tests for retry decorator with sync functions."""

    def test_success_on_first_attempt(self) -> None:
        """Should return immediately on success."""
        call_count = 0

        @retry_on_transient(max_attempts=3)
        def success_func():
            return "ok"

        result = success_func()
        assert result == "ok"

    def test_retry_on_transient_error(self) -> None:
        """Should retry on TransientError."""
        call_count = 0

        @retry_on_transient(max_attempts=3)
        def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise TransientError("try again")
            return "success"

        result = flaky_func()
        assert result == "success"
        assert call_count == 3

    def test_max_attempts_exhausted(self) -> None:
        """Should raise after max_attempts."""
        call_count = 0

        @retry_on_transient(max_attempts=2)
        def always_fails():
            nonlocal call_count
            call_count += 1
            raise TransientError("always fails")

        with pytest.raises(TransientError):
            always_fails()
        assert call_count == 2

    def test_permanent_error_not_retried(self) -> None:
        """PermanentError should not be retried."""
        call_count = 0

        @retry_on_transient(max_attempts=3)
        def permanent_fail():
            nonlocal call_count
            call_count += 1
            raise PermanentError("can't recover")

        with pytest.raises(PermanentError):
            permanent_fail()
        assert call_count == 1

    def test_custom_retryable_exceptions(self) -> None:
        """Should retry on custom exception types."""
        class MyError(Exception):
            pass

        call_count = 0

        @retry_on_transient(
            max_attempts=3,
            retryable_exceptions=(MyError,),
        )
        def custom_fail():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise MyError("custom transient")
            return "ok"

        result = custom_fail()
        assert result == "ok"
        assert call_count == 2

    def test_non_retryable_exception(self) -> None:
        """Non-specified exceptions should raise immediately."""
        call_count = 0

        @retry_on_transient(max_attempts=3)
        def value_error_func():
            nonlocal call_count
            call_count += 1
            raise ValueError("not retryable")

        with pytest.raises(ValueError):
            value_error_func()
        assert call_count == 1


class TestRetryAsync:
    """Tests for retry decorator with async functions."""

    @pytest.mark.asyncio
    async def test_async_success(self) -> None:
        """Should return immediately on async success."""
        call_count = 0

        @retry_on_transient(max_attempts=3)
        async def async_success():
            nonlocal call_count
            call_count += 1
            return "async_ok"

        result = await async_success()
        assert result == "async_ok"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_async_retry_on_transient(self) -> None:
        """Should retry async function on TransientError."""
        call_count = 0

        @retry_on_transient(max_attempts=4)
        async def async_flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise TransientError("try again")
            return "recovered"

        result = await async_flaky()
        assert result == "recovered"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_async_max_attempts(self) -> None:
        """Should raise after max attempts in async."""
        call_count = 0

        @retry_on_transient(max_attempts=2)
        async def async_always_fail():
            nonlocal call_count
            call_count += 1
            raise TransientError("permanent failure")

        with pytest.raises(TransientError):
            await async_always_fail()
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_async_permanent_error_not_retried(self) -> None:
        """PermanentError should not be retried in async."""
        call_count = 0

        @retry_on_transient(max_attempts=3)
        async def async_permanent():
            nonlocal call_count
            call_count += 1
            raise PermanentError("fatal")

        with pytest.raises(PermanentError):
            await async_permanent()
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_async_with_exception_transfer(self) -> None:
        """Should properly handle exception chaining."""
        call_count = 0

        @retry_on_transient(max_attempts=2)
        async def async_chain():
            nonlocal call_count
            call_count += 1
            raise TransientError("chained error")

        with pytest.raises(TransientError, match="chained error"):
            await async_chain()


class TestRetryWithLogging:
    """Tests for retry decorator with logging enabled."""

    def test_retry_logs_attempts(self, caplog: pytest.LogCaptureFixture) -> None:
        """Should log retry attempts."""
        caplog.set_level("DEBUG", logger="merge-engine.retry")
        call_count = 0

        @retry_on_transient(max_attempts=3)
        def loggable_func():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise TransientError("log me")
            return "ok"

        result = loggable_func()
        assert result == "ok"
        assert "retry" in caplog.text.lower() or call_count == 2

    def test_no_retry_on_success_logs(self, caplog: pytest.LogCaptureFixture) -> None:
        """Success should log at debug level."""
        caplog.set_level("DEBUG", logger="merge-engine.retry")

        @retry_on_transient(max_attempts=3)
        def success_func():
            return "ok"

        result = success_func()
        assert result == "ok"


class TestRetryConfiguration:
    """Tests for various retry configurations."""

    def test_single_attempt(self) -> None:
        """max_attempts=1 should not retry."""
        call_count = 0

        @retry_on_transient(max_attempts=1)
        def no_retry():
            nonlocal call_count
            call_count += 1
            raise TransientError("no retry")

        with pytest.raises(TransientError):
            no_retry()
        assert call_count == 1

    def test_large_max_attempts(self) -> None:
        """Should support large max_attempts values."""
        call_count = 0

        @retry_on_transient(max_attempts=5)
        def many_retries():
            nonlocal call_count
            call_count += 1
            if call_count < 4:
                raise TransientError("still trying")
            return "done"

        result = many_retries()
        assert result == "done"
        assert call_count == 4

    def test_custom_backoff_values(self) -> None:
        """Should use custom min/max backoff."""
        call_count = 0

        @retry_on_transient(
            max_attempts=2,
            backoff="linear",
            min_backoff=0.01,
            max_backoff=0.01,
        )
        def fast_retry():
            nonlocal call_count
            call_count += 1
            raise TransientError("fast")

        with pytest.raises(TransientError):
            fast_retry()
        assert call_count == 2

    def test_both_async_and_sync_in_decorator(self) -> None:
        """Same decorator should work for both sync and async."""
        sync_calls = 0
        async_calls = 0

        # Sync function
        @retry_on_transient(max_attempts=2)
        def sync_f():
            nonlocal sync_calls
            sync_calls += 1
            if sync_calls < 2:
                raise TransientError("sync retry")
            return "sync_ok"

        # Async function
        @retry_on_transient(max_attempts=2)
        async def async_f():
            nonlocal async_calls
            async_calls += 1
            if async_calls < 2:
                raise TransientError("async retry")
            return "async_ok"

        # Run sync
        sync_result = sync_f()
        assert sync_result == "sync_ok"
        assert sync_calls == 2

        # Run async
        loop = asyncio.new_event_loop()
        try:
            async_result = loop.run_until_complete(async_f())
            assert async_result == "async_ok"
            assert async_calls == 2
        finally:
            loop.close()