import sqlite3

import pytest

from app.db.repositories import (
    get_business_settings,
    get_business_working_hours,
    get_schedule_blocks,
    get_schedule_breaks,
    get_staff_appointments,
    get_staff_working_hours,
)


def create_business(test_database, name):
    cursor = test_database.execute(
        """
        INSERT INTO businesses (business_name)
        VALUES (?)
        """,
        (name,),
    )
    test_database.commit()
    return cursor.lastrowid


def create_staff(test_database, business_id, name, is_active=1):
    cursor = test_database.execute(
        """
        INSERT INTO staff (
            business_id,
            name,
            is_active
        )
        VALUES (?, ?, ?)
        """,
        (business_id, name, is_active),
    )
    test_database.commit()
    return cursor.lastrowid


def create_customer(test_database, business_id, name):
    cursor = test_database.execute(
        """
        INSERT INTO customers (
            business_id,
            name,
            phone,
            customer_type
        )
        VALUES (?, ?, ?, ?)
        """,
        (business_id, name, f"050{cursor_value(test_database)}", "GUEST"),
    )
    test_database.commit()
    return cursor.lastrowid


def cursor_value(test_database):
    row = test_database.execute(
        "SELECT COALESCE(MAX(id), 0) + 1 FROM customers"
    ).fetchone()
    return row[0]


# ---------------------------------------------------------------------------
# Business Working Hours
# ---------------------------------------------------------------------------


def test_get_business_working_hours_returns_correct_day(test_database):
    business_id = create_business(test_database, "Test Business")

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
        (business_id, 1, 1, "09:00", "20:00"),
    )
    test_database.commit()

    result = get_business_working_hours(test_database, business_id, 1)

    assert result == {
        "business_id": business_id,
        "day_of_week": 1,
        "is_working_day": 1,
        "start_time": "09:00",
        "end_time": "20:00",
    }


def test_get_business_working_hours_returns_none_for_missing_day(test_database):
    business_id = create_business(test_database, "Test Business")

    result = get_business_working_hours(test_database, business_id, 1)

    assert result is None


def test_get_business_working_hours_is_business_scoped(test_database):
    business_a = create_business(test_database, "Business A")
    business_b = create_business(test_database, "Business B")

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
        (business_a, 1, 1, "09:00", "20:00"),
    )
    test_database.commit()

    result = get_business_working_hours(test_database, business_b, 1)

    assert result is None


# ---------------------------------------------------------------------------
# Staff Working Hours
# ---------------------------------------------------------------------------


def test_get_staff_working_hours_returns_correct_staff_day(test_database):
    business_id = create_business(test_database, "Test Business")
    staff_id = create_staff(test_database, business_id, "Yossi")

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
        (staff_id, 1, 1, "10:00", "18:00"),
    )
    test_database.commit()

    result = get_staff_working_hours(test_database, staff_id, 1)

    assert result == {
        "staff_id": staff_id,
        "day_of_week": 1,
        "is_working_day": 1,
        "start_time": "10:00",
        "end_time": "18:00",
    }


def test_get_staff_working_hours_returns_none_for_missing_day(test_database):
    business_id = create_business(test_database, "Test Business")
    staff_id = create_staff(test_database, business_id, "Yossi")

    result = get_staff_working_hours(test_database, staff_id, 1)

    assert result is None


def test_get_staff_working_hours_is_staff_scoped(test_database):
    business_id = create_business(test_database, "Test Business")
    staff_a = create_staff(test_database, business_id, "Yossi")
    staff_b = create_staff(test_database, business_id, "David")

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
        (staff_a, 1, 1, "09:00", "17:00"),
    )
    test_database.commit()

    result = get_staff_working_hours(test_database, staff_b, 1)

    assert result is None


# ---------------------------------------------------------------------------
# Schedule Breaks
# ---------------------------------------------------------------------------


