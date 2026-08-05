"""
Tests for src/orchestrator/utils/retry.py - Retry logic wrapper using tenacity.
"""

import pytest

from src.orchestrator.utils.retry import (
    PermanentError,
    TransientError,
    _build_wait_strategy,
    retry_on_transient,
)


class TestTransientError:
    """Tests for TransientError exception."""

    def test_transient_error_inherits_from_exception(self):
        """TransientError should inherit from Exception."""
        assert issubclass(TransientError, Exception)

    def test_transient_error_message(self):
        """TransientError should store the error message."""
        err = TransientError("connection timeout")
        assert str(err) == "connection timeout"

    def test_transient_error_retry_after(self):
        """TransientError should store retry_after value."""
        err = TransientError("rate limited", retry_after=5.0)
        assert err.retry_after == 5.0

    def test_transient_error_default_retry_after(self):
        """TransientError should default retry_after to None."""
        err = TransientError("some error")
        assert err.retry_after is None


class TestPermanentError:
    """Tests for PermanentError exception."""

    def test_permanent_error_inherits_from_exception(self):
        """PermanentError should inherit from Exception."""
        assert issubclass(PermanentError, Exception)

    def test_permanent_error_message(self):
        """PermanentError should store the error message."""
        err = PermanentError("invalid config")
        assert str(err) == "invalid config"


class TestBuildWaitStrategy:
    """Tests for _build_wait_strategy()."""

    def test_exponential_backoff(self):
        """Exponential backoff should build a wait chain."""
        strategy = _build_wait_strategy("exponential", 1.0, 30.0)
        assert strategy is not None

    def test_linear_backoff(self):
        """Linear backoff should build a fixed wait."""
        strategy = _build_wait_strategy("linear", 2.0, 10.0)
        assert strategy is not None

    def test_unknown_backoff_defaults_to_exponential(self):
        """Unknown backoff strategy should default to exponential."""
        strategy = _build_wait_strategy("unknown", 1.0, 30.0)
        assert strategy is not None


class TestRetryOnTransientSync:
    """Tests for retry_on_transient decorator with synchronous functions."""

    def test_success_on_first_attempt(self):
        """Function that succeeds should return immediately."""

        @retry_on_transient(max_attempts=3)
        def success_func():
            return "success"

        result = success_func()
        assert result == "success"

    def test_retry_on_transient_error(self):
        """Function should retry on TransientError."""
        call_count = 0

        @retry_on_transient(max_attempts=3)
        def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise TransientError("temporary failure")
            return "recovered"

        result = flaky_func()
        assert result == "recovered"
        assert call_count == 3

    def test_exhausts_max_attempts(self):
        """Function should raise after max_attempts."""
        call_count = 0

        @retry_on_transient(max_attempts=3)
        def always_fails():
            nonlocal call_count
            call_count += 1
            raise TransientError("permanent transient error")

        with pytest.raises(TransientError):
            always_fails()
        assert call_count == 3

    def test_no_retry_on_permanent_error(self):
        """PermanentError should not be retried."""
        call_count = 0

        @retry_on_transient(max_attempts=3)
        def fails_permanently():
            nonlocal call_count
            call_count += 1
            raise PermanentError("cannot recover")

        with pytest.raises(PermanentError):
            fails_permanently()
        assert call_count == 1

    def test_custom_retryable_exceptions(self):
        """Custom retryable exceptions should be respected."""
        call_count = 0

        class CustomRetryable(Exception):
            pass

        @retry_on_transient(
            max_attempts=2,
            retryable_exceptions=(CustomRetryable,),
        )
        def fails_with_custom():
            nonlocal call_count
            call_count += 1
            raise CustomRetryable("custom error")

        with pytest.raises(CustomRetryable):
            fails_with_custom()
        assert call_count == 2

    def test_no_retry_on_non_retryable_exception(self):
        """Non-retryable exceptions should not trigger retry."""
        call_count = 0

        @retry_on_transient(max_attempts=3)
        def fails_with_value_error():
            nonlocal call_count
            call_count += 1
            raise ValueError("not retryable")

        with pytest.raises(ValueError):
            fails_with_value_error()
        assert call_count == 1


class TestRetryOnTransientAsync:
    """Tests for retry_on_transient decorator with async functions."""

    @pytest.mark.asyncio
    async def test_async_success_on_first_attempt(self):
        """Async function that succeeds should return immediately."""

        @retry_on_transient(max_attempts=3)
        async def async_success():
            return "async_success"

        result = await async_success()
        assert result == "async_success"

    @pytest.mark.asyncio
    async def test_async_retry_on_transient_error(self):
        """Async function should retry on TransientError."""
        call_count = 0

        @retry_on_transient(max_attempts=3)
        async def async_flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise TransientError("temporary async failure")
            return "async_recovered"

        result = await async_flaky()
        assert result == "async_recovered"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_async_exhausts_max_attempts(self):
        """Async function should raise after max_attempts."""
        call_count = 0

        @retry_on_transient(max_attempts=2)
        async def async_always_fails():
            nonlocal call_count
            call_count += 1
            raise TransientError("async permanent transient error")

        with pytest.raises(TransientError):
            await async_always_fails()
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_async_no_retry_on_permanent_error(self):
        """Async PermanentError should not be retried."""
        call_count = 0

        @retry_on_transient(max_attempts=3)
        async def async_fails_permanently():
            nonlocal call_count
            call_count += 1
            raise PermanentError("async cannot recover")

        with pytest.raises(PermanentError):
            await async_fails_permanently()
        assert call_count == 1


class TestRetryConfiguration:
    """Tests for retry configuration parameters."""

    def test_custom_max_attempts(self):
        """Custom max_attempts should be respected."""
        call_count = 0

        @retry_on_transient(max_attempts=5)
        def func():
            nonlocal call_count
            call_count += 1
            raise TransientError("keep retrying")

        with pytest.raises(TransientError):
            func()
        assert call_count == 5

    def test_linear_backoff_config(self):
        """Linear backoff should work with custom parameters."""
        call_count = 0

        @retry_on_transient(
            max_attempts=2,
            backoff="linear",
            min_backoff=0.01,
        )
        def func():
            nonlocal call_count
            call_count += 1
            raise TransientError("linear backoff test")

        with pytest.raises(TransientError):
            func()
        assert call_count == 2

    def test_exponential_backoff_config(self):
        """Exponential backoff should work with custom parameters."""
        call_count = 0

        @retry_on_transient(
            max_attempts=2,
            backoff="exponential",
            min_backoff=0.01,
            max_backoff=5.0,
        )
        def func():
            nonlocal call_count
            call_count += 1
            raise TransientError("exponential backoff test")

        with pytest.raises(TransientError):
            func()
        assert call_count == 2
