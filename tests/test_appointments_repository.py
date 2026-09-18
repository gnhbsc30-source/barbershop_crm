import pytest

from app.db.database import transaction
from app.db.repositories import create_appointment


def test_create_appointment_with_items(test_database):
    connection = test_database

    connection.execute(
        "INSERT INTO businesses (business_name) VALUES (?)",
        ("Test Barbershop",),
    )
    business_id = connection.execute(
        "SELECT id FROM businesses"
    ).fetchone()["id"]

    connection.execute(
        """
        INSERT INTO customers (
            business_id,
            name,
            phone,
            customer_type
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            business_id,
            "David",
            "0501234567",
            "GUEST",
        ),
    )
    customer_id = connection.execute(
        "SELECT id FROM customers"
    ).fetchone()["id"]

    connection.execute(
        """
        INSERT INTO staff (
            business_id,
            name
        )
        VALUES (?, ?)
        """,
        (
            business_id,
            "Yossi",
        ),
    )
    staff_id = connection.execute(
        "SELECT id FROM staff"
    ).fetchone()["id"]

    connection.execute(
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
            "Haircut",
            45,
            80.0,
        ),
    )
    haircut_service_id = connection.execute(
        "SELECT id FROM services WHERE name = ?",
        ("Haircut",),
    ).fetchone()["id"]

    connection.execute(
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
            "Beard",
            20,
            30.0,
        ),
    )
    beard_service_id = connection.execute(
        "SELECT id FROM services WHERE name = ?",
        ("Beard",),
    ).fetchone()["id"]

    appointment_id = create_appointment(
        connection=connection,
        business_id=business_id,
        customer_id=customer_id,
        staff_id=staff_id,
        start_datetime="2026-09-10 14:00:00",
        end_datetime="2026-09-10 15:05:00",
        items=[
            {
                "service_id": haircut_service_id,
                "service_name": "Haircut",
                "duration_minutes": 45,
                "price": 80.0,
            },
            {
                "service_id": beard_service_id,
                "service_name": "Beard",
                "duration_minutes": 20,
                "price": 30.0,
            },
        ],
    )

    appointment = connection.execute(
        """
        SELECT *
        FROM appointments
        WHERE id = ?
        """,
        (appointment_id,),
    ).fetchone()

    items = connection.execute(
        """
        SELECT *
        FROM appointment_items
        WHERE appointment_id = ?
        ORDER BY id
        """,
        (appointment_id,),
    ).fetchall()

    assert appointment is not None
    assert appointment["business_id"] == business_id
    assert appointment["customer_id"] == customer_id
    assert appointment["staff_id"] == staff_id
    assert appointment["start_datetime"] == "2026-09-10 14:00:00"
    assert appointment["end_datetime"] == "2026-09-10 15:05:00"
    assert appointment["status"] == "BOOKED"

    assert len(items) == 2

    assert items[0]["business_id"] == business_id
    assert items[0]["appointment_id"] == appointment_id
    assert items[0]["service_id"] == haircut_service_id
    assert items[0]["service_name"] == "Haircut"
    assert items[0]["duration_minutes"] == 45
    assert items[0]["price"] == 80.0

    assert items[1]["business_id"] == business_id
    assert items[1]["appointment_id"] == appointment_id
    assert items[1]["service_id"] == beard_service_id
    assert items[1]["service_name"] == "Beard"
    assert items[1]["duration_minutes"] == 20
    assert items[1]["price"] == 30.0


def test_create_appointment_does_not_commit(test_database):
    connection = test_database

    connection.execute(
        "INSERT INTO businesses (business_name) VALUES (?)",
        ("Test Barbershop",),
    )
    business_id = connection.execute(
        "SELECT id FROM businesses"
    ).fetchone()["id"]

    connection.execute(
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
            "David",
            "GUEST",
        ),
    )
    customer_id = connection.execute(
        "SELECT id FROM customers"
    ).fetchone()["id"]

    connection.execute(
        """
        INSERT INTO staff (
            business_id,
            name
        )
        VALUES (?, ?)
        """,
        (
            business_id,
            "Yossi",
        ),
    )
    staff_id = connection.execute(
        "SELECT id FROM staff"
    ).fetchone()["id"]

    appointment_id = create_appointment(
        connection=connection,
        business_id=business_id,
        customer_id=customer_id,
        staff_id=staff_id,
        start_datetime="2026-09-10 14:00:00",
        end_datetime="2026-09-10 14:45:00",
    )

    assert appointment_id is not None

    connection.rollback()

    appointment = connection.execute(
        """
        SELECT *
        FROM appointments
        WHERE id = ?
        """,
        (appointment_id,),
    ).fetchone()

    assert appointment is None


def test_transaction_rolls_back_all_repository_writes(test_database):
    connection = test_database

    with pytest.raises(RuntimeError):
        with transaction(connection):
            connection.execute(
                "INSERT INTO businesses (business_name) VALUES (?)",
                ("Rollback Barbershop",),
            )
            raise RuntimeError("force rollback")

    business = connection.execute(
        "SELECT id FROM businesses WHERE business_name = ?",
        ("Rollback Barbershop",),
    ).fetchone()

    assert business is None

from app.db.repositories import (
    get_staff,
    get_active_staff_for_service,
    get_services_by_ids,
    get_staff_service,
)


def test_get_staff_returns_staff_from_same_business(test_database):
    connection = test_database

    connection.execute(
        "INSERT INTO businesses (business_name) VALUES (?)",
        ("Test Barbershop",),
    )

    business_id = connection.execute(
        "SELECT id FROM businesses"
    ).fetchone()["id"]

    connection.execute(
        """
        INSERT INTO staff (
            business_id,
            name,
            is_active
        )
        VALUES (?, ?, ?)
        """,
        (
            business_id,
            "Staff One",
            1,
        ),
    )

    staff_id = connection.execute(
        "SELECT id FROM staff"
    ).fetchone()["id"]

    result = get_staff(
        connection,
        business_id,
        staff_id,
    )

    assert result is not None
    assert result["id"] == staff_id
    assert result["business_id"] == business_id
    assert result["name"] == "Staff One"
    assert result["is_active"] == 1


def test_get_staff_does_not_cross_business_boundary(test_database):
    connection = test_database

    connection.execute(
        "INSERT INTO businesses (business_name) VALUES (?)",
        ("Business One",),
    )
    connection.execute(
        "INSERT INTO businesses (business_name) VALUES (?)",
        ("Business Two",),
    )

    businesses = connection.execute(
        "SELECT id FROM businesses ORDER BY id"
    ).fetchall()

    business_one_id = businesses[0]["id"]
    business_two_id = businesses[1]["id"]

    connection.execute(
        """
        INSERT INTO staff (
            business_id,
            name,
            is_active
        )
        VALUES (?, ?, ?)
        """,
        (
            business_two_id,
            "Staff Two",
            1,
        ),
    )

    staff_id = connection.execute(
        "SELECT id FROM staff"
    ).fetchone()["id"]

    result = get_staff(
        connection,
        business_one_id,
        staff_id,
    )

    assert result is None


def test_get_active_staff_for_service_returns_only_eligible_staff(
    test_database,
):
    connection = test_database

    connection.execute(
        "INSERT INTO businesses (business_name) VALUES (?)",
        ("Test Barbershop",),
    )

    business_id = connection.execute(
        "SELECT id FROM businesses"
    ).fetchone()["id"]

    connection.execute(
        """
        INSERT INTO staff (
            business_id,
            name,
            is_active
        )
        VALUES (?, ?, ?)
        """,
        (
            business_id,
            "Active Staff",
            1,
        ),
    )

    staff_id = connection.execute(
        "SELECT id FROM staff"
    ).fetchone()["id"]

    connection.execute(
        """
        INSERT INTO services (
            business_id,
            name,
            description,
            default_duration_minutes,
            default_price,
            is_active
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            business_id,
            "Haircut",
            "Basic haircut",
            45,
            80,
            1,
        ),
    )

    service_id = connection.execute(
        "SELECT id FROM services"
    ).fetchone()["id"]

    connection.execute(
        """
        INSERT INTO staff_services (
            business_id,
            staff_id,
            service_id,
            duration_minutes,
            price,
            is_active
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            business_id,
            staff_id,
            service_id,
            None,
            None,
            1,
        ),
    )

    result = get_active_staff_for_service(
        connection,
        business_id,
        service_id,
    )

    assert len(result) == 1
    assert result[0]["id"] == staff_id
    assert result[0]["business_id"] == business_id
    assert result[0]["name"] == "Active Staff"
    assert result[0]["is_active"] == 1


def test_get_active_staff_for_service_excludes_inactive_staff(
    test_database,
):
    connection = test_database

    connection.execute(
        "INSERT INTO businesses (business_name) VALUES (?)",
        ("Test Barbershop",),
    )

    business_id = connection.execute(
        "SELECT id FROM businesses"
    ).fetchone()["id"]

    connection.execute(
        """
        INSERT INTO staff (
            business_id,
            name,
            is_active
        )
        VALUES (?, ?, ?)
        """,
        (
            business_id,
            "Inactive Staff",
            0,
        ),
    )

    staff_id = connection.execute(
        "SELECT id FROM staff"
    ).fetchone()["id"]

    connection.execute(
        """
        INSERT INTO services (
            business_id,
            name,
            description,
            default_duration_minutes,
            default_price,
            is_active
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            business_id,
            "Beard",
            "Beard trim",
            20,
            30,
            1,
        ),
    )

    service_id = connection.execute(
        "SELECT id FROM services"
    ).fetchone()["id"]

    connection.execute(
        """
        INSERT INTO staff_services (
            business_id,
            staff_id,
            service_id,
            duration_minutes,
            price,
            is_active
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            business_id,
            staff_id,
            service_id,
            None,
            None,
            1,
        ),
    )

    result = get_active_staff_for_service(
        connection,
        business_id,
        service_id,
    )

    assert result == []


def test_get_services_by_ids_returns_only_active_services_from_business(
    test_database,
):
    connection = test_database

    connection.execute(
        "INSERT INTO businesses (business_name) VALUES (?)",
        ("Test Barbershop",),
    )

    business_id = connection.execute(
        "SELECT id FROM businesses"
    ).fetchone()["id"]

    connection.execute(
        """
        INSERT INTO services (
            business_id,
            name,
            description,
            default_duration_minutes,
            default_price,
            is_active
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            business_id,
            "Haircut",
            "Basic haircut",
            45,
            80,
            1,
        ),
    )

    active_service_id = connection.execute(
        "SELECT id FROM services WHERE name = ?",
        ("Haircut",),
    ).fetchone()["id"]

    connection.execute(
        """
        INSERT INTO services (
            business_id,
            name,
            description,
            default_duration_minutes,
            default_price,
            is_active
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            business_id,
            "Inactive Service",
            "Not bookable",
            30,
            50,
            0,
        ),
    )

    inactive_service_id = connection.execute(
        "SELECT id FROM services WHERE name = ?",
        ("Inactive Service",),
    ).fetchone()["id"]

    result = get_services_by_ids(
        connection,
        business_id,
        [
            active_service_id,
            inactive_service_id,
        ],
    )

    assert len(result) == 1
    assert result[0]["id"] == active_service_id
    assert result[0]["name"] == "Haircut"
    assert result[0]["default_duration_minutes"] == 45
    assert result[0]["default_price"] == 80


def test_get_services_by_ids_does_not_cross_business_boundary(
    test_database,
):
    connection = test_database

    connection.execute(
        "INSERT INTO businesses (business_name) VALUES (?)",
        ("Business One",),
    )
    connection.execute(
        "INSERT INTO businesses (business_name) VALUES (?)",
        ("Business Two",),
    )

    businesses = connection.execute(
        "SELECT id FROM businesses ORDER BY id"
    ).fetchall()

    business_one_id = businesses[0]["id"]
    business_two_id = businesses[1]["id"]

    connection.execute(
        """
        INSERT INTO services (
            business_id,
            name,
            description,
            default_duration_minutes,
            default_price,
            is_active
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            business_two_id,
            "Other Business Service",
            "Other tenant",
            60,
            100,
            1,
        ),
    )

    service_id = connection.execute(
        "SELECT id FROM services"
    ).fetchone()["id"]

    result = get_services_by_ids(
        connection,
        business_one_id,
        [service_id],
    )

    assert result == []


