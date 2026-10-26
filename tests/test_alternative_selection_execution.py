import inspect
import threading
from datetime import datetime, timedelta

import pytest

from app.services.alternative_selection import (
    AlternativeSelectionError,
    AlternativeSelectionOption,
    InMemoryAlternativeSelectionContextStore,
)
from app.services.alternative_selection_execution import AlternativeSelectionService
from app.services.appointments import (
    AppointmentBookingError,
    AppointmentBookingResult,
    AppointmentService,
)


class MutableClock:
    def __init__(self, value: datetime):
        self.value = value

    def now(self) -> datetime:
        return self.value


def setup_selection_booking(connection, *, minimum_notice_minutes=0):
    now = datetime(2027, 1, 15, 8, 0)
    first_start = datetime(2027, 1, 15, 10, 0)
    second_start = datetime(2027, 1, 15, 11, 0)
    connection.execute("INSERT INTO businesses (business_name) VALUES (?)", ("Shop",))
    business_id = connection.execute("SELECT id FROM businesses").fetchone()["id"]
    connection.execute(
        """
        INSERT INTO business_settings (
            business_id, booking_window_months, minimum_booking_notice_minutes
        ) VALUES (?, ?, ?)
        """,
        (business_id, 3, minimum_notice_minutes),
    )
    connection.execute(
        "INSERT INTO customers (business_id, name, customer_type) VALUES (?, ?, ?)",
        (business_id, "Dana", "GUEST"),
    )
    customer_id = connection.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    staff_ids = []
    for name in ("Noa", "Maya"):
        connection.execute(
            "INSERT INTO staff (business_id, name) VALUES (?, ?)",
            (business_id, name),
        )
        staff_ids.append(
            connection.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        )

    connection.execute(
        """
        INSERT INTO services (business_id, name, default_duration_minutes, default_price)
        VALUES (?, ?, ?, ?)
        """,
        (business_id, "Haircut", 45, 80.0),
    )
    service_id = connection.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    connection.execute(
        """
        INSERT INTO staff_services (
            business_id, staff_id, service_id, duration_minutes, price
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (business_id, staff_ids[0], service_id, 60, 100.0),
    )
    connection.execute(
        """
        INSERT INTO staff_services (business_id, staff_id, service_id)
        VALUES (?, ?, ?)
        """,
        (business_id, staff_ids[1], service_id),
    )
    day_of_week = (first_start.weekday() + 1) % 7
    connection.execute(
        """
        INSERT INTO business_working_hours (
            business_id, day_of_week, start_time, end_time
        ) VALUES (?, ?, ?, ?)
        """,
        (business_id, day_of_week, "09:00", "17:00"),
    )
    for staff_id in staff_ids:
        connection.execute(
            """
            INSERT INTO working_hours (staff_id, day_of_week, start_time, end_time)
            VALUES (?, ?, ?, ?)
            """,
            (staff_id, day_of_week, "09:00", "17:00"),
        )
    connection.commit()
    return business_id, customer_id, staff_ids, service_id, now, first_start, second_start


def build_service(connection, clock, *, business_id, service_id, staff_ids, starts):
    store = InMemoryAlternativeSelectionContextStore(now_provider=clock.now)
    context = store.create_search_context(
        business_id=business_id,
        requested_service_ids=[service_id],
        options=[
            AlternativeSelectionOption(
                option_id=f"option_{index:03d}",
                staff_id=staff_id,
                start_datetime=start,
            )
            for index, (staff_id, start) in enumerate(zip(staff_ids, starts), start=1)
        ],
    )
    service = AlternativeSelectionService(
        store,
        AppointmentService(connection, now_provider=clock.now),
    )
    return service, store, context


def test_selected_alternative_books_with_trusted_values_and_fresh_pricing(test_database):
    business_id, customer_id, staff_ids, service_id, now, first_start, _ = (
        setup_selection_booking(test_database)
    )
    clock = MutableClock(now)
    service, store, context = build_service(
        test_database,
        clock,
        business_id=business_id,
        service_id=service_id,
        staff_ids=[staff_ids[0]],
        starts=[first_start],
    )

    result = service.book_selected_alternative(
        search_id=context.search_id,
        option_id="option_001",
        customer_id=customer_id,
        notes="Preferred style",
    )

    assert result.staff_id == staff_ids[0]
    assert result.start_datetime == first_start.isoformat()
    assert result.total_duration_minutes == 60
    assert result.total_price == 100.0
    appointment = test_database.execute(
        "SELECT business_id, staff_id, notes FROM appointments WHERE id = ?",
        (result.appointment_id,),
    ).fetchone()
    assert dict(appointment) == {
        "business_id": business_id,
        "staff_id": staff_ids[0],
        "notes": "Preferred style",
    }
    with pytest.raises(AlternativeSelectionError, match="ALTERNATIVE_SEARCH_EXPIRED"):
        store.resolve_selection(context.search_id, "option_001")


def test_selection_inputs_cannot_override_trusted_booking_values():
    parameter_names = set(
        inspect.signature(
            AlternativeSelectionService.book_selected_alternative
        ).parameters
    )

    assert parameter_names == {
        "self",
        "search_id",
        "option_id",
        "customer_id",
        "notes",
        "confirmation",  # Comparison-only precondition; no trusted selection overrides.
    }


def test_selection_store_errors_remain_unchanged(test_database):
    business_id, customer_id, staff_ids, service_id, now, first_start, _ = (
        setup_selection_booking(test_database)
    )
    clock = MutableClock(now)
    service, store, context = build_service(
        test_database,
        clock,
        business_id=business_id,
        service_id=service_id,
        staff_ids=[staff_ids[0]],
        starts=[first_start],
    )

    with pytest.raises(AlternativeSelectionError, match="ALTERNATIVE_OPTION_NOT_FOUND"):
        service.book_selected_alternative(
            search_id=context.search_id,
            option_id="unknown",
            customer_id=customer_id,
        )
    store.expire_search_context(context.search_id)
    with pytest.raises(AlternativeSelectionError, match="ALTERNATIVE_SEARCH_EXPIRED"):
        service.book_selected_alternative(
            search_id=context.search_id,
            option_id="option_001",
            customer_id=customer_id,
        )


@pytest.mark.parametrize(
    "mutate",
    [
        "conflict",
        "past",
        "notice",
        "staff_inactive",
        "service_inactive",
    ],
)
def test_stale_selected_alternative_maps_to_single_error(
    test_database,
    mutate,
):
    business_id, customer_id, staff_ids, service_id, now, first_start, _ = (
        setup_selection_booking(test_database)
    )
    clock = MutableClock(now)
    selected_start = first_start
    if mutate == "past":
        selected_start = now + timedelta(minutes=10)
        day_of_week = (selected_start.weekday() + 1) % 7
        test_database.execute(
            """
            UPDATE business_working_hours SET start_time = ?
            WHERE business_id = ? AND day_of_week = ?
            """,
            ("08:00", business_id, day_of_week),
        )
        test_database.execute(
            "UPDATE working_hours SET start_time = ? WHERE staff_id = ?",
            ("08:00", staff_ids[0]),
        )
        test_database.commit()
    service, store, context = build_service(
        test_database,
        clock,
        business_id=business_id,
        service_id=service_id,
        staff_ids=[staff_ids[0]],
        starts=[selected_start],
    )

    if mutate == "conflict":
        test_database.execute(
            """
            INSERT INTO appointments (
                business_id, customer_id, staff_id, start_datetime, end_datetime, status
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                business_id,
                customer_id,
                staff_ids[0],
                first_start.isoformat(),
                (first_start + timedelta(minutes=60)).isoformat(),
                "BOOKED",
            ),
        )
        test_database.commit()
    elif mutate == "past":
        clock.value = now + timedelta(minutes=11)
    elif mutate == "notice":
        test_database.execute(
            """
            UPDATE business_settings
            SET minimum_booking_notice_minutes = ?
            WHERE business_id = ?
            """,
            (180, business_id),
        )
        test_database.commit()
    elif mutate == "staff_inactive":
        test_database.execute("UPDATE staff SET is_active = 0 WHERE id = ?", (staff_ids[0],))
        test_database.commit()
    elif mutate == "service_inactive":
        test_database.execute("UPDATE services SET is_active = 0 WHERE id = ?", (service_id,))
        test_database.commit()

    with pytest.raises(
        AlternativeSelectionError,
        match="SELECTED_ALTERNATIVE_NO_LONGER_AVAILABLE",
    ):
        service.book_selected_alternative(
            search_id=context.search_id,
            option_id="option_001",
            customer_id=customer_id,
        )

    assert store.resolve_selection(context.search_id, "option_001").staff_id == staff_ids[0]


