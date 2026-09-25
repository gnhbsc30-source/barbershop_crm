from datetime import datetime, timedelta

import pytest

from app.services.alternative_selection import (
    AlternativeSelectionError,
    AlternativeSelectionOption,
    InMemoryAlternativeSelectionContextStore,
)


class MutableClock:
    def __init__(self, value: datetime):
        self.value = value

    def now(self) -> datetime:
        return self.value


def make_options() -> list[AlternativeSelectionOption]:
    return [
        AlternativeSelectionOption(
            option_id="option_001",
            staff_id=10,
            start_datetime=datetime(2027, 1, 15, 9, 0),
        ),
        AlternativeSelectionOption(
            option_id="option_002",
            staff_id=11,
            start_datetime=datetime(2027, 1, 15, 10, 0),
        ),
    ]


def create_context(store, *, business_id=1, service_ids=(4, 7), options=None):
    return store.create_search_context(
        business_id=business_id,
        requested_service_ids=list(service_ids),
        options=options or make_options(),
    )


def test_create_context_uses_unique_opaque_ids_and_stores_minimal_data():
    clock = MutableClock(datetime(2027, 1, 15, 8, 0))
    store = InMemoryAlternativeSelectionContextStore(now_provider=clock.now)

    first = create_context(store)
    second = create_context(store)

    assert first.search_id != second.search_id
    assert isinstance(first.search_id, str)
    assert first.business_id == 1
    assert first.requested_service_ids == (4, 7)
    assert first.created_at == datetime(2027, 1, 15, 8, 0)
    assert first.expires_at == datetime(2027, 1, 15, 8, 15)
    assert first.options == tuple(make_options())
    assert first.options[0].__dict__ == {
        "option_id": "option_001",
        "staff_id": 10,
        "start_datetime": datetime(2027, 1, 15, 9, 0),
    }


def test_resolve_valid_selection_returns_trusted_minimal_context():
    store = InMemoryAlternativeSelectionContextStore(
        now_provider=lambda: datetime(2027, 1, 15, 8, 0)
    )
    context = create_context(store, business_id=3, service_ids=(4, 7))

    selection = store.resolve_selection(context.search_id, "option_002")

    assert selection.search_id == context.search_id
    assert selection.business_id == 3
    assert selection.requested_service_ids == (4, 7)
    assert selection.option_id == "option_002"
    assert selection.staff_id == 11
    assert selection.start_datetime == datetime(2027, 1, 15, 10, 0)


def test_resolve_rejects_unknown_option_in_valid_context():
    store = InMemoryAlternativeSelectionContextStore(
        now_provider=lambda: datetime(2027, 1, 15, 8, 0)
    )
    context = create_context(store)

    with pytest.raises(AlternativeSelectionError, match="ALTERNATIVE_OPTION_NOT_FOUND"):
        store.resolve_selection(context.search_id, "option_missing")


def test_resolve_rejects_unknown_search_id_as_expired():
    store = InMemoryAlternativeSelectionContextStore(
        now_provider=lambda: datetime(2027, 1, 15, 8, 0)
    )

    with pytest.raises(AlternativeSelectionError, match="ALTERNATIVE_SEARCH_EXPIRED"):
        store.resolve_selection("unknown", "option_001")


def test_context_is_valid_before_expiration():
    clock = MutableClock(datetime(2027, 1, 15, 8, 0))
    store = InMemoryAlternativeSelectionContextStore(now_provider=clock.now)
    context = create_context(store)
    clock.value = context.expires_at - timedelta(microseconds=1)

    assert store.resolve_selection(context.search_id, "option_001").staff_id == 10


@pytest.mark.parametrize("offset", [timedelta(), timedelta(microseconds=1)])
def test_context_is_expired_at_and_after_expiration(offset):
    clock = MutableClock(datetime(2027, 1, 15, 8, 0))
    store = InMemoryAlternativeSelectionContextStore(now_provider=clock.now)
    context = create_context(store)
    clock.value = context.expires_at + offset

    with pytest.raises(AlternativeSelectionError, match="ALTERNATIVE_SEARCH_EXPIRED"):
        store.resolve_selection(context.search_id, "option_001")


def test_explicit_expiration_invalidates_context():
    store = InMemoryAlternativeSelectionContextStore(
        now_provider=lambda: datetime(2027, 1, 15, 8, 0)
    )
    context = create_context(store)

    store.expire_search_context(context.search_id)

    with pytest.raises(AlternativeSelectionError, match="ALTERNATIVE_SEARCH_EXPIRED"):
        store.resolve_selection(context.search_id, "option_001")


def test_option_id_is_scoped_to_its_search_context():
    store = InMemoryAlternativeSelectionContextStore(
        now_provider=lambda: datetime(2027, 1, 15, 8, 0)
    )
    first = create_context(
        store,
        business_id=1,
        options=[
            AlternativeSelectionOption(
                option_id="option_001",
                staff_id=10,
                start_datetime=datetime(2027, 1, 15, 9, 0),
            )
        ],
    )
    second = create_context(
        store,
        business_id=2,
        options=[
            AlternativeSelectionOption(
                option_id="option_001",
                staff_id=20,
                start_datetime=datetime(2027, 1, 15, 11, 0),
            )
        ],
    )

    assert store.resolve_selection(first.search_id, "option_001").staff_id == 10
    assert store.resolve_selection(second.search_id, "option_001").staff_id == 20


def test_input_list_mutation_does_not_change_stored_context():
    store = InMemoryAlternativeSelectionContextStore(
        now_provider=lambda: datetime(2027, 1, 15, 8, 0)
    )
    service_ids = [4, 7]
    options = make_options()
    context = store.create_search_context(
        business_id=1,
        requested_service_ids=service_ids,
        options=options,
    )

    service_ids.append(9)
    options.clear()

    selection = store.resolve_selection(context.search_id, "option_001")
    assert selection.requested_service_ids == (4, 7)
    assert selection.staff_id == 10
