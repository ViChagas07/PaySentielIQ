# ============================================================
# PaySentinelIQ — Retry Policy Tests
# ============================================================

from app.messaging.application.retry import RetryPolicy
from app.messaging.domain.ports import (
    PermanentProcessingError,
    TransientProcessingError,
)


def test_transient_error_schedules_retry_with_backoff():
    policy = RetryPolicy(max_attempts=3, backoff_seconds=(5, 30, 120))
    decision = policy.evaluate(TransientProcessingError("db down"), current_retry_count=0)
    assert decision.should_retry is True
    assert decision.delay_seconds == 5
    assert decision.next_attempt == 1


def test_backoff_sequence():
    policy = RetryPolicy(max_attempts=3, backoff_seconds=(5, 30, 120))
    assert policy.evaluate(TransientProcessingError("x"), 0).delay_seconds == 5
    assert policy.evaluate(TransientProcessingError("x"), 1).delay_seconds == 30
    # Attempt 3 is the last one — a failure there dead-letters (no retry).
    final = policy.evaluate(TransientProcessingError("x"), 2)
    assert final.should_retry is False
    assert final.delay_seconds == 0


def test_max_attempts_ends_in_dead_letter():
    policy = RetryPolicy(max_attempts=3, backoff_seconds=(5, 30, 120))
    decision = policy.evaluate(TransientProcessingError("still failing"), current_retry_count=2)
    assert decision.should_retry is False
    assert "max attempts" in decision.reason


def test_permanent_error_never_retries():
    policy = RetryPolicy(max_attempts=3, backoff_seconds=(5, 30, 120))
    decision = policy.evaluate(PermanentProcessingError("invalid payload"), current_retry_count=0)
    assert decision.should_retry is False
    assert decision.next_attempt == 0


def test_backoff_last_value_repeats():
    policy = RetryPolicy(max_attempts=5, backoff_seconds=(5, 30, 120))
    assert policy.backoff_for(3) == 120
    assert policy.backoff_for(4) == 120
    assert policy.backoff_for(10) == 120
