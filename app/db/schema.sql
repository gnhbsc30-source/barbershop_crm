PRAGMA foreign_keys = ON;

BEGIN;

-- ============================================================
-- Barbershop CRM - Canonical SQLite Schema
-- Single source of truth for the current MVP.
-- ============================================================

-- ------------------------------------------------------------
-- 1. Businesses / Tenants
-- ------------------------------------------------------------
CREATE TABLE businesses (
    id INTEGER PRIMARY KEY,
    business_name TEXT NOT NULL,
    display_name TEXT,
    logo_url TEXT,
    website TEXT,
    phone TEXT,
    email TEXT,
    address TEXT,
    timezone TEXT NOT NULL DEFAULT 'Asia/Jerusalem',
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ------------------------------------------------------------
-- 2. Employee login accounts
-- ------------------------------------------------------------
CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    business_id INTEGER NOT NULL,
    username TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    phone TEXT,
    phone_verified INTEGER NOT NULL DEFAULT 0 CHECK (phone_verified IN (0, 1)),
    role TEXT NOT NULL CHECK (role IN ('OWNER', 'MANAGER', 'STAFF')),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    last_login_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (business_id, id),
    UNIQUE (business_id, username),

    FOREIGN KEY (business_id)
        REFERENCES businesses(id)
        ON DELETE RESTRICT
);

-- ------------------------------------------------------------
-- 3. Staff / service providers
-- ------------------------------------------------------------
CREATE TABLE staff (
    id INTEGER PRIMARY KEY,
    business_id INTEGER NOT NULL,
    user_id INTEGER,
    name TEXT NOT NULL,
    phone TEXT,
    email TEXT,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (business_id, id),
    UNIQUE (user_id),

    FOREIGN KEY (business_id)
        REFERENCES businesses(id)
        ON DELETE RESTRICT,

    FOREIGN KEY (business_id, user_id)
        REFERENCES users(business_id, id)
        ON DELETE RESTRICT
);

-- ------------------------------------------------------------
-- 4. Customers
-- ------------------------------------------------------------
CREATE TABLE customers (
    id INTEGER PRIMARY KEY,
    business_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    phone TEXT,
    email TEXT,
    national_id TEXT,
    customer_type TEXT NOT NULL
        CHECK (customer_type IN ('GUEST', 'REGISTERED')),
    birthday TEXT,
    notes TEXT,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (business_id, id),
    UNIQUE (business_id, national_id),

    FOREIGN KEY (business_id)
        REFERENCES businesses(id)
        ON DELETE RESTRICT
);

-- ------------------------------------------------------------
-- 5. Customer login accounts
-- ------------------------------------------------------------
CREATE TABLE customer_accounts (
    id INTEGER PRIMARY KEY,
    business_id INTEGER NOT NULL,
    customer_id INTEGER NOT NULL,
    username TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    phone_verified INTEGER NOT NULL DEFAULT 0 CHECK (phone_verified IN (0, 1)),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    last_login_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (business_id, id),
    UNIQUE (customer_id),
    UNIQUE (business_id, username),

    FOREIGN KEY (business_id)
        REFERENCES businesses(id)
        ON DELETE RESTRICT,

    FOREIGN KEY (business_id, customer_id)
        REFERENCES customers(business_id, id)
        ON DELETE RESTRICT
);

-- ------------------------------------------------------------
-- 6. Services
-- ------------------------------------------------------------
CREATE TABLE services (
    id INTEGER PRIMARY KEY,
    business_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    default_duration_minutes INTEGER NOT NULL
        CHECK (default_duration_minutes > 0),
    default_price REAL NOT NULL
        CHECK (default_price >= 0),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (business_id, id),
    UNIQUE (business_id, name),

    FOREIGN KEY (business_id)
        REFERENCES businesses(id)
        ON DELETE RESTRICT
);

-- ------------------------------------------------------------
-- 7. Staff-specific service configuration / overrides
-- ------------------------------------------------------------
CREATE TABLE staff_services (
    id INTEGER PRIMARY KEY,
    business_id INTEGER NOT NULL,
    staff_id INTEGER NOT NULL,
    service_id INTEGER NOT NULL,
    duration_minutes INTEGER
        CHECK (duration_minutes IS NULL OR duration_minutes > 0),
    price REAL
        CHECK (price IS NULL OR price >= 0),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (business_id, id),
    UNIQUE (business_id, staff_id, service_id),

    FOREIGN KEY (business_id, staff_id)
        REFERENCES staff(business_id, id)
        ON DELETE RESTRICT,

    FOREIGN KEY (business_id, service_id)
        REFERENCES services(business_id, id)
        ON DELETE RESTRICT
);

