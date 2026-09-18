from sqlite3 import Connection
from typing import Any


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
    """Create a customer and return its ID."""
    cursor = connection.execute(
        """
        INSERT INTO customers (
            business_id,
            name,
            phone,
            email,
            national_id,
            customer_type,
            birthday,
            notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            business_id,
            name,
            phone,
            email,
            national_id,
            customer_type,
            birthday,
            notes,
        ),
    )

    return cursor.lastrowid


def get_customer(
    connection: Connection,
    business_id: int,
    customer_id: int,
) -> dict[str, Any] | None:
    """Return one customer belonging to the specified business."""
    row = connection.execute(
        """
        SELECT
            id,
            business_id,
            name,
            phone,
            email,
            national_id,
            customer_type,
            birthday,
            notes,
            is_active,
            created_at,
            updated_at
        FROM customers
        WHERE business_id = ?
          AND id = ?
        """,
        (business_id, customer_id),
    ).fetchone()

    return dict(row) if row else None


def get_customers(
    connection: Connection,
    business_id: int,
    include_inactive: bool = False,
) -> list[dict[str, Any]]:
    """Return customers belonging to the specified business."""
    query = """
        SELECT
            id,
            business_id,
            name,
            phone,
            email,
            national_id,
            customer_type,
            birthday,
            notes,
            is_active,
            created_at,
            updated_at
        FROM customers
        WHERE business_id = ?
    """

    parameters: list[Any] = [business_id]

    if not include_inactive:
        query += " AND is_active = 1"

    query += " ORDER BY name COLLATE NOCASE"

    rows = connection.execute(query, parameters).fetchall()

    return [dict(row) for row in rows]


def update_customer(
    connection: Connection,
    business_id: int,
    customer_id: int,
    *,
    name: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    national_id: str | None = None,
    customer_type: str | None = None,
    birthday: str | None = None,
    notes: str | None = None,
    is_active: bool | None = None,
) -> bool:
    """Update provided customer fields and return whether a row was updated."""
    fields: list[str] = []
    values: list[Any] = []

    updates = {
        "name": name,
        "phone": phone,
        "email": email,
        "national_id": national_id,
        "customer_type": customer_type,
        "birthday": birthday,
        "notes": notes,
        "is_active": int(is_active) if is_active is not None else None,
    }

    for field, value in updates.items():
        if value is not None:
            fields.append(f"{field} = ?")
            values.append(value)

    if not fields:
        return False

    fields.append("updated_at = CURRENT_TIMESTAMP")

    values.extend([business_id, customer_id])

    cursor = connection.execute(
        f"""
        UPDATE customers
        SET {", ".join(fields)}
        WHERE business_id = ?
          AND id = ?
        """,
        values,
    )

    return cursor.rowcount > 0


def find_customer_by_phone(
    connection: Connection,
    business_id: int,
    phone: str,
) -> dict[str, Any] | None:
    """Return a customer belonging to the specified business by phone."""
    row = connection.execute(
        """
        SELECT
            id,
            business_id,
            name,
            phone,
            email,
            national_id,
            customer_type,
            birthday,
            notes,
            is_active,
            created_at,
            updated_at
        FROM customers
        WHERE business_id = ?
          AND phone = ?
        LIMIT 1
        """,
        (business_id, phone),
    ).fetchone()

    return dict(row) if row else None


def deactivate_customer(
    connection: Connection,
    business_id: int,
    customer_id: int,
) -> bool:
    """Deactivate a customer without deleting the database record."""
    cursor = connection.execute(
        """
        UPDATE customers
        SET
            is_active = 0,
            updated_at = CURRENT_TIMESTAMP
        WHERE business_id = ?
          AND id = ?
          AND is_active = 1
        """,
        (business_id, customer_id),
    )

    return cursor.rowcount > 0

def create_appointment(
    connection: Connection,
    business_id: int,
    customer_id: int,
    staff_id: int,
    start_datetime: str,
    end_datetime: str,
    status: str = "BOOKED",
    notes: str | None = None,
    items: list[dict[str, Any]] | None = None,
) -> int:
    """Create an appointment and its appointment items."""
    cursor = connection.execute(
        """
        INSERT INTO appointments (
            business_id,
            customer_id,
            staff_id,
            start_datetime,
            end_datetime,
            status,
            notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            business_id,
            customer_id,
            staff_id,
            start_datetime,
            end_datetime,
            status,
            notes,
        ),
    )

    appointment_id = cursor.lastrowid

    for item in items or []:
        connection.execute(
            """
            INSERT INTO appointment_items (
                business_id,
                appointment_id,
                service_id,
                service_name,
                duration_minutes,
                price
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                business_id,
                appointment_id,
                item["service_id"],
                item["service_name"],
                item["duration_minutes"],
                item["price"],
            ),
        )

    return appointment_id

def get_staff(
    connection: Connection,
    business_id: int,
    staff_id: int,
) -> dict[str, Any] | None:
    """Return a staff member belonging to the specified business."""
    row = connection.execute(
        """
        SELECT
            id,
            business_id,
            user_id,
            name,
            phone,
            email,
            is_active
        FROM staff
        WHERE business_id = ?
          AND id = ?
        """,
        (business_id, staff_id),
    ).fetchone()

    return dict(row) if row else None

def get_active_staff_for_service(
    connection: Connection,
    business_id: int,
    service_id: int,
) -> list[dict[str, Any]]:
    """Return active staff members who can perform the specified service."""
    rows = connection.execute(
        """
        SELECT
            s.id,
            s.business_id,
            s.user_id,
            s.name,
            s.phone,
            s.email,
            s.is_active
        FROM staff AS s
        INNER JOIN staff_services AS ss
            ON ss.business_id = s.business_id
           AND ss.staff_id = s.id
        WHERE s.business_id = ?
          AND ss.service_id = ?
          AND s.is_active = 1
          AND ss.is_active = 1
        ORDER BY s.id
        """,
        (business_id, service_id),
    ).fetchall()

    return [dict(row) for row in rows]

def get_services_by_ids(
    connection: Connection,
    business_id: int,
    service_ids: list[int],
) -> list[dict[str, Any]]:
    """Return active services belonging to the specified business."""
    if not service_ids:
        return []

    placeholders = ", ".join("?" for _ in service_ids)

    rows = connection.execute(
        f"""
        SELECT
            id,
            business_id,
            name,
            description,
            default_duration_minutes,
            default_price,
            is_active
        FROM services
        WHERE business_id = ?
          AND is_active = 1
          AND id IN ({placeholders})
        ORDER BY id
        """,
        [business_id, *service_ids],
    ).fetchall()

    return [dict(row) for row in rows]

def get_staff_service(
    connection: Connection,
    business_id: int,
    staff_id: int,
    service_id: int,
) -> dict[str, Any] | None:
    """Return the staff-specific configuration for a service."""
    row = connection.execute(
        """
        SELECT
            id,
            business_id,
            staff_id,
            service_id,
            duration_minutes,
            price,
            is_active
        FROM staff_services
        WHERE business_id = ?
          AND staff_id = ?
          AND service_id = ?
        """,
        (business_id, staff_id, service_id),
    ).fetchone()

    return dict(row) if row else None

def get_business_working_hours(
    connection: Connection,
    business_id: int,
    day_of_week: int,
) -> dict[str, Any] | None:
    """Return working hours for a business on a specific day."""
    row = connection.execute(
        """
        SELECT
            business_id,
            day_of_week,
            is_working_day,
            start_time,
            end_time
        FROM business_working_hours
        WHERE business_id = ?
          AND day_of_week = ?
        """,
        (business_id, day_of_week),
    ).fetchone()

    return dict(row) if row else None

def get_staff_working_hours(
    connection: Connection,
    staff_id: int,
    day_of_week: int,
) -> dict[str, Any] | None:
    """Return working hours for a staff member on a specific day."""
    row = connection.execute(
        """
        SELECT
            staff_id,
            day_of_week,
            is_working_day,
            start_time,
            end_time
        FROM working_hours
        WHERE staff_id = ?
          AND day_of_week = ?
        """,
        (staff_id, day_of_week),
    ).fetchone()

    return dict(row) if row else None

def get_schedule_breaks(
    connection: Connection,
    staff_id: int,
    day_of_week: int,
) -> list[dict[str, Any]]:
    """Return all schedule breaks for a staff member on a specific day."""
    rows = connection.execute(
        """
        SELECT
            staff_id,
            day_of_week,
            start_time,
            end_time
        FROM schedule_breaks
        WHERE staff_id = ?
          AND day_of_week = ?
        ORDER BY start_time
        """,
        (staff_id, day_of_week),
    ).fetchall()

    return [dict(row) for row in rows]

def get_schedule_blocks(
    connection: Connection,
    business_id: int,
    staff_id: int,
    start_datetime: str,
    end_datetime: str,
) -> list[dict[str, Any]]:
    """Return business-wide and staff-specific schedule blocks overlapping a time range."""
    rows = connection.execute(
        """
        SELECT
            id,
            business_id,
            staff_id,
            start_datetime,
            end_datetime,
            reason
        FROM schedule_blocks
        WHERE business_id = ?
          AND (staff_id IS NULL OR staff_id = ?)
          AND start_datetime < ?
          AND end_datetime > ?
        ORDER BY start_datetime
        """,
        (
            business_id,
            staff_id,
            end_datetime,
            start_datetime,
        ),
    ).fetchall()

    return [dict(row) for row in rows]

def get_staff_appointments(
    connection: Connection,
    business_id: int,
    staff_id: int,
    start_datetime: str,
    end_datetime: str,
) -> list[dict[str, Any]]:
    """Return active appointments for a staff member overlapping a time range."""
    rows = connection.execute(
        """
        SELECT
            id,
            business_id,
            customer_id,
            staff_id,
            start_datetime,
            end_datetime,
            status,
            notes
        FROM appointments
        WHERE business_id = ?
          AND staff_id = ?
          AND status != 'CANCELLED'
          AND start_datetime < ?
          AND end_datetime > ?
        ORDER BY start_datetime
        """,
        (
            business_id,
            staff_id,
            end_datetime,
            start_datetime,
        ),
    ).fetchall()

    return [dict(row) for row in rows]
def get_business_settings(
    connection: Connection,
    business_id: int,
) -> dict[str, Any] | None:
    """Return settings for a business."""
    row = connection.execute(
        """
        SELECT
            business_id,
            booking_window_months,
            cancellation_cutoff_hours,
            allow_guest_booking,
            allow_customer_reschedule,
            allow_customer_cancel
        FROM business_settings
        WHERE business_id = ?
        """,
        (business_id,),
    ).fetchone()

    return dict(row) if row else None
