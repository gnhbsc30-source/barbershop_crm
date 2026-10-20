from sqlite3 import Connection
from typing import Any


def get_user_account_identity(
    connection: Connection,
    business_id: int,
    user_id: int,
) -> dict[str, Any] | None:
    """Read an employee account and business state without authorizing it."""
    row = connection.execute(
        """
        SELECT u.id, u.business_id, u.username, u.password_hash, u.role,
               u.is_active, b.is_active AS business_is_active
        FROM users AS u
        LEFT JOIN businesses AS b ON b.id = u.business_id
        WHERE u.business_id = ? AND u.id = ?
        """,
        (business_id, user_id),
    ).fetchone()
    return dict(row) if row else None


def get_customer_account_identity(
    connection: Connection,
    business_id: int,
    account_id: int,
) -> dict[str, Any] | None:
    """Read an account with its tenant-matched customer and business state."""
    row = connection.execute(
        """
        SELECT a.id, a.business_id, a.customer_id, a.username, a.password_hash,
               a.is_active, b.is_active AS business_is_active,
               c.id AS linked_customer_id,
               c.business_id AS linked_customer_business_id,
               c.is_active AS customer_is_active
        FROM customer_accounts AS a
        LEFT JOIN businesses AS b ON b.id = a.business_id
        LEFT JOIN customers AS c
            ON c.id = a.customer_id AND c.business_id = a.business_id
        WHERE a.business_id = ? AND a.id = ?
        """,
        (business_id, account_id),
    ).fetchone()
    return dict(row) if row else None


def replace_account_credential(
    connection: Connection, *, account_kind: str, business_id: int,
    account_id: int, username: str, expected_hash: str, new_hash: str,
) -> bool:
    """Conditional persistence only; caller verifies identity and owns transaction."""
    tables = {"user": "users", "customer_account": "customer_accounts"}
    if account_kind not in tables:
        raise ValueError("INVALID_ACCOUNT_KIND")
    cursor = connection.execute(
        f"""UPDATE {tables[account_kind]}
            SET password_hash = ?, updated_at = CURRENT_TIMESTAMP
            WHERE business_id = ? AND id = ? AND username = ? AND password_hash = ?""",
        (new_hash, business_id, account_id, username, expected_hash),
    )
    return cursor.rowcount == 1


def create_development_fixture(
    connection: Connection, *, fixture: dict[str, Any],
    owner_username: str, owner_hash: str, customer_username: str, customer_hash: str,
) -> int:
    """Insert the explicit synthetic dataset; no reset, policy or commit."""
    business_id = connection.execute(
        "INSERT INTO businesses (business_name, timezone) VALUES (?, ?)",
        (fixture["business_name"], fixture["timezone"]),
    ).lastrowid
    connection.execute(
        "INSERT INTO users (business_id, username, password_hash, role) VALUES (?, ?, ?, 'OWNER')",
        (business_id, owner_username, owner_hash),
    )
    customer_id = connection.execute(
        "INSERT INTO customers (business_id, name, email, customer_type) VALUES (?, ?, ?, 'REGISTERED')",
        (business_id, fixture["customer_name"], fixture["customer_email"]),
    ).lastrowid
    connection.execute(
        "INSERT INTO customer_accounts (business_id, customer_id, username, password_hash) VALUES (?, ?, ?, ?)",
        (business_id, customer_id, customer_username, customer_hash),
    )
    staff_ids = [connection.execute(
        "INSERT INTO staff (business_id, name) VALUES (?, ?)", (business_id, name),
    ).lastrowid for name in fixture["staff_names"]]
    service_ids = [connection.execute(
        "INSERT INTO services (business_id, name, default_duration_minutes, default_price) VALUES (?, ?, ?, ?)",
        (business_id, name, duration, price),
    ).lastrowid for name, duration, price in fixture["services"]]
    for staff_id in staff_ids:
        for service_id in service_ids:
            connection.execute(
                "INSERT INTO staff_services (business_id, staff_id, service_id) VALUES (?, ?, ?)",
                (business_id, staff_id, service_id),
            )
    for day in range(7):
        working = int(day in fixture["working_days"])
        start, end = (fixture["start_time"], fixture["end_time"]) if working else (None, None)
        connection.execute(
            "INSERT INTO business_working_hours (business_id, day_of_week, is_working_day, start_time, end_time) VALUES (?, ?, ?, ?, ?)",
            (business_id, day, working, start, end),
        )
        for staff_id in staff_ids:
            connection.execute(
                "INSERT INTO working_hours (staff_id, day_of_week, is_working_day, start_time, end_time) VALUES (?, ?, ?, ?, ?)",
                (staff_id, day, working, start, end),
            )
    connection.execute(
        """INSERT INTO business_settings (business_id, booking_window_months,
           minimum_booking_notice_minutes, alternative_search_window_days, booking_interval_minutes)
           VALUES (?, ?, ?, ?, ?)""",
        (business_id, *fixture["booking_settings"]),
    )
    return business_id


def get_development_fixture_snapshot(connection: Connection, business_id: int) -> dict[str, Any]:
    """Non-secret fixture records; appointments and credential hashes are excluded."""
    queries = {
        "businesses": "SELECT id, business_name, timezone, is_active FROM businesses WHERE id = ? ORDER BY id",
        "users": "SELECT id, business_id, username, role, is_active FROM users WHERE business_id = ? ORDER BY id",
        "customers": "SELECT id, business_id, name, email, customer_type, is_active FROM customers WHERE business_id = ? ORDER BY id",
        "customer_accounts": "SELECT id, business_id, customer_id, username, is_active FROM customer_accounts WHERE business_id = ? ORDER BY id",
        "staff": "SELECT id, business_id, name, is_active FROM staff WHERE business_id = ? ORDER BY id",
        "services": "SELECT id, business_id, name, default_duration_minutes, default_price, is_active FROM services WHERE business_id = ? ORDER BY id",
        "staff_services": "SELECT id, business_id, staff_id, service_id, duration_minutes, price, is_active FROM staff_services WHERE business_id = ? ORDER BY id",
        "business_working_hours": "SELECT id, business_id, day_of_week, is_working_day, start_time, end_time FROM business_working_hours WHERE business_id = ? ORDER BY id",
        "working_hours": "SELECT h.id, h.staff_id, h.day_of_week, h.is_working_day, h.start_time, h.end_time FROM working_hours h JOIN staff s ON s.id = h.staff_id WHERE s.business_id = ? ORDER BY h.id",
        "business_settings": "SELECT id, business_id, booking_window_months, minimum_booking_notice_minutes, alternative_search_window_days, booking_interval_minutes FROM business_settings WHERE business_id = ? ORDER BY id",
    }
    return {name: [dict(row) for row in connection.execute(query, (business_id,)).fetchall()]
            for name, query in queries.items()}


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
            minimum_booking_notice_minutes,
            alternative_search_window_days,
            booking_interval_minutes,
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
