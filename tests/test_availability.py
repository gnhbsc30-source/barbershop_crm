from datetime import date, datetime, timedelta

from app.services.availability import AvailabilityEngine


def future_monday_at(time: str = "00:00:00") -> datetime:
    """Return the next Monday at the requested time, always after today."""
    days_until_monday = 7 - date.today().weekday()
    return datetime.combine(
        date.today() + timedelta(days=days_until_monday),
        datetime.strptime(time, "%H:%M:%S").time(),
    )


def create_business(test_database, business_id=None):
    if business_id is None:
        test_database.execute(
            """
            INSERT INTO businesses (
                business_name
            )
            VALUES (?)
            """,
            ("Test Barbershop",),
        )
    else:
        test_database.execute(
            """
            INSERT INTO businesses (
                id,
                business_name
            )
            VALUES (?, ?)
            """,
            (
                business_id,
                "Test Barbershop",
            ),
        )

    business = test_database.execute(
        """
        SELECT id
        FROM businesses
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()

    business_id = business["id"]

    test_database.execute(
        """
        INSERT INTO business_settings (
            business_id
        )
        VALUES (?)
        """,
        (business_id,),
    )

    return business_id


def create_staff(
    test_database,
    business_id,
    name="Test Barber",
):
    test_database.execute(
        """
        INSERT INTO staff (
            business_id,
            name
        )
        VALUES (?, ?)
        """,
        (
            business_id,
            name,
        ),
    )

    return test_database.execute(
        """
        SELECT id
        FROM staff
        WHERE business_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (business_id,),
    ).fetchone()["id"]


def create_service(
    test_database,
    business_id,
    name="Haircut",
    duration=45,
    price=80.0,
):
    test_database.execute(
        """
        INSERT INTO services (
            business_id,
            name,
            default_duration_minutes,
            default_price
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            business_id,
            name,
            duration,
            price,
        ),
    )

    return test_database.execute(
        """
        SELECT id
        FROM services
        WHERE business_id = ?
          AND name = ?
        """,
        (
            business_id,
            name,
        ),
    ).fetchone()["id"]


def connect_staff_to_service(
    test_database,
    business_id,
    staff_id,
    service_id,
    duration=None,
):
    test_database.execute(
        """
        INSERT INTO staff_services (
            business_id,
            staff_id,
            service_id,
            duration_minutes
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            business_id,
            staff_id,
            service_id,
            duration,
        ),
    )


def create_business_hours(
    test_database,
    business_id,
    day_of_week=1,
    start_time="09:00",
    end_time="17:00",
):
    test_database.execute(
        """
        INSERT INTO business_working_hours (
            business_id,
            day_of_week,
            is_working_day,
            start_time,
            end_time
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            business_id,
            day_of_week,
            1,
            start_time,
            end_time,
        ),
    )


def create_staff_hours(
    test_database,
    staff_id,
    day_of_week=1,
    start_time="09:00",
    end_time="17:00",
):
    test_database.execute(
        """
        INSERT INTO working_hours (
            staff_id,
            day_of_week,
            is_working_day,
            start_time,
            end_time
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            staff_id,
            day_of_week,
            1,
            start_time,
            end_time,
        ),
    )


def create_break(
    test_database,
    staff_id,
    day_of_week=1,
    start_time="12:00",
    end_time="13:00",
):
    test_database.execute(
        """
        INSERT INTO schedule_breaks (
            staff_id,
            day_of_week,
            start_time,
            end_time
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            staff_id,
            day_of_week,
            start_time,
            end_time,
        ),
    )


def create_schedule_block(
    test_database,
    business_id,
    staff_id,
    start_datetime,
    end_datetime,
    reason="Test block",
):
    test_database.execute(
        """
        INSERT INTO schedule_blocks (
            business_id,
            staff_id,
            start_datetime,
            end_datetime,
            reason
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            business_id,
            staff_id,
            start_datetime,
            end_datetime,
            reason,
        ),
    )


def create_customer(
    test_database,
    business_id,
    name="Test Customer",
):
    test_database.execute(
        """
        INSERT INTO customers (
            business_id,
            name,
            customer_type
        )
        VALUES (?, ?, ?)
        """,
        (
            business_id,
            name,
            "GUEST",
        ),
    )

    return test_database.execute(
        """
        SELECT id
        FROM customers
        WHERE business_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (business_id,),
    ).fetchone()["id"]


def create_appointment(
    test_database,
    business_id,
    customer_id,
    staff_id,
    start_datetime,
    end_datetime,
    status="BOOKED",
):
    test_database.execute(
        """
        INSERT INTO appointments (
            business_id,
            customer_id,
            staff_id,
            start_datetime,
            end_datetime,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            business_id,
            customer_id,
            staff_id,
            start_datetime,
            end_datetime,
            status,
        ),
    )