def test_get_schedule_breaks_returns_breaks_in_start_time_order(test_database):
    business_id = create_business(test_database, "Test Business")
    staff_id = create_staff(test_database, business_id, "Yossi")

    test_database.executemany(
        """
        INSERT INTO schedule_breaks (
            staff_id,
            day_of_week,
            start_time,
            end_time
        )
        VALUES (?, ?, ?, ?)
        """,
        [
            (staff_id, 1, "15:30", "16:00"),
            (staff_id, 1, "12:00", "12:30"),
        ],
    )
    test_database.commit()

    result = get_schedule_breaks(test_database, staff_id, 1)

    assert result == [
        {
            "staff_id": staff_id,
            "day_of_week": 1,
            "start_time": "12:00",
            "end_time": "12:30",
        },
        {
            "staff_id": staff_id,
            "day_of_week": 1,
            "start_time": "15:30",
            "end_time": "16:00",
        },
    ]


def test_get_schedule_breaks_returns_empty_list_when_no_breaks(test_database):
    business_id = create_business(test_database, "Test Business")
    staff_id = create_staff(test_database, business_id, "Yossi")

    result = get_schedule_breaks(test_database, staff_id, 1)

    assert result == []


def test_get_schedule_breaks_is_staff_scoped(test_database):
    business_id = create_business(test_database, "Test Business")
    staff_a = create_staff(test_database, business_id, "Yossi")
    staff_b = create_staff(test_database, business_id, "David")

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
        (staff_a, 1, "12:00", "12:30"),
    )
    test_database.commit()

    result = get_schedule_breaks(test_database, staff_b, 1)

    assert result == []


# ---------------------------------------------------------------------------
# Schedule Blocks
# ---------------------------------------------------------------------------


def test_get_schedule_blocks_returns_business_wide_block(test_database):
    business_id = create_business(test_database, "Test Business")
    staff_id = create_staff(test_database, business_id, "Yossi")

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
            None,
            "2026-09-10T14:00:00",
            "2026-09-10T16:00:00",
            "Holiday",
        ),
    )
    test_database.commit()

    result = get_schedule_blocks(
        test_database,
        business_id,
        staff_id,
        "2026-09-10T15:00:00",
        "2026-09-10T15:30:00",
    )

    assert len(result) == 1
    assert result[0]["staff_id"] is None
    assert result[0]["reason"] == "Holiday"


def test_get_schedule_blocks_returns_staff_specific_block(test_database):
    business_id = create_business(test_database, "Test Business")
    staff_id = create_staff(test_database, business_id, "Yossi")

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
            "2026-09-10T14:00:00",
            "2026-09-10T16:00:00",
            "Personal",
        ),
    )
    test_database.commit()

    result = get_schedule_blocks(
        test_database,
        business_id,
        staff_id,
        "2026-09-10T15:00:00",
        "2026-09-10T15:30:00",
    )

    assert len(result) == 1
    assert result[0]["staff_id"] == staff_id
    assert result[0]["reason"] == "Personal"


def test_get_schedule_blocks_excludes_other_staff_block(test_database):
    business_id = create_business(test_database, "Test Business")
    staff_a = create_staff(test_database, business_id, "Yossi")
    staff_b = create_staff(test_database, business_id, "David")

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
            staff_a,
            "2026-09-10T14:00:00",
            "2026-09-10T16:00:00",
            "Personal",
        ),
    )
    test_database.commit()

    result = get_schedule_blocks(
        test_database,
        business_id,
        staff_b,
        "2026-09-10T15:00:00",
        "2026-09-10T15:30:00",
    )

    assert result == []


def test_get_schedule_blocks_returns_overlapping_block(test_database):
    business_id = create_business(test_database, "Test Business")
    staff_id = create_staff(test_database, business_id, "Yossi")

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
            "2026-09-10T14:00:00",
            "2026-09-10T15:00:00",
            "Personal",
        ),
    )
    test_database.commit()

    result = get_schedule_blocks(
        test_database,
        business_id,
        staff_id,
        "2026-09-10T14:30:00",
        "2026-09-10T15:30:00",
    )

    assert len(result) == 1


