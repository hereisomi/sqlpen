"""
utils/retry.py — Connection resilience helpers.

Provides a pre-configured tenacity retry decorator for SQL operations
that may fail transiently (network blips, pool exhaustion, etc.).

Usage
-----
    from utils.retry import db_retry

    @db_retry()
    def my_sql_op(engine): ...

Or decorate an entire method::

    class MyCrud:
        @db_retry()
        def insert(self, ...): ...
"""
from __future__ import annotations

import logging

from sqlalchemy.exc import DBAPIError, OperationalError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

#: Tuple of exception types that warrant a retry.
DB_RETRYABLE: tuple[type[Exception], ...] = (DBAPIError, OperationalError)

try:
    from tenacity import (
        retry,
        retry_if_exception_type,
        stop_after_attempt,
        wait_exponential,
        before_sleep_log,
    )

    _HAS_TENACITY = True

    def db_retry(
        *,
        max_attempts: int = 3,
        multiplier: float = 0.5,
        min_wait: float = 0.5,
        max_wait: float = 10.0,
    ):
        """Return a tenacity retry decorator tuned for transient DB errors.

        Parameters
        ----------
        max_attempts : int
            Total number of attempts (including the first).
        multiplier : float
            Exponential back-off multiplier (seconds).
        min_wait : float
            Minimum wait between retries (seconds).
        max_wait : float
            Maximum wait between retries (seconds).
        """
        return retry(
            retry=retry_if_exception_type(DB_RETRYABLE),
            wait=wait_exponential(multiplier=multiplier, min=min_wait, max=max_wait),
            stop=stop_after_attempt(max_attempts),
            reraise=True,
            before_sleep=before_sleep_log(logger, logging.WARNING),
        )

except ImportError:  # noqa: BLE001 – tenacity is optional
    _HAS_TENACITY = False

    def db_retry(  # type: ignore[misc]
        *,
        max_attempts: int = 3,
        multiplier: float = 0.5,
        min_wait: float = 0.5,
        max_wait: float = 10.0,
    ):
        """No-op decorator when tenacity is not installed."""
        logger.debug(
            "tenacity not installed — db_retry() is a no-op. "
            "Install with: pip install tenacity"
        )

        def _passthrough(func):
            return func

        return _passthrough
