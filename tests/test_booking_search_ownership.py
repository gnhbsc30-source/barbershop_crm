from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier, Event

import pytest

from app.core.booking_search_ownership import BookingSearchOwnership, SearchOwner, SearchAdmissionError
from app.services.alternative_selection import InMemoryAlternativeSelectionContextStore, AlternativeSelectionOption, AlternativeSelectionError


@pytest.fixture
def registry():
    clock = [datetime(2027, 1, 4, tzinfo=timezone.utc)]
    store = InMemoryAlternativeSelectionContextStore(lambda: clock[0])
    return BookingSearchOwnership(store, lambda: clock[0]), store, clock


def owner(n=1, session=None):
    return SearchOwner(f"customer_account:{n}", 1, n + 10, session or str(n))


def publish(registry, who):
    reg, store, _ = registry
    with reg.computation(who, True) as ticket:
        context = store.create_search_context(business_id=1, requested_service_ids=[1],
            options=[AlternativeSelectionOption("option_001", 1, datetime(2027, 1, 4, 10))])
        reg.publish(ticket, context.search_id)
    return context


@pytest.mark.parametrize("foreign", [owner(2), owner(1, "new-session"), SearchOwner("customer_account:1", 2, 11, "1"), SearchOwner("customer_account:1", 1, 12, "1")])
def test_ownership_requires_all_identity_fields(registry, foreign):
    reg, store, _ = registry
    context = publish(registry, owner())
    reg.authorize(context.search_id, owner())
    with pytest.raises(AlternativeSelectionError, match="ALTERNATIVE_SEARCH_EXPIRED"):
        reg.authorize(context.search_id, foreign)
    assert store.resolve_selection(context.search_id, "option_001")


def test_session_capacity_never_evicts_claimed_context(registry):
    reg, store, clock = registry
    contexts = [publish(registry, owner()) for _ in range(5)]
    store.claim_selection(contexts[0].search_id, "option_001")
    with pytest.raises(SearchAdmissionError) as caught:
        with reg.computation(owner(), True):
            pytest.fail("must not admit")
    assert caught.value.status == 429
    assert len(store._contexts) == 5
    assert contexts[0].search_id in store._claimed_search_ids
    clock[0] += timedelta(minutes=15)
    publish(registry, owner())
    assert len(store._contexts) == len(reg._records) == 1
    assert not store._claimed_search_ids


def test_global_capacity_and_exact_expiry(registry):
    reg, store, clock = registry
    contexts = [publish(registry, owner(n)) for n in range(128)]
    with pytest.raises(SearchAdmissionError) as caught:
        publish(registry, owner(999))
    assert caught.value.status == 503
    clock[0] += timedelta(minutes=15) - timedelta(microseconds=1)
    reg.authorize(contexts[0].search_id, owner(0))
    clock[0] += timedelta(microseconds=1)
    with pytest.raises(AlternativeSelectionError):
        reg.authorize(contexts[0].search_id, owner(0))
    assert not reg._records and not store._contexts


def test_failed_publication_and_computation_release_capacity(registry):
    reg, store, _ = registry
    with pytest.raises(RuntimeError):
        with reg.computation(owner(), True) as ticket:
            context = store.create_search_context(business_id=1, requested_service_ids=[1], options=[])
            reg.publish(ticket, context.search_id)
            raise RuntimeError("test failure")
    assert not reg._pending and not reg._records and not store._contexts
    with reg.computation(owner(), False):
        pass


def test_computation_limits_are_atomic_and_pending_does_not_expire(registry):
    reg, _, clock = registry
    entered = Barrier(3)
    release = Event()

    def work(who):
        with reg.computation(who, True):
            entered.wait(timeout=5)
            assert release.wait(5)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(work, owner(n)) for n in (1, 2)]
        entered.wait(timeout=5)
        try:
            clock[0] += timedelta(hours=1)
            with pytest.raises(SearchAdmissionError) as same:
                with reg.computation(owner(1), True):
                    pytest.fail("same session")
            assert same.value.status == 429
            with pytest.raises(SearchAdmissionError) as other:
                with reg.computation(owner(3), True):
                    pytest.fail("third computation")
            assert other.value.status == 503
        finally:
            release.set()
        for future in futures:
            future.result(5)
    assert not reg._pending