def setup_basic_schedule(test_database):
    business_id = create_business(test_database)

    staff_id = create_staff(
        test_database,
        business_id,
    )

    service_id = create_service(
        test_database,
        business_id,
    )

    connect_staff_to_service(
        test_database,
        business_id,
        staff_id,
        service_id,
    )

    # The test helper always returns a future Monday.
    # Schema convention: Sunday=0 ... Saturday=6.
    create_business_hours(
        test_database,
        business_id,
        day_of_week=1,
    )

    create_staff_hours(
        test_database,
        staff_id,
        day_of_week=1,
    )

    test_database.commit()

    return business_id, staff_id, service_id


def test_available_appointment_uses_staff_service_duration(
    test_database,
):
    business_id, staff_id, service_id = setup_basic_schedule(
        test_database
    )

    engine = AvailabilityEngine(test_database)

    result = engine.check(
        business_id=business_id,
        service_ids=[service_id],
        start_datetime=future_monday_at("10:00:00").isoformat(),
        staff_selection={
            "type": "SPECIFIC",
            "staff_id": staff_id,
        },
    )

    assert result.available is True
    assert result.staff_id == staff_id
    assert result.start_datetime == future_monday_at("10:00:00").isoformat()
    assert result.end_datetime == future_monday_at("10:45:00").isoformat()


def test_staff_duration_override_is_used(
    test_database,
):
    business_id, staff_id, service_id = setup_basic_schedule(
        test_database
    )

    test_database.execute(
        """
        UPDATE staff_services
        SET duration_minutes = ?
        WHERE business_id = ?
          AND staff_id = ?
          AND service_id = ?
        """,
        (
            60,
            business_id,
            staff_id,
            service_id,
        ),
    )

    test_database.commit()

    engine = AvailabilityEngine(test_database)

    result = engine.check(
        business_id=business_id,
        service_ids=[service_id],
        start_datetime=future_monday_at("10:00:00").isoformat(),
        staff_selection={
            "type": "SPECIFIC",
            "staff_id": staff_id,
        },
    )

    assert result.available is True
    assert result.end_datetime == future_monday_at("11:00:00").isoformat()


def test_service_must_fit_inside_staff_and_business_hours(
    test_database,
):
    business_id, staff_id, service_id = setup_basic_schedule(
        test_database
    )

    engine = AvailabilityEngine(test_database)

    result = engine.check(
        business_id=business_id,
        service_ids=[service_id],
        start_datetime=future_monday_at("16:30:00").isoformat(),
        staff_selection={
            "type": "SPECIFIC",
            "staff_id": staff_id,
        },
    )

    assert result.available is False
    assert result.reason == "NO_STAFF_AVAILABLE"


def test_closed_business_day_is_unavailable(
    test_database,
):
    business_id, staff_id, service_id = setup_basic_schedule(
        test_database
    )

    test_database.execute(
        """
        UPDATE business_working_hours
        SET is_working_day = 0,
            start_time = NULL,
            end_time = NULL
        WHERE business_id = ?
          AND day_of_week = ?
        """,
        (
            business_id,
            1,
        ),
    )

    test_database.commit()

    engine = AvailabilityEngine(test_database)

    result = engine.check(
        business_id=business_id,
        service_ids=[service_id],
        start_datetime=future_monday_at("10:00:00").isoformat(),
        staff_selection={
            "type": "SPECIFIC",
            "staff_id": staff_id,
        },
    )

    assert result.available is False
    assert result.reason == "BUSINESS_CLOSED"


def test_staff_not_working_is_unavailable(
    test_database,
):
    business_id, staff_id, service_id = setup_basic_schedule(
        test_database
    )

    test_database.execute(
        """
        UPDATE working_hours
        SET is_working_day = 0,
            start_time = NULL,
            end_time = NULL
        WHERE staff_id = ?
          AND day_of_week = ?
        """,
        (
            staff_id,
            1,
        ),
    )

    test_database.commit()

    engine = AvailabilityEngine(test_database)

    result = engine.check(
        business_id=business_id,
        service_ids=[service_id],
        start_datetime=future_monday_at("10:00:00").isoformat(),
        staff_selection={
            "type": "SPECIFIC",
            "staff_id": staff_id,
        },
    )

    assert result.available is False
    assert result.reason == "STAFF_NOT_WORKING"