def test_get_schedule_blocks_excludes_non_overlapping_block(test_database):
    business_id = create_business(test_database, "Test Business")
    staff_id = create_staff(test_database, business_id, "Yossi")

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
            "2026-09-10T14:00:00",
            "2026-09-10T15:00:00",
            "Personal",
        ),
    )
    test_database.commit()

    result = get_schedule_blocks(
        test_database,
        business_id,
        staff_id,
        "2026-09-10T15:00:00",
        "2026-09-10T16:00:00",
    )

    assert result == []


def test_get_schedule_blocks_is_business_scoped(test_database):
    business_a = create_business(test_database, "Business A")
    business_b = create_business(test_database, "Business B")
    staff_b = create_staff(test_database, business_b, "David")

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
            business_a,
            None,
            "2026-09-10T14:00:00",
            "2026-09-10T16:00:00",
            "Business A closure",
        ),
    )
    test_database.commit()

    result = get_schedule_blocks(
        test_database,
        business_b,
        staff_b,
        "2026-09-10T15:00:00",
        "2026-09-10T15:30:00",
    )

    assert result == []


# ---------------------------------------------------------------------------
# Staff Appointments
# ---------------------------------------------------------------------------


def test_get_staff_appointments_returns_overlapping_appointment(test_database):
    business_id = create_business(test_database, "Test Business")
    staff_id = create_staff(test_database, business_id, "Yossi")
    customer_id = create_customer(test_database, business_id, "Aminadav")

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
            "2026-09-10T14:00:00",
            "2026-09-10T15:00:00",
            "BOOKED",
        ),
    )
    test_database.commit()

    result = get_staff_appointments(
        test_database,
        business_id,
        staff_id,
        "2026-09-10T14:30:00",
        "2026-09-10T15:30:00",
    )

    assert len(result) == 1
    assert result[0]["staff_id"] == staff_id


def test_get_staff_appointments_excludes_non_overlapping_appointment(test_database):
    business_id = create_business(test_database, "Test Business")
    staff_id = create_staff(test_database, business_id, "Yossi")
    customer_id = create_customer(test_database, business_id, "Aminadav")

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
            "2026-09-10T14:00:00",
            "2026-09-10T15:00:00",
            "BOOKED",
        ),
    )
    test_database.commit()

    result = get_staff_appointments(
        test_database,
        business_id,
        staff_id,
        "2026-09-10T15:00:00",
        "2026-09-10T16:00:00",
    )

    assert result == []


def test_get_staff_appointments_excludes_cancelled_appointment(test_database):
    business_id = create_business(test_database, "Test Business")
    staff_id = create_staff(test_database, business_id, "Yossi")
    customer_id = create_customer(test_database, business_id, "Aminadav")

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
            "2026-09-10T14:00:00",
            "2026-09-10T15:00:00",
            "CANCELLED",
        ),
    )
    test_database.commit()

    result = get_staff_appointments(
        test_database,
        business_id,
        staff_id,
        "2026-09-10T14:30:00",
        "2026-09-10T15:30:00",
    )

    assert result == []


def test_get_staff_appointments_is_staff_scoped(test_database):
    business_id = create_business(test_database, "Test Business")
    staff_a = create_staff(test_database, business_id, "Yossi")
    staff_b = create_staff(test_database, business_id, "David")
    customer_id = create_customer(test_database, business_id, "Aminadav")

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
            staff_a,
            "2026-09-10T14:00:00",
            "2026-09-10T15:00:00",
            "BOOKED",
        ),
    )
    test_database.commit()

    result = get_staff_appointments(
        test_database,
        business_id,
        staff_b,
        "2026-09-10T14:30:00",
        "2026-09-10T15:30:00",
    )

    assert result == []