def test_get_services_by_ids_empty_list_returns_empty_list(
    test_database,
):
    result = get_services_by_ids(
        test_database,
        1,
        [],
    )

    assert result == []


def test_get_staff_service_returns_staff_specific_configuration(
    test_database,
):
    connection = test_database

    connection.execute(
        "INSERT INTO businesses (business_name) VALUES (?)",
        ("Test Barbershop",),
    )

    business_id = connection.execute(
        "SELECT id FROM businesses"
    ).fetchone()["id"]

    connection.execute(
        """
        INSERT INTO staff (
            business_id,
            name,
            is_active
        )
        VALUES (?, ?, ?)
        """,
        (
            business_id,
            "Specialist",
            1,
        ),
    )

    staff_id = connection.execute(
        "SELECT id FROM staff"
    ).fetchone()["id"]

    connection.execute(
        """
        INSERT INTO services (
            business_id,
            name,
            description,
            default_duration_minutes,
            default_price,
            is_active
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            business_id,
            "Premium Cut",
            "Premium service",
            45,
            100,
            1,
        ),
    )

    service_id = connection.execute(
        "SELECT id FROM services"
    ).fetchone()["id"]

    connection.execute(
        """
        INSERT INTO staff_services (
            business_id,
            staff_id,
            service_id,
            duration_minutes,
            price,
            is_active
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            business_id,
            staff_id,
            service_id,
            60,
            120,
            1,
        ),
    )

    result = get_staff_service(
        connection,
        business_id,
        staff_id,
        service_id,
    )

    assert result is not None
    assert result["business_id"] == business_id
    assert result["staff_id"] == staff_id
    assert result["service_id"] == service_id
    assert result["duration_minutes"] == 60
    assert result["price"] == 120


