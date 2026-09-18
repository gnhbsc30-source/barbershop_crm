import pytest

from app.exceptions.customer import CustomerAlreadyExistsError
from app.services.customers import create_customer


def test_create_customer_service_creates_customer(test_database):
    connection = test_database

    connection.execute(
        "INSERT INTO businesses (business_name) VALUES (?)",
        ("Test Barbershop",),
    )

    business_id = connection.execute(
        "SELECT id FROM businesses"
    ).fetchone()["id"]

    customer_id = create_customer(
        connection=connection,
        business_id=business_id,
        name="David",
        phone="0501234567",
        customer_type="GUEST",
    )

    customer = connection.execute(
        """
        SELECT
            id,
            business_id,
            name,
            phone,
            customer_type
        FROM customers
        WHERE id = ?
        """,
        (customer_id,),
    ).fetchone()

    assert customer is not None
    assert customer["business_id"] == business_id
    assert customer["name"] == "David"
    assert customer["phone"] == "0501234567"
    assert customer["customer_type"] == "GUEST"


def test_create_customer_rejects_duplicate_phone(test_database):
    connection = test_database

    connection.execute(
        "INSERT INTO businesses (business_name) VALUES (?)",
        ("Test Barbershop",),
    )

    business_id = connection.execute(
        "SELECT id FROM businesses"
    ).fetchone()["id"]

    create_customer(
        connection=connection,
        business_id=business_id,
        name="David",
        phone="0501234567",
        customer_type="GUEST",
    )

    with pytest.raises(CustomerAlreadyExistsError):
        create_customer(
            connection=connection,
            business_id=business_id,
            name="Yossi",
            phone="0501234567",
            customer_type="GUEST",
        )