def test_get_staff_appointments_is_business_scoped(test_database):
    business_a = create_business(test_database, "Business A")
    business_b = create_business(test_database, "Business B")
    staff_a = create_staff(test_database, business_a, "Yossi")
    staff_b = create_staff(test_database, business_b, "David")
    customer_a = create_customer(test_database, business_a, "Customer A")

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
            business_a,
            customer_a,
            staff_a,
            "2026-09-10T14:00:00",
            "2026-09-10T15:00:00",
            "BOOKED",
        ),
    )
    test_database.commit()

    result = get_staff_appointments(
        test_database,
        business_b,
        staff_b,
        "2026-09-10T14:30:00",
        "2026-09-10T15:30:00",
    )

    assert result == []


# ---------------------------------------------------------------------------
# Business Settings
# ---------------------------------------------------------------------------


def test_get_business_settings_returns_correct_settings(test_database):
    business_id = create_business(test_database, "Test Business")

    test_database.execute(
        """
        INSERT INTO business_settings (
            business_id,
            booking_window_months,
            cancellation_cutoff_hours,
            allow_guest_booking,
            allow_customer_reschedule,
            allow_customer_cancel
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (business_id, 3, 4, 1, 1, 1),
    )
    test_database.commit()

    result = get_business_settings(test_database, business_id)

    assert result == {
        "business_id": business_id,
        "booking_window_months": 3,
        "minimum_booking_notice_minutes": 0,
        "alternative_search_window_days": 7,
        "booking_interval_minutes": 30,
        "cancellation_cutoff_hours": 4,
        "allow_guest_booking": 1,
        "allow_customer_reschedule": 1,
        "allow_customer_cancel": 1,
    }


def test_get_business_settings_is_business_scoped(test_database):
    business_a = create_business(test_database, "Business A")
    business_b = create_business(test_database, "Business B")

    test_database.execute(
        """
        INSERT INTO business_settings (
            business_id,
            booking_window_months,
            cancellation_cutoff_hours,
            allow_guest_booking,
            allow_customer_reschedule,
            allow_customer_cancel
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (business_a, 3, 4, 1, 1, 1),
    )
    test_database.commit()

    result = get_business_settings(test_database, business_b)

    assert result is None


def test_get_business_settings_returns_alternative_search_window_days(
    test_database,
):
    business_id = create_business(test_database, "Test Business")
    test_database.execute(
        """
        INSERT INTO business_settings (
            business_id,
            alternative_search_window_days
        )
        VALUES (?, ?)
        """,
        (business_id, 14),
    )
    test_database.commit()

    result = get_business_settings(test_database, business_id)

    assert result["alternative_search_window_days"] == 14


def test_get_business_settings_returns_booking_interval_minutes(test_database):
    business_id = create_business(test_database, "Test Business")
    test_database.execute(
        """
        INSERT INTO business_settings (
            business_id,
            booking_interval_minutes
        )
        VALUES (?, ?)
        """,
        (business_id, 15),
    )
    test_database.commit()

    result = get_business_settings(test_database, business_id)

    assert result["booking_interval_minutes"] == 15


def test_get_business_settings_returns_none_when_missing(test_database):
    business_id = create_business(test_database, "Test Business")

    result = get_business_settings(test_database, business_id)

    assert result is None


@pytest.mark.parametrize(
    (
        "booking_window_months",
        "minimum_booking_notice_minutes",
        "alternative_search_window_days",
        "booking_interval_minutes",
    ),
    [
        (0, 0, 7, 30),
        (3, -1, 7, 30),
        (3, 0, 0, 30),
        (3, 0, 7, 0),
    ],
)
def test_business_settings_reject_invalid_rule_values(
    test_database,
    booking_window_months,
    minimum_booking_notice_minutes,
    alternative_search_window_days,
    booking_interval_minutes,
):
    business_id = create_business(test_database, "Test Business")

    with pytest.raises(sqlite3.IntegrityError):
        test_database.execute(
            """
            INSERT INTO business_settings (
                business_id,
                booking_window_months,
                minimum_booking_notice_minutes,
                alternative_search_window_days,
                booking_interval_minutes
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                business_id,
                booking_window_months,
                minimum_booking_notice_minutes,
                alternative_search_window_days,
                booking_interval_minutes,
            ),
        )