def test_staff_break_blocks_appointment(
    test_database,
):
    business_id, staff_id, service_id = setup_basic_schedule(
        test_database
    )

    create_break(
        test_database,
        staff_id,
        day_of_week=1,
        start_time="12:00",
        end_time="13:00",
    )

    test_database.commit()

    engine = AvailabilityEngine(test_database)

    result = engine.check(
        business_id=business_id,
        service_ids=[service_id],
        start_datetime=future_monday_at("12:00:00").isoformat(),
        staff_selection={
            "type": "SPECIFIC",
            "staff_id": staff_id,
        },
    )

    assert result.available is False
    assert result.reason == "NO_STAFF_AVAILABLE"


def test_staff_schedule_block_blocks_appointment(
    test_database,
):
    business_id, staff_id, service_id = setup_basic_schedule(
        test_database
    )

    create_schedule_block(
        test_database,
        business_id=business_id,
        staff_id=staff_id,
        start_datetime=future_monday_at("14:00:00").isoformat(),
        end_datetime=future_monday_at("15:00:00").isoformat(),
    )

    test_database.commit()

    engine = AvailabilityEngine(test_database)

    result = engine.check(
        business_id=business_id,
        service_ids=[service_id],
        start_datetime=future_monday_at("14:00:00").isoformat(),
        staff_selection={
            "type": "SPECIFIC",
            "staff_id": staff_id,
        },
    )

    assert result.available is False
    assert result.reason == "NO_STAFF_AVAILABLE"


def test_business_wide_block_blocks_staff(
    test_database,
):
    business_id, staff_id, service_id = setup_basic_schedule(
        test_database
    )

    create_schedule_block(
        test_database,
        business_id=business_id,
        staff_id=None,
        start_datetime=future_monday_at("14:00:00").isoformat(),
        end_datetime=future_monday_at("15:00:00").isoformat(),
    )

    test_database.commit()

    engine = AvailabilityEngine(test_database)

    result = engine.check(
        business_id=business_id,
        service_ids=[service_id],
        start_datetime=future_monday_at("14:00:00").isoformat(),
        staff_selection={
            "type": "SPECIFIC",
            "staff_id": staff_id,
        },
    )

    assert result.available is False
    assert result.reason == "NO_STAFF_AVAILABLE"


def test_overlapping_existing_appointment_blocks_time(
    test_database,
):
    business_id, staff_id, service_id = setup_basic_schedule(
        test_database
    )

    customer_id = create_customer(
        test_database,
        business_id,
    )

    create_appointment(
        test_database,
        business_id=business_id,
        customer_id=customer_id,
        staff_id=staff_id,
        start_datetime=future_monday_at("10:00:00").isoformat(),
        end_datetime=future_monday_at("10:45:00").isoformat(),
    )

    test_database.commit()

    engine = AvailabilityEngine(test_database)

    result = engine.check(
        business_id=business_id,
        service_ids=[service_id],
        start_datetime=future_monday_at("10:15:00").isoformat(),
        staff_selection={
            "type": "SPECIFIC",
            "staff_id": staff_id,
        },
    )

    assert result.available is False
    assert result.reason == "NO_STAFF_AVAILABLE"


def test_cancelled_appointment_does_not_block_time(
    test_database,
):
    business_id, staff_id, service_id = setup_basic_schedule(
        test_database
    )

    customer_id = create_customer(
        test_database,
        business_id,
    )

    create_appointment(
        test_database,
        business_id=business_id,
        customer_id=customer_id,
        staff_id=staff_id,
        start_datetime=future_monday_at("10:00:00").isoformat(),
        end_datetime=future_monday_at("10:45:00").isoformat(),
        status="CANCELLED",
    )

    test_database.commit()

    engine = AvailabilityEngine(test_database)

    result = engine.check(
        business_id=business_id,
        service_ids=[service_id],
        start_datetime=future_monday_at("10:00:00").isoformat(),
        staff_selection={
            "type": "SPECIFIC",
            "staff_id": staff_id,
        },
    )

    assert result.available is True
    assert result.end_datetime == future_monday_at("10:45:00").isoformat()


def test_service_not_supported_by_specific_staff(
    test_database,
):
    business_id = create_business(test_database)

    staff_id = create_staff(
        test_database,
        business_id,
    )

    service_id = create_service(
        test_database,
        business_id,
        name="Beard",
        duration=30,
    )

    create_business_hours(
        test_database,
        business_id,
        day_of_week=1,
    )

    create_staff_hours(
        test_database,
        staff_id,
        day_of_week=1,
    )

    test_database.commit()

    engine = AvailabilityEngine(test_database)

    result = engine.check(
        business_id=business_id,
        service_ids=[service_id],
        start_datetime=future_monday_at("10:00:00").isoformat(),
        staff_selection={
            "type": "SPECIFIC",
            "staff_id": staff_id,
        },
    )

    assert result.available is False
    assert result.reason == "SERVICE_NOT_SUPPORTED"


