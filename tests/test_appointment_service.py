from datetime import datetime, timedelta

import pytest

from app.services.appointments import AppointmentBookingError, AppointmentService


def setup_bookable_request(connection):
    connection.execute("INSERT INTO businesses (business_name) VALUES (?)", ("Shop",))
    business_id = connection.execute("SELECT id FROM businesses").fetchone()["id"]
    connection.execute(
        "INSERT INTO customers (business_id, name, customer_type) VALUES (?, ?, ?)",
        (business_id, "Dana", "GUEST"),
    )
    customer_id = connection.execute("SELECT id FROM customers").fetchone()["id"]
    connection.execute(
        "INSERT INTO staff (business_id, name) VALUES (?, ?)",
        (business_id, "Noa"),
    )
    staff_id = connection.execute("SELECT id FROM staff").fetchone()["id"]

    appointment_start = (datetime.now().replace(microsecond=0) + timedelta(days=8)).replace(
        hour=10, minute=0, second=0
    )
    schema_day = (appointment_start.weekday() + 1) % 7
    connection.execute(
        """
        INSERT INTO business_working_hours (business_id, day_of_week, start_time, end_time)
        VALUES (?, ?, ?, ?)
        """,
        (business_id, schema_day, "09:00", "18:00"),
    )
    connection.execute(
        """
        INSERT INTO working_hours (staff_id, day_of_week, start_time, end_time)
        VALUES (?, ?, ?, ?)
        """,
        (staff_id, schema_day, "09:00", "18:00"),
    )

    return business_id, customer_id, staff_id, appointment_start


def add_service(connection, business_id, staff_id, name, duration, price, *, override_duration=None, override_price=None):
    connection.execute(
        """
        INSERT INTO services (business_id, name, default_duration_minutes, default_price)
        VALUES (?, ?, ?, ?)
        """,
        (business_id, name, duration, price),
    )
    service_id = connection.execute("SELECT id FROM services WHERE name = ?", (name,)).fetchone()["id"]
    connection.execute(
        """
        INSERT INTO staff_services (business_id, staff_id, service_id, duration_minutes, price)
        VALUES (?, ?, ?, ?, ?)
        """,
        (business_id, staff_id, service_id, override_duration, override_price),
    )
    return service_id


def test_resolve_uses_each_staff_override_or_service_default(test_database):
    business_id, customer_id, staff_id, appointment_start = setup_bookable_request(test_database)
    haircut_id = add_service(test_database, business_id, staff_id, "Haircut", 45, 80.0, override_duration=60)
    beard_id = add_service(test_database, business_id, staff_id, "Beard", 20, 40.0, override_price=45.0)

    start, items = AppointmentService(test_database).resolve(
        business_id=business_id,
        customer_id=customer_id,
        staff_id=staff_id,
        start_datetime=appointment_start.isoformat(),
        service_ids=[haircut_id, beard_id],
    )

    assert start == appointment_start
    assert [(item.duration_minutes, item.price) for item in items] == [(60, 80.0), (20, 45.0)]
    assert AppointmentService.totals(items) == (80, 125.0)


def test_resolve_rejects_customer_from_another_business(test_database):
    business_id, _, staff_id, appointment_start = setup_bookable_request(test_database)
    service_id = add_service(test_database, business_id, staff_id, "Haircut", 45, 80.0)
    test_database.execute("INSERT INTO businesses (business_name) VALUES (?)", ("Other shop",))
    other_business_id = test_database.execute("SELECT MAX(id) AS id FROM businesses").fetchone()["id"]
    test_database.execute(
        "INSERT INTO customers (business_id, name, customer_type) VALUES (?, ?, ?)",
        (other_business_id, "Other customer", "GUEST"),
    )
    other_customer_id = test_database.execute("SELECT MAX(id) AS id FROM customers").fetchone()["id"]

    with pytest.raises(AppointmentBookingError, match="CUSTOMER_NOT_FOUND"):
        AppointmentService(test_database).resolve(
            business_id=business_id,
            customer_id=other_customer_id,
            staff_id=staff_id,
            start_datetime=appointment_start.isoformat(),
            service_ids=[service_id],
        )