def test_get_staff_service_preserves_null_overrides(
    test_database,
):
    connection = test_database

    connection.execute(
        "INSERT INTO businesses (business_name) VALUES (?)",
        ("Test Barbershop",),
    )

    business_id = connection.execute(
        "SELECT id FROM businesses"
    ).fetchone()["id"]

    connection.execute(
        """
        INSERT INTO staff (
            business_id,
            name,
            is_active
        )
        VALUES (?, ?, ?)
        """,
        (
            business_id,
            "Default Specialist",
            1,
        ),
    )

    staff_id = connection.execute(
        "SELECT id FROM staff"
    ).fetchone()["id"]

    connection.execute(
        """
        INSERT INTO services (
            business_id,
            name,
            description,
            default_duration_minutes,
            default_price,
            is_active
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            business_id,
            "Standard Cut",
            "Standard service",
            45,
            80,
            1,
        ),
    )

    service_id = connection.execute(
        "SELECT id FROM services"
    ).fetchone()["id"]

    connection.execute(
        """
        INSERT INTO staff_services (
            business_id,
            staff_id,
            service_id,
            duration_minutes,
            price,
            is_active
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            business_id,
            staff_id,
            service_id,
            None,
            None,
            1,
        ),
    )

    result = get_staff_service(
        connection,
        business_id,
        staff_id,
        service_id,
    )

    assert result is not None
    assert result["duration_minutes"] is None
    assert result["price"] is None

