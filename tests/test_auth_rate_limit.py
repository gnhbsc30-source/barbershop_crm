from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app.core.auth_rate_limit import AuthenticationRateLimitError, AuthenticationRateLimiter


def test_selector_limit_exact_five_minute_boundary():
    clock = [0.0]
    limiter = AuthenticationRateLimiter(lambda: clock[0])
    for _ in range(5):
        limiter.check_and_record("selector")
    with pytest.raises(AuthenticationRateLimitError) as caught:
        limiter.check_and_record("selector")
    assert caught.value.code == "AUTH_RATE_LIMITED"
    assert caught.value.retry_after_seconds == 300
    clock[0] = 299.999
    with pytest.raises(AuthenticationRateLimitError) as caught:
        limiter.check_and_record("selector")
    assert caught.value.retry_after_seconds == 1
    clock[0] = 300
    limiter.check_and_record("selector")
    assert list(limiter._selectors["selector"]) == [300]


def test_global_limit_exact_minute_boundary():
    clock = [0.0]
    limiter = AuthenticationRateLimiter(lambda: clock[0])
    for index in range(20):
        limiter.check_and_record(str(index))
    clock[0] = 59.999
    with pytest.raises(AuthenticationRateLimitError) as caught:
        limiter.check_and_record("other")
    assert caught.value.retry_after_seconds == 1
    clock[0] = 60
    limiter.check_and_record("other")
    assert list(limiter._global) == [60]


def test_sliding_windows_do_not_reset_all_attempts_at_once():
    clock = [0.0]
    limiter = AuthenticationRateLimiter(lambda: clock[0])
    for instant in (0, 10, 20, 30, 40):
        clock[0] = instant
        limiter.check_and_record("selector")
    clock[0] = 300
    limiter.check_and_record("selector")
    with pytest.raises(AuthenticationRateLimitError) as caught:
        limiter.check_and_record("selector")
    assert caught.value.retry_after_seconds == 10


def test_both_limits_report_longer_wait_and_denials_do_not_extend_it():
    clock = [0.0]
    limiter = AuthenticationRateLimiter(lambda: clock[0])
    for index in range(20):
        limiter.check_and_record(str(index // 5))
    for _ in range(3):
        with pytest.raises(AuthenticationRateLimitError) as caught:
            limiter.check_and_record("0")
        assert caught.value.retry_after_seconds == 300
    assert len(limiter._global) == 20
    assert len(limiter._selectors["0"]) == 5


def test_state_capacity_never_evicts_live_limits():
    clock = [0.0]
    limiter = AuthenticationRateLimiter(lambda: clock[0], max_selectors=1)
    limiter.check_and_record("first")
    with pytest.raises(AuthenticationRateLimitError):
        limiter.check_and_record("second")
    assert list(limiter._selectors) == ["first"]
    clock[0] = 300
    limiter.check_and_record("second")
    assert list(limiter._selectors) == ["second"]


@pytest.mark.parametrize("same_selector,accepted", [(True, 5), (False, 20)])
def test_simultaneous_attempts_are_atomic(same_selector, accepted):
    limiter = AuthenticationRateLimiter(lambda: 0.0)
    barrier = Barrier(30)

    def attempt(index):
        barrier.wait(timeout=5)
        try:
            limiter.check_and_record("same" if same_selector else str(index))
            return True
        except AuthenticationRateLimitError:
            return False

    with ThreadPoolExecutor(max_workers=30) as pool:
        results = list(pool.map(attempt, range(30)))
    assert sum(results) == accepted
    assert len(limiter._global) == accepted


def test_state_stays_bounded_during_many_unknown_selectors():
    clock = [0.0]
    limiter = AuthenticationRateLimiter(lambda: clock[0])
    for minute in range(20):
        clock[0] = minute * 60
        for index in range(20):
            limiter.check_and_record(f"unknown-{minute}-{index}")
        assert len(limiter._selectors) <= 100
        assert len(limiter._global) == 20


@pytest.mark.parametrize("selector", [None, "", "s" * 65])
def test_invalid_input_does_not_allocate_state(selector):
    limiter = AuthenticationRateLimiter()
    with pytest.raises(ValueError):
        limiter.check_and_record(selector)
    assert not limiter._selectors and not limiter._global