def test_resolve_rejects_service_not_supported_by_staff(test_database):
    business_id, customer_id, staff_id, appointment_start = setup_bookable_request(test_database)
    test_database.execute(
        """
        INSERT INTO services (business_id, name, default_duration_minutes, default_price)
        VALUES (?, ?, ?, ?)
        """,
        (business_id, "Haircut", 45, 80.0),
    )
    service_id = test_database.execute("SELECT id FROM services").fetchone()["id"]

    with pytest.raises(AppointmentBookingError, match="SERVICE_NOT_SUPPORTED"):
        AppointmentService(test_database).resolve(
            business_id=business_id,
            customer_id=customer_id,
            staff_id=staff_id,
            start_datetime=appointment_start.isoformat(),
            service_ids=[service_id],
        )


def test_book_creates_appointment_and_historical_items(test_database):
    business_id, customer_id, staff_id, appointment_start = setup_bookable_request(test_database)
    haircut_id = add_service(test_database, business_id, staff_id, "Haircut", 45, 80.0, override_duration=60)
    beard_id = add_service(test_database, business_id, staff_id, "Beard", 20, 40.0, override_price=45.0)
    test_database.commit()

    result = AppointmentService(test_database).book(
        business_id=business_id,
        customer_id=customer_id,
        start_datetime=appointment_start.isoformat(),
        service_ids=[haircut_id, beard_id],
        staff_selection={"type": "SPECIFIC", "staff_id": staff_id},
        notes="Regular visit",
    )

    assert result.total_duration_minutes == 80
    assert result.total_price == 125.0
    assert result.end_datetime == (appointment_start + timedelta(minutes=80)).isoformat()
    appointment = test_database.execute("SELECT * FROM appointments WHERE id = ?", (result.appointment_id,)).fetchone()
    items = test_database.execute(
        "SELECT service_name, duration_minutes, price FROM appointment_items WHERE appointment_id = ? ORDER BY id",
        (result.appointment_id,),
    ).fetchall()
    assert appointment["notes"] == "Regular visit"
    assert [(item["service_name"], item["duration_minutes"], item["price"]) for item in items] == [
        ("Haircut", 60, 80.0),
        ("Beard", 20, 45.0),
    ]


@pytest.mark.parametrize(
    ("staff_selection", "allow_auto_assign", "error_code"),
    [
        ({"type": "ANY", "mode": "CHOICE"}, False, "STAFF_SELECTION_REQUIRES_CHOICE"),
        ({"type": "ANY", "mode": "AUTO"}, False, "AUTO_ASSIGNMENT_NOT_AUTHORIZED"),
    ],
)
def test_book_does_not_create_for_unresolved_staff_selection(
    test_database, staff_selection, allow_auto_assign, error_code
):
    business_id, customer_id, staff_id, appointment_start = setup_bookable_request(test_database)
    service_id = add_service(test_database, business_id, staff_id, "Haircut", 45, 80.0)
    test_database.commit()

    with pytest.raises(AppointmentBookingError, match=error_code):
        AppointmentService(test_database).book(
            business_id=business_id,
            customer_id=customer_id,
            start_datetime=appointment_start.isoformat(),
            service_ids=[service_id],
            staff_selection=staff_selection,
            allow_auto_assign=allow_auto_assign,
        )

    count = test_database.execute("SELECT COUNT(*) AS count FROM appointments").fetchone()["count"]
    assert count == 0


def test_book_allows_any_auto_only_when_explicitly_authorized(test_database):
    business_id, customer_id, staff_id, appointment_start = setup_bookable_request(test_database)
    service_id = add_service(test_database, business_id, staff_id, "Haircut", 45, 80.0)
    test_database.commit()

    result = AppointmentService(test_database).book(
        business_id=business_id,
        customer_id=customer_id,
        start_datetime=appointment_start.isoformat(),
        service_ids=[service_id],
        staff_selection={"type": "ANY", "mode": "AUTO"},
        allow_auto_assign=True,
    )

    assert result.staff_id == staff_id
