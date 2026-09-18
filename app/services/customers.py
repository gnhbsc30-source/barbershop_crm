from sqlite3 import Connection

from app.db import repositories
from app.exceptions.customer import CustomerAlreadyExistsError


def create_customer(
    connection: Connection,
    business_id: int,
    name: str,
    phone: str | None = None,
    email: str | None = None,
    national_id: str | None = None,
    customer_type: str = "GUEST",
    birthday: str | None = None,
    notes: str | None = None,
) -> int:
    """Create a new customer after applying business rules."""

    if phone is not None:
        existing_customer = repositories.find_customer_by_phone(
            connection=connection,
            business_id=business_id,
            phone=phone,
        )

        if existing_customer is not None:
            raise CustomerAlreadyExistsError(
                "A customer with this phone number already exists."
            )

    return repositories.create_customer(
        connection=connection,
        business_id=business_id,
        name=name,
        phone=phone,
        email=email,
        national_id=national_id,
        customer_type=customer_type,
        birthday=birthday,
        notes=notes,
    )