def test_multiple_services_use_total_duration(
    test_database,
):
    business_id, staff_id, haircut_id = setup_basic_schedule(
        test_database
    )

    beard_id = create_service(
        test_database,
        business_id,
        name="Beard",
        duration=30,
        price=30.0,
    )

    connect_staff_to_service(
        test_database,
        business_id,
        staff_id,
        beard_id,
    )

    test_database.commit()

    engine = AvailabilityEngine(test_database)

    result = engine.check(
        business_id=business_id,
        service_ids=[haircut_id, beard_id],
        start_datetime=future_monday_at("10:00:00").isoformat(),
        staff_selection={
            "type": "SPECIFIC",
            "staff_id": staff_id,
        },
    )

    assert result.available is True
    assert result.start_datetime == future_monday_at("10:00:00").isoformat()
    assert result.end_datetime == future_monday_at("11:15:00").isoformat()


def test_any_auto_selects_available_staff(
    test_database,
):
    business_id, first_staff_id, service_id = setup_basic_schedule(
        test_database
    )

    second_staff_id = create_staff(
        test_database,
        business_id,
        name="Second Barber",
    )

    create_staff_hours(
        test_database,
        second_staff_id,
        day_of_week=1,
    )

    connect_staff_to_service(
        test_database,
        business_id,
        second_staff_id,
        service_id,
    )

    customer_id = create_customer(
        test_database,
        business_id,
    )

    create_appointment(
        test_database,
        business_id=business_id,
        customer_id=customer_id,
        staff_id=first_staff_id,
        start_datetime=future_monday_at("10:00:00").isoformat(),
        end_datetime=future_monday_at("10:45:00").isoformat(),
    )

    test_database.commit()

    engine = AvailabilityEngine(test_database)

    result = engine.check(
        business_id=business_id,
        service_ids=[service_id],
        start_datetime=future_monday_at("10:00:00").isoformat(),
        staff_selection={
            "type": "ANY",
            "mode": "AUTO",
        },
    )

    assert result.available is True
    assert result.staff_id == second_staff_id


def test_any_returns_unavailable_when_no_staff_can_fit(
    test_database,
):
    business_id, staff_id, service_id = setup_basic_schedule(
        test_database
    )

    customer_id = create_customer(
        test_database,
        business_id,
    )

    create_appointment(
        test_database,
        business_id=business_id,
        customer_id=customer_id,
        staff_id=staff_id,
        start_datetime=future_monday_at("10:00:00").isoformat(),
        end_datetime=future_monday_at("10:45:00").isoformat(),
    )

    test_database.commit()

    engine = AvailabilityEngine(test_database)

    result = engine.check(
        business_id=business_id,
        service_ids=[service_id],
        start_datetime=future_monday_at("10:15:00").isoformat(),
        staff_selection={
            "type": "ANY",
            "mode": "AUTO",
        },
    )

    assert result.available is False
    assert result.reason == "NO_STAFF_AVAILABLE"


def test_any_choice_finds_available_staff(
    test_database,
):
    business_id, first_staff_id, service_id = setup_basic_schedule(
        test_database
    )

    second_staff_id = create_staff(
        test_database,
        business_id,
        name="Second Barber",
    )

    create_staff_hours(
        test_database,
        second_staff_id,
        day_of_week=1,
    )

    connect_staff_to_service(
        test_database,
        business_id,
        second_staff_id,
        service_id,
    )

    customer_id = create_customer(
        test_database,
        business_id,
    )

    create_appointment(
        test_database,
        business_id=business_id,
        customer_id=customer_id,
        staff_id=first_staff_id,
        start_datetime=future_monday_at("10:00:00").isoformat(),
        end_datetime=future_monday_at("10:45:00").isoformat(),
    )

    test_database.commit()

    engine = AvailabilityEngine(test_database)

    result = engine.check(
        business_id=business_id,
        service_ids=[service_id],
        start_datetime=future_monday_at("10:00:00").isoformat(),
        staff_selection={
            "type": "ANY",
            "mode": "CHOICE",
        },
    )

    assert result.available is True
    assert result.staff_id == second_staff_id


