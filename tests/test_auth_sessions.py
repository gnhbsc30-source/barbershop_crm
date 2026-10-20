from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import hashlib
from threading import Barrier

import pytest

from app.core.auth_sessions import InMemorySessionStore, SessionError
from app.services.authentication import Principal, PrincipalRole


@pytest.fixture
def clock():
    return [datetime(2026, 10, 20, tzinfo=timezone.utc)]


@pytest.fixture
def principal():
    return Principal("user:1", PrincipalRole.OWNER, 1, owner_user_id=1)


def test_create_resolve_opaque_digest_only_and_csrf(clock, principal):
    store = InMemorySessionStore(lambda: clock[0])
    issued = store.create(principal, "a" * 64)
    context = store.resolve(issued.token)
    assert context.principal == principal
    assert context.created_at == clock[0]
    assert context.expires_at == clock[0] + timedelta(hours=1)
    assert len(issued.token) == len(context.csrf_token) == 43
    assert issued.token != context.csrf_token
    assert list(store._sessions) == [hashlib.sha256(issued.token.encode()).hexdigest()]
    assert issued.token not in repr(issued)
    assert context.csrf_token not in repr(context)
    store.verify_csrf(issued.token, context.csrf_token)
    with pytest.raises(FrozenInstanceError):
        context.expires_at = clock[0]


@pytest.mark.parametrize("seconds,valid", [(3599.999, True), (3600, False), (3601, False)])
def test_absolute_expiry_boundary(clock, principal, seconds, valid):
    store = InMemorySessionStore(lambda: clock[0])
    issued = store.create(principal, "a" * 64)
    clock[0] += timedelta(seconds=seconds)
    if valid:
        assert store.resolve(issued.token) == issued.context
        assert store.resolve(issued.token).expires_at == issued.context.expires_at
    else:
        with pytest.raises(SessionError, match="UNAUTHENTICATED"):
            store.resolve(issued.token)
        assert not store._sessions


def test_login_rotation_and_logout(clock, principal):
    store = InMemorySessionStore(lambda: clock[0], max_sessions=1)
    old = store.create(principal, "a" * 64)
    new = store.create(principal, "a" * 64, previous_token=old.token)
    assert old.token != new.token
    assert old.context.csrf_token != new.context.csrf_token
    with pytest.raises(SessionError):
        store.resolve(old.token)
    assert store.resolve(new.token) == new.context
    store.invalidate(new.token)
    store.invalidate(new.token)
    store.invalidate("malformed")
    with pytest.raises(SessionError):
        store.resolve(new.token)


@pytest.mark.parametrize("value", [None, "", "short", "é" * 43, "?" * 43, "x" * 43])
def test_invalid_or_unknown_tokens(clock, value):
    with pytest.raises(SessionError, match="UNAUTHENTICATED"):
        InMemorySessionStore(lambda: clock[0]).resolve(value)


@pytest.mark.parametrize("value", [None, "", "wrong", "é" * 43])
def test_csrf_failure_keeps_valid_session(clock, principal, value):
    store = InMemorySessionStore(lambda: clock[0])
    issued = store.create(principal, "a" * 64)
    with pytest.raises(SessionError, match="CSRF_FAILED"):
        store.verify_csrf(issued.token, value)
    assert store.resolve(issued.token) == issued.context


def test_csrf_is_session_scoped(clock, principal):
    store = InMemorySessionStore(lambda: clock[0])
    first, second = store.create(principal, "a" * 64), store.create(principal, "a" * 64)
    with pytest.raises(SessionError, match="CSRF_FAILED"):
        store.verify_csrf(first.token, second.context.csrf_token)


@pytest.mark.parametrize("changed", ["fingerprint", "principal"])
def test_fresh_identity_binding_mismatch_invalidates_only_target(clock, principal, changed):
    store = InMemorySessionStore(lambda: clock[0])
    first, second = store.create(principal, "a" * 64), store.create(principal, "a" * 64)
    assert store.validate_identity(first.token, principal, "a" * 64) == first.context
    other = Principal("user:2", PrincipalRole.OWNER, 2, owner_user_id=2)
    with pytest.raises(SessionError, match="UNAUTHENTICATED"):
        store.validate_identity(first.token, other if changed == "principal" else principal,
                                "b" * 64 if changed == "fingerprint" else "a" * 64)
    with pytest.raises(SessionError):
        store.resolve(first.token)
    assert store.resolve(second.token) == second.context


def test_capacity_cleanup_and_failed_creation_does_not_revoke(clock, principal):
    store = InMemorySessionStore(lambda: clock[0], max_sessions=1)
    first = store.create(principal, "a" * 64)
    with pytest.raises(SessionError, match="SESSION_CAPACITY_REACHED"):
        store.create(principal, "a" * 64)
    with pytest.raises(ValueError, match="INVALID_CREDENTIAL_FINGERPRINT"):
        store.create(principal, "invalid", previous_token=first.token)
    assert store.resolve(first.token)
    clock[0] += timedelta(hours=1)
    assert store.create(principal, "a" * 64)
    assert len(store._sessions) == 1


def test_simultaneous_creations_respect_capacity(clock, principal):
    store = InMemorySessionStore(lambda: clock[0], max_sessions=5)
    barrier = Barrier(12)

    def create(_):
        barrier.wait(timeout=5)
        try:
            return store.create(principal, "a" * 64).token
        except SessionError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(create, range(12)))
    tokens = [value for value in results if value != "SESSION_CAPACITY_REACHED"]
    assert len(tokens) == len(set(tokens)) == len(store._sessions) == 5
    assert results.count("SESSION_CAPACITY_REACHED") == 7


def test_naive_clock_and_invalid_capacity_are_refused(principal):
    with pytest.raises(ValueError, match="SESSION_CLOCK_MUST_BE_AWARE"):
        InMemorySessionStore(datetime.now).create(principal, "a" * 64)
    for value in (0, -1, True):
        with pytest.raises(ValueError):
            InMemorySessionStore(max_sessions=value)