-- ------------------------------------------------------------
-- 8. Business recurring working hours
-- day_of_week: 0=Sunday ... 6=Saturday
-- ------------------------------------------------------------
CREATE TABLE business_working_hours (
    id INTEGER PRIMARY KEY,
    business_id INTEGER NOT NULL,
    day_of_week INTEGER NOT NULL
        CHECK (day_of_week BETWEEN 0 AND 6),
    is_working_day INTEGER NOT NULL DEFAULT 1
        CHECK (is_working_day IN (0, 1)),
    start_time TEXT,
    end_time TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (business_id, id),
    UNIQUE (business_id, day_of_week),

    CHECK (
        (is_working_day = 0 AND start_time IS NULL AND end_time IS NULL)
        OR
        (is_working_day = 1 AND start_time IS NOT NULL AND end_time IS NOT NULL
         AND start_time < end_time)
    ),

    FOREIGN KEY (business_id)
        REFERENCES businesses(id)
        ON DELETE RESTRICT
);

-- ------------------------------------------------------------
-- 9. Staff recurring working-hours overrides
-- ------------------------------------------------------------
CREATE TABLE working_hours (
    id INTEGER PRIMARY KEY,
    staff_id INTEGER NOT NULL,
    day_of_week INTEGER NOT NULL
        CHECK (day_of_week BETWEEN 0 AND 6),
    is_working_day INTEGER NOT NULL DEFAULT 1
        CHECK (is_working_day IN (0, 1)),
    start_time TEXT,
    end_time TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (staff_id, id),
    UNIQUE (staff_id, day_of_week),

    CHECK (
        (is_working_day = 0 AND start_time IS NULL AND end_time IS NULL)
        OR
        (is_working_day = 1 AND start_time IS NOT NULL AND end_time IS NOT NULL
         AND start_time < end_time)
    ),

    FOREIGN KEY (staff_id)
        REFERENCES staff(id)
        ON DELETE RESTRICT
);

-- ------------------------------------------------------------
-- 10. Recurring staff breaks
-- ------------------------------------------------------------
CREATE TABLE schedule_breaks (
    id INTEGER PRIMARY KEY,
    staff_id INTEGER NOT NULL,
    day_of_week INTEGER NOT NULL
        CHECK (day_of_week BETWEEN 0 AND 6),
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CHECK (start_time < end_time),

    FOREIGN KEY (staff_id)
        REFERENCES staff(id)
        ON DELETE RESTRICT
);

-- ------------------------------------------------------------
-- 11. One-time / exception schedule blocks
-- staff_id NULL = entire business is blocked
-- ------------------------------------------------------------
CREATE TABLE schedule_blocks (
    id INTEGER PRIMARY KEY,
    business_id INTEGER NOT NULL,
    staff_id INTEGER,
    start_datetime TEXT NOT NULL,
    end_datetime TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (business_id, id),
    CHECK (start_datetime < end_datetime),

    FOREIGN KEY (business_id)
        REFERENCES businesses(id)
        ON DELETE RESTRICT,

    FOREIGN KEY (business_id, staff_id)
        REFERENCES staff(business_id, id)
        ON DELETE RESTRICT
);

-- ------------------------------------------------------------
-- 12. Appointments
-- ------------------------------------------------------------
CREATE TABLE appointments (
    id INTEGER PRIMARY KEY,
    business_id INTEGER NOT NULL,
    customer_id INTEGER NOT NULL,
    staff_id INTEGER NOT NULL,
    start_datetime TEXT NOT NULL,
    end_datetime TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('BOOKED', 'COMPLETED', 'CANCELLED', 'NO_SHOW')),
    notes TEXT,
    cancelled_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (business_id, id),
    CHECK (start_datetime < end_datetime),

    FOREIGN KEY (business_id)
        REFERENCES businesses(id)
        ON DELETE RESTRICT,

    FOREIGN KEY (business_id, customer_id)
        REFERENCES customers(business_id, id)
        ON DELETE RESTRICT,

    FOREIGN KEY (business_id, staff_id)
        REFERENCES staff(business_id, id)
        ON DELETE RESTRICT
);