def test_get_staff_service_does_not_cross_business_boundary(test_database):
    connection = test_database

    connection.execute(
        """
        INSERT INTO businesses (business_name)
        VALUES (?), (?)
        """,
        (
            "Business A",
            "Business B",
        ),
    )

    business_a_id = connection.execute(
        """
        SELECT id
        FROM businesses
        WHERE business_name = ?
        """,
        ("Business A",),
    ).fetchone()["id"]

    business_b_id = connection.execute(
        """
        SELECT id
        FROM businesses
        WHERE business_name = ?
        """,
        ("Business B",),
    ).fetchone()["id"]

    connection.execute(
        """
        INSERT INTO staff (
            business_id,
            name,
            is_active
        )
        VALUES (?, ?, ?)
        """,
        (
            business_a_id,
            "Yossi",
            1,
        ),
    )
    staff_id = connection.execute(
        """
        SELECT id
        FROM staff
        WHERE business_id = ?
        """,
        (business_a_id,),
    ).fetchone()["id"]

    connection.execute(
        """
        INSERT INTO services (
            business_id,
            name,
            default_duration_minutes,
            default_price,
            is_active
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            business_a_id,
            "Haircut",
            45,
            80.0,
            1,
        ),
    )
    service_id = connection.execute(
        """
        SELECT id
        FROM services
        WHERE business_id = ?
        """,
        (business_a_id,),
    ).fetchone()["id"]

    connection.execute(
        """
        INSERT INTO staff_services (
            business_id,
            staff_id,
            service_id,
            duration_minutes,
            price,
            is_active
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            business_a_id,
            staff_id,
            service_id,
            60,
            100.0,
            1,
        ),
    )

    result = get_staff_service(
        connection,
        business_b_id,
        staff_id,
        service_id,
    )

    assert result is None
