"""レート制限のテスト。"""

from __future__ import annotations

from ir_review.ratelimit import RateLimiter


def test_allows_up_to_the_limit() -> None:
    limiter = RateLimiter(limit=3)
    assert [limiter.check("a")[0] for _ in range(4)] == [True, True, True, False]


def test_remaining_counts_down() -> None:
    limiter = RateLimiter(limit=2)
    assert limiter.check("a") == (True, 1)
    assert limiter.check("a") == (True, 0)


def test_clients_are_independent() -> None:
    limiter = RateLimiter(limit=1)
    assert limiter.check("a")[0] is True
    assert limiter.check("b")[0] is True
    assert limiter.check("a")[0] is False


def test_zero_limit_means_unlimited() -> None:
    limiter = RateLimiter(limit=0)
    assert all(limiter.check("a")[0] for _ in range(50))


def test_window_expires() -> None:
    """古いリクエストは窓から外れて、また通るようになること。"""
    limiter = RateLimiter(limit=1, window_seconds=0.05)
    assert limiter.check("a")[0] is True
    assert limiter.check("a")[0] is False

    import time

    time.sleep(0.08)
    assert limiter.check("a")[0] is True


def test_retry_after_is_positive_when_blocked() -> None:
    limiter = RateLimiter(limit=1, window_seconds=60)
    limiter.check("a")
    assert limiter.retry_after_seconds("a") > 0


def test_retry_after_is_zero_when_allowed() -> None:
    limiter = RateLimiter(limit=5, window_seconds=60)
    limiter.check("a")
    assert limiter.retry_after_seconds("a") == 0