-- ------------------------------------------------------------
-- 13. Services attached to an appointment
-- price and duration are the appointment's current values;
-- service_name preserves the historical display name.
-- ------------------------------------------------------------
CREATE TABLE appointment_items (
    id INTEGER PRIMARY KEY,
    business_id INTEGER NOT NULL,
    appointment_id INTEGER NOT NULL,
    service_id INTEGER NOT NULL,
    service_name TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL
        CHECK (duration_minutes > 0),
    price REAL NOT NULL
        CHECK (price >= 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (business_id, id),

    FOREIGN KEY (business_id, appointment_id)
        REFERENCES appointments(business_id, id)
        ON DELETE RESTRICT,

    FOREIGN KEY (business_id, service_id)
        REFERENCES services(business_id, id)
        ON DELETE RESTRICT
);

-- ------------------------------------------------------------
-- 14. Waitlist
-- ------------------------------------------------------------
CREATE TABLE waitlist (
    id INTEGER PRIMARY KEY,
    business_id INTEGER NOT NULL,
    customer_id INTEGER NOT NULL,
    service_id INTEGER NOT NULL,
    staff_id INTEGER,
    requested_date TEXT NOT NULL,
    earliest_time TEXT,
    latest_time TEXT,
    status TEXT NOT NULL
        CHECK (status IN ('ACTIVE', 'FULFILLED', 'CANCELLED', 'EXPIRED')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (business_id, id),

    CHECK (
        (earliest_time IS NULL AND latest_time IS NULL)
        OR
        (earliest_time IS NOT NULL AND latest_time IS NOT NULL
         AND earliest_time < latest_time)
    ),

    FOREIGN KEY (business_id)
        REFERENCES businesses(id)
        ON DELETE RESTRICT,

    FOREIGN KEY (business_id, customer_id)
        REFERENCES customers(business_id, id)
        ON DELETE RESTRICT,

    FOREIGN KEY (business_id, service_id)
        REFERENCES services(business_id, id)
        ON DELETE RESTRICT,

    FOREIGN KEY (business_id, staff_id)
        REFERENCES staff(business_id, id)
        ON DELETE RESTRICT
);

-- ------------------------------------------------------------
-- 15. Business settings
-- ------------------------------------------------------------
CREATE TABLE business_settings (
    id INTEGER PRIMARY KEY,
    business_id INTEGER NOT NULL,
    booking_window_months INTEGER NOT NULL DEFAULT 3
        CHECK (booking_window_months > 0),
    minimum_booking_notice_minutes INTEGER NOT NULL DEFAULT 0
        CHECK (minimum_booking_notice_minutes >= 0),
    cancellation_cutoff_hours INTEGER NOT NULL DEFAULT 4
        CHECK (cancellation_cutoff_hours >= 0),
    allow_guest_booking INTEGER NOT NULL DEFAULT 1
        CHECK (allow_guest_booking IN (0, 1)),
    allow_customer_reschedule INTEGER NOT NULL DEFAULT 1
        CHECK (allow_customer_reschedule IN (0, 1)),
    allow_customer_cancel INTEGER NOT NULL DEFAULT 1
        CHECK (allow_customer_cancel IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (business_id),
    FOREIGN KEY (business_id)
        REFERENCES businesses(id)
        ON DELETE RESTRICT
);

-- ------------------------------------------------------------
-- 16. Conversation sessions
-- session_type: CUSTOMER / ADMIN
-- state: IDLE / BOOKING / WAITING_CONFIRMATION / CANCELLATION
-- ------------------------------------------------------------
CREATE TABLE conversation_sessions (
    id INTEGER PRIMARY KEY,
    business_id INTEGER NOT NULL,
    customer_id INTEGER,
    session_type TEXT NOT NULL
        CHECK (session_type IN ('CUSTOMER', 'ADMIN')),
    state TEXT NOT NULL
        CHECK (state IN ('IDLE', 'BOOKING', 'WAITING_CONFIRMATION', 'CANCELLATION')),
    context_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (business_id, id),

    FOREIGN KEY (business_id)
        REFERENCES businesses(id)
        ON DELETE RESTRICT,

    FOREIGN KEY (business_id, customer_id)
        REFERENCES customers(business_id, id)
        ON DELETE RESTRICT
);

-- ============================================================
-- Indexes
-- ============================================================

CREATE INDEX idx_users_business_phone
    ON users (business_id, phone);

CREATE INDEX idx_customers_business_phone
    ON customers (business_id, phone);

CREATE INDEX idx_staff_business
    ON staff (business_id);

CREATE INDEX idx_staff_services_staff
    ON staff_services (staff_id);

CREATE INDEX idx_working_hours_staff_day
    ON working_hours (staff_id, day_of_week);

CREATE INDEX idx_schedule_breaks_staff_day
    ON schedule_breaks (staff_id, day_of_week);

CREATE INDEX idx_schedule_blocks_business_start
    ON schedule_blocks (business_id, start_datetime);

CREATE INDEX idx_schedule_blocks_staff_start
    ON schedule_blocks (staff_id, start_datetime);

CREATE INDEX idx_appointments_business_start
    ON appointments (business_id, start_datetime);

CREATE INDEX idx_appointments_staff_start
    ON appointments (staff_id, start_datetime);

CREATE INDEX idx_appointments_customer_start
    ON appointments (customer_id, start_datetime);

CREATE INDEX idx_appointment_items_appointment
    ON appointment_items (appointment_id);

CREATE INDEX idx_waitlist_business_date_status_created
    ON waitlist (business_id, requested_date, status, created_at);

CREATE INDEX idx_conversation_sessions_business_type_updated
    ON conversation_sessions (business_id, session_type, updated_at);

COMMIT;