def test_customer_error_remains_unchanged_and_context_is_retained(test_database):
    business_id, _, staff_ids, service_id, now, first_start, _ = setup_selection_booking(
        test_database
    )
    clock = MutableClock(now)
    service, store, context = build_service(
        test_database,
        clock,
        business_id=business_id,
        service_id=service_id,
        staff_ids=[staff_ids[0]],
        starts=[first_start],
    )

    with pytest.raises(AppointmentBookingError, match="CUSTOMER_NOT_FOUND"):
        service.book_selected_alternative(
            search_id=context.search_id,
            option_id="option_001",
            customer_id=999,
        )

    assert store.resolve_selection(context.search_id, "option_001").staff_id == staff_ids[0]


def test_failed_option_does_not_expire_context_and_another_option_can_book(test_database):
    business_id, customer_id, staff_ids, service_id, now, first_start, second_start = (
        setup_selection_booking(test_database)
    )
    clock = MutableClock(now)
    service, store, context = build_service(
        test_database,
        clock,
        business_id=business_id,
        service_id=service_id,
        staff_ids=staff_ids,
        starts=[first_start, second_start],
    )
    test_database.execute(
        """
        INSERT INTO appointments (
            business_id, customer_id, staff_id, start_datetime, end_datetime, status
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            business_id,
            customer_id,
            staff_ids[0],
            first_start.isoformat(),
            (first_start + timedelta(minutes=60)).isoformat(),
            "BOOKED",
        ),
    )
    test_database.commit()

    with pytest.raises(
        AlternativeSelectionError,
        match="SELECTED_ALTERNATIVE_NO_LONGER_AVAILABLE",
    ):
        service.book_selected_alternative(
            search_id=context.search_id,
            option_id="option_001",
            customer_id=customer_id,
        )

    count_after_failed_first_option = test_database.execute(
        "SELECT COUNT(*) AS count FROM appointments"
    ).fetchone()["count"]
    assert count_after_failed_first_option == 1

    result = service.book_selected_alternative(
        search_id=context.search_id,
        option_id="option_002",
        customer_id=customer_id,
    )

    assert result.staff_id == staff_ids[1]
    with pytest.raises(AlternativeSelectionError, match="ALTERNATIVE_SEARCH_EXPIRED"):
        store.resolve_selection(context.search_id, "option_001")


class BlockingAppointmentService:
    def __init__(self):
        self.calls = 0
        self.started = threading.Event()
        self.continue_booking = threading.Event()

    def book(self, **kwargs):
        self.calls += 1
        self.started.set()
        assert self.continue_booking.wait(timeout=1)
        return AppointmentBookingResult(
            appointment_id=1,
            staff_id=kwargs["staff_selection"]["staff_id"],
            start_datetime=kwargs["start_datetime"],
            end_datetime="2027-01-15T10:45:00",
            total_duration_minutes=45,
            total_price=80.0,
            items=[],
        )


def test_concurrent_options_from_one_search_cannot_both_reach_booking():
    now = datetime(2027, 1, 15, 8, 0)
    store = InMemoryAlternativeSelectionContextStore(now_provider=lambda: now)
    context = store.create_search_context(
        business_id=1,
        requested_service_ids=[4],
        options=[
            AlternativeSelectionOption("option_001", 10, datetime(2027, 1, 15, 10, 0)),
            AlternativeSelectionOption("option_002", 11, datetime(2027, 1, 15, 11, 0)),
        ],
    )
    appointment_service = BlockingAppointmentService()
    service = AlternativeSelectionService(store, appointment_service)
    first_errors = []

    def submit_first_option():
        try:
            service.book_selected_alternative(
                search_id=context.search_id,
                option_id="option_001",
                customer_id=20,
            )
        except BaseException as error:
            first_errors.append(error)

    first_thread = threading.Thread(target=submit_first_option)
    first_thread.start()
    assert appointment_service.started.wait(timeout=1)

    with pytest.raises(
        AlternativeSelectionError,
        match="ALTERNATIVE_SELECTION_IN_PROGRESS",
    ):
        service.book_selected_alternative(
            search_id=context.search_id,
            option_id="option_002",
            customer_id=20,
        )
    assert appointment_service.calls == 1

    appointment_service.continue_booking.set()
    first_thread.join(timeout=1)
    assert not first_thread.is_alive()
    assert first_errors == []


def test_separate_searches_can_execute_independently():
    now = datetime(2027, 1, 15, 8, 0)
    store = InMemoryAlternativeSelectionContextStore(now_provider=lambda: now)
    first = store.create_search_context(
        business_id=1,
        requested_service_ids=[4],
        options=[
            AlternativeSelectionOption("option_001", 10, datetime(2027, 1, 15, 10, 0))
        ],
    )
    second = store.create_search_context(
        business_id=2,
        requested_service_ids=[5],
        options=[
            AlternativeSelectionOption("option_001", 20, datetime(2027, 1, 15, 11, 0))
        ],
    )

    class ImmediateAppointmentService:
        def __init__(self):
            self.calls = []

        def book(self, **kwargs):
            self.calls.append(kwargs)
            return AppointmentBookingResult(
                appointment_id=len(self.calls),
                staff_id=kwargs["staff_selection"]["staff_id"],
                start_datetime=kwargs["start_datetime"],
                end_datetime="2027-01-15T10:45:00",
                total_duration_minutes=45,
                total_price=80.0,
                items=[],
            )

    appointment_service = ImmediateAppointmentService()
    service = AlternativeSelectionService(store, appointment_service)

    assert service.book_selected_alternative(
        search_id=first.search_id,
        option_id="option_001",
        customer_id=30,
    ).staff_id == 10
    assert service.book_selected_alternative(
        search_id=second.search_id,
        option_id="option_001",
        customer_id=31,
    ).staff_id == 20
    assert len(appointment_service.calls) == 2