def test_no_eligible_staff(
    test_database,
):
    business_id = create_business(test_database)

    staff_id = create_staff(
        test_database,
        business_id,
    )

    service_id = create_service(
        test_database,
        business_id,
    )

    create_business_hours(
        test_database,
        business_id,
        day_of_week=1,
    )

    create_staff_hours(
        test_database,
        staff_id,
        day_of_week=1,
    )

    test_database.commit()

    engine = AvailabilityEngine(test_database)

    result = engine.check(
        business_id=business_id,
        service_ids=[service_id],
        start_datetime=future_monday_at("10:00:00").isoformat(),
        staff_selection={
            "type": "ANY",
            "mode": "AUTO",
        },
    )

    assert result.available is False
    assert result.reason == "NO_ELIGIBLE_STAFF"


def test_unknown_service_is_rejected(
    test_database,
):
    business_id, staff_id, _ = setup_basic_schedule(
        test_database
    )

    engine = AvailabilityEngine(test_database)

    result = engine.check(
        business_id=business_id,
        service_ids=[999999],
        start_datetime=future_monday_at("10:00:00").isoformat(),
        staff_selection={
            "type": "SPECIFIC",
            "staff_id": staff_id,
        },
    )

    assert result.available is False
    assert result.reason == "SERVICE_NOT_FOUND"


def test_invalid_datetime_is_rejected(
    test_database,
):
    business_id, staff_id, service_id = setup_basic_schedule(
        test_database
    )

    engine = AvailabilityEngine(test_database)

    result = engine.check(
        business_id=business_id,
        service_ids=[service_id],
        start_datetime="not-a-datetime",
        staff_selection={
            "type": "SPECIFIC",
            "staff_id": staff_id,
        },
    )

    assert result.available is False
    assert result.reason == "INVALID_DATETIME"


def test_past_datetime_is_rejected(
    test_database,
):
    business_id, staff_id, service_id = setup_basic_schedule(
        test_database
    )

    engine = AvailabilityEngine(test_database)

    result = engine.check(
        business_id=business_id,
        service_ids=[service_id],
        start_datetime="2020-01-01T10:00:00",
        staff_selection={
            "type": "SPECIFIC",
            "staff_id": staff_id,
        },
    )

    assert result.available is False
    assert result.reason == "PAST_DATETIME"


def test_empty_service_list_is_rejected(
    test_database,
):
    business_id, staff_id, _ = setup_basic_schedule(
        test_database
    )

    engine = AvailabilityEngine(test_database)

    result = engine.check(
        business_id=business_id,
        service_ids=[],
        start_datetime=future_monday_at("10:00:00").isoformat(),
        staff_selection={
            "type": "SPECIFIC",
            "staff_id": staff_id,
        },
    )

    assert result.available is False
    assert result.reason == "NO_SERVICES"


def test_invalid_staff_selection_is_rejected(
    test_database,
):
    business_id, _, service_id = setup_basic_schedule(
        test_database
    )

    engine = AvailabilityEngine(test_database)

    result = engine.check(
        business_id=business_id,
        service_ids=[service_id],
        start_datetime=future_monday_at("10:00:00").isoformat(),
        staff_selection={
            "type": "INVALID",
        },
    )

    assert result.available is False
    assert result.reason == "INVALID_STAFF_SELECTION"


def test_real_free_window_starts_after_existing_appointment(
    test_database,
):
    business_id, staff_id, service_id = setup_basic_schedule(
        test_database
    )

    customer_id = create_customer(
        test_database,
        business_id,
    )

    create_appointment(
        test_database,
        business_id=business_id,
        customer_id=customer_id,
        staff_id=staff_id,
        start_datetime=future_monday_at("09:00:00").isoformat(),
        end_datetime=future_monday_at("09:45:00").isoformat(),
    )

    test_database.commit()

    engine = AvailabilityEngine(test_database)

    windows = engine._build_staff_availability_windows(
        business_id=business_id,
        staff_id=staff_id,
        target_date=future_monday_at().date(),
    )

    assert windows == [
        (
            future_monday_at("09:45:00"),
            future_monday_at("17:00:00"),
        )
    ]


def test_availability_does_not_use_a_fixed_time_grid(
    test_database,
):
    """
    Availability is based on real working/free time.

    The engine must accept a legal start time that does not belong
    to a fixed booking grid when the service fits completely.
    """

    business_id, staff_id, service_id = setup_basic_schedule(
        test_database
    )

    engine = AvailabilityEngine(test_database)

    result = engine.check(
        business_id=business_id,
        service_ids=[service_id],
        start_datetime=future_monday_at("09:17:00").isoformat(),
        staff_selection={
            "type": "SPECIFIC",
            "staff_id": staff_id,
        },
    )

    assert result.available is True
    assert result.start_datetime == future_monday_at("09:17:00").isoformat()
    assert result.end_datetime == future_monday_at("10:02:00").isoformat()
