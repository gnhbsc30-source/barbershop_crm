from app.db.repositories import create_customer, get_customer
from app.db.repositories import (
    create_customer,
    find_customer_by_phone,
    get_customer,
    get_customers,
    update_customer,
)
from app.db.repositories import (
    create_customer,
    get_customer,
    get_customers,
    update_customer,
)

from app.db.repositories import (
    create_customer,
    get_customer,
    get_customers,
    update_customer,
)
from app.db.repositories import (
    create_customer,
    deactivate_customer,
    find_customer_by_phone,
    get_customer,
    get_customers,
    update_customer,
)


def test_create_and_get_customer(test_database):
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

    customer = get_customer(
        connection=connection,
        business_id=business_id,
        customer_id=customer_id,
    )

    assert customer is not None
    assert customer["name"] == "David"
    assert customer["phone"] == "0501234567"
    assert customer["customer_type"] == "GUEST"


def test_get_customers_returns_customers_for_business(test_database):
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
        phone="0501111111",
        customer_type="GUEST",
    )

    create_customer(
        connection=connection,
        business_id=business_id,
        name="Yossi",
        phone="0502222222",
        customer_type="REGISTERED",
    )

    customers = get_customers(
        connection=connection,
        business_id=business_id,
    )

    assert len(customers) == 2
    assert customers[0]["name"] == "David"
    assert customers[1]["name"] == "Yossi"


def test_update_customer_changes_customer_data(test_database):
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
        phone="0501111111",
        customer_type="GUEST",
    )

    updated = update_customer(
        connection=connection,
        business_id=business_id,
        customer_id=customer_id,
        phone="0509999999",
    )

    assert updated is True

    customer = get_customer(
        connection=connection,
        business_id=business_id,
        customer_id=customer_id,
    )

    assert customer is not None
    assert customer["phone"] == "0509999999"
    assert customer["name"] == "David"

def test_find_customer_by_phone_returns_matching_customer(test_database):
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

    customer = find_customer_by_phone(
        connection=connection,
        business_id=business_id,
        phone="0501234567",
    )

    assert customer is not None
    assert customer["name"] == "David"
    assert customer["phone"] == "0501234567"

def test_deactivate_customer_marks_customer_inactive(test_database):
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

    deactivated = deactivate_customer(
        connection=connection,
        business_id=business_id,
        customer_id=customer_id,
    )

    assert deactivated is True

    customer = get_customer(
        connection=connection,
        business_id=business_id,
        customer_id=customer_id,
    )

    assert customer is not None
    assert customer["is_active"] == 0
    
def test_customer_cannot_be_accessed_from_another_business(test_database):
    connection = test_database

    connection.execute(
        "INSERT INTO businesses (business_name) VALUES (?)",
        ("Barbershop A",),
    )
    business_a_id = connection.execute(
        "SELECT id FROM businesses WHERE business_name = ?",
        ("Barbershop A",),
    ).fetchone()["id"]

    connection.execute(
        "INSERT INTO businesses (business_name) VALUES (?)",
        ("Barbershop B",),
    )
    business_b_id = connection.execute(
        "SELECT id FROM businesses WHERE business_name = ?",
        ("Barbershop B",),
    ).fetchone()["id"]

    customer_id = create_customer(
        connection=connection,
        business_id=business_a_id,
        name="David",
        phone="0501234567",
        customer_type="GUEST",
    )

    customer = get_customer(
        connection=connection,
        business_id=business_b_id,
        customer_id=customer_id,
    )

    assert customer is None