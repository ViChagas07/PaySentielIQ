# ============================================================
# PaySentinelIQ — Consumer Retry Policy
# ============================================================
# Decides what happens when a handler fails:
#   - permanent error            → dead-letter immediately
#   - transient error, attempts  → republish to the retry queue
#     < max                        with exponential backoff
#   - transient error, attempts  → dead-letter (no infinite loops)
#     >= max
# ============================================================

from __future__ import annotations

from dataclasses import dataclass

from app.messaging.domain.ports import PermanentProcessingError


@dataclass(frozen=True)
class RetryDecision:
    """Outcome of evaluating a handler failure."""

    should_retry: bool
    delay_seconds: float
    next_attempt: int  # retry count that the republished message will carry
    reason: str


@dataclass(frozen=True)
class RetryPolicy:
    """Configurable retry policy for event consumers."""

    max_attempts: int = 3
    backoff_seconds: tuple[int, ...] = (5, 30, 120)

    def backoff_for(self, attempt: int) -> float:
        """Delay before attempt ``attempt`` (1-based). Last value repeats."""
        if not self.backoff_seconds:
            return 0.0
        index = min(max(attempt - 1, 0), len(self.backoff_seconds) - 1)
        return float(self.backoff_seconds[index])

    def evaluate(self, exc: BaseException, current_retry_count: int) -> RetryDecision:
        """Classify a failure and decide whether to retry.

        ``current_retry_count`` is the number of retries the message has
        already been through (0 = first failure).
        """
        if isinstance(exc, PermanentProcessingError):
            return RetryDecision(
                should_retry=False,
                delay_seconds=0.0,
                next_attempt=current_retry_count,
                reason=f"permanent: {exc}",
            )

        next_attempt = current_retry_count + 1
        if next_attempt >= self.max_attempts:
            return RetryDecision(
                should_retry=False,
                delay_seconds=0.0,
                next_attempt=next_attempt,
                reason=f"max attempts reached ({self.max_attempts}): {exc}",
            )

        return RetryDecision(
            should_retry=True,
            delay_seconds=self.backoff_for(next_attempt),
            next_attempt=next_attempt,
            reason=f"transient: {exc}",
        )


def is_permanent_error(exc: BaseException) -> bool:
    """True when retrying can never succeed (bad payload, unknown entity...)."""
    return isinstance(exc, (PermanentProcessingError, ValueError, KeyError, TypeError))
