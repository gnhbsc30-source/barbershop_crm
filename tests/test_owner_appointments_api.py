from contextlib import closing

import pytest

from app.db import database, repositories

from test_auth_api import client, execute, http_data, login


ROOT = "/api/v1/owner"
CALENDAR = ROOT + "/appointments?from_date=2027-01-04&to_date_exclusive=2027-01-05"


@pytest.fixture
def owner(client, http_data):
    response = login(client, http_data[2]["OWNER"])
    assert response.status_code == 200
    return {"Origin": "http://127.0.0.1:8000", "X-CSRF-Token": response.json()["csrf_token"]}


def insert_appointment(path, start, end, *, business=1, customer=1, staff=1, service=1, status="BOOKED"):
    with closing(database.open_development_database(path)) as connection, database.transaction(connection):
        return repositories.create_appointment(
            connection, business, customer, staff, start, end, status=status, notes="appointment",
            items=[{"service_id": service, "service_name": "Booked haircut", "duration_minutes": 45, "price": 80.0}])


def test_owner_reads_require_fresh_owner(client, http_data):
    assert client.get(CALENDAR).status_code == 401
    assert login(client, http_data[2]["CUSTOMER"]).status_code == 200
    assert client.get(CALENDAR).status_code == 403
    assert login(client, http_data[2]["OWNER"]).status_code == 200
    assert client.get(CALENDAR).status_code == 200
    execute(http_data[0], "UPDATE users SET is_active=0")
    assert client.get(CALENDAR).status_code == 401


def test_calendar_intersection_pagination_and_contact_projection(client, http_data, owner):
    path = http_data[0]
    execute(path, "UPDATE customers SET phone='0501234567', national_id='private'")
    execute(path, "UPDATE staff SET is_active=0 WHERE id=1")
    insert_appointment(path, "2027-01-03T23:15:00", "2027-01-04T00:00:00")
    crossing = insert_appointment(path, "2027-01-03T23:30:00", "2027-01-04T00:15:00")
    tied = [insert_appointment(path, "2027-01-04T10:00:00", "2027-01-04T10:45:00", status=status)
            for status in ("COMPLETED", "CANCELLED")]
    insert_appointment(path, "2027-01-05T00:00:00", "2027-01-05T00:45:00")
    url = CALENDAR + "&limit=1&staff_id=1"
    ids = []
    response = client.get(url)
    for _ in range(3):
        assert response.status_code == 200, response.text
        body = response.json()
        row, = body["appointments"]
        ids.append(row["appointment_id"])
        assert "customer_phone" not in row and "notes" not in row and "private" not in response.text
        assert row["items"][0]["service_name"] == "Booked haircut"
        if body["next_cursor"]:
            assert client.get(CALENDAR + "&staff_id=2&cursor=" + body["next_cursor"]).status_code == 422
            response = client.get(url + "&cursor=" + body["next_cursor"])
    assert ids == [crossing, *tied]
    assert body["next_cursor"] is None
    detail = client.get(f"{ROOT}/appointments/{crossing}")
    assert detail.json()["customer_phone"] == "0501234567"
    assert detail.headers["etag"].startswith('"')


@pytest.mark.parametrize("query", [
    "", "?from_date=2027-01-01&to_date_exclusive=2027-02-02",
    "?from_date=2027-01-01&to_date_exclusive=2027-01-01",
    "?from_date=2027-01-01&to_date_exclusive=2027-01-02&limit=201",
    "?from_date=2027-01-01&to_date_exclusive=2027-01-02&cursor=broken",
])
def test_calendar_bounds(client, owner, query):
    assert client.get(ROOT + "/appointments" + query).status_code == 422


def test_calendar_and_detail_never_read_foreign_appointments(client, owner, http_data):
    path = http_data[0]
    execute(path, "INSERT INTO businesses(id,business_name) VALUES (2,'Foreign')")
    execute(path, "INSERT INTO customers(id,business_id,name,customer_type) VALUES (2,2,'Foreign','GUEST')")
    execute(path, "INSERT INTO staff(id,business_id,name) VALUES (9,2,'Foreign')")
    execute(path, "INSERT INTO services(id,business_id,name,default_duration_minutes,default_price) VALUES (9,2,'Foreign',45,80)")
    appointment = insert_appointment(path, "2027-01-04T10:00:00", "2027-01-04T10:45:00",
                                     business=2, customer=2, staff=9, service=9)
    assert client.get(CALENDAR).json()["appointments"] == []
    assert client.get(CALENDAR + "&staff_id=9").status_code == 404
    assert client.get(f"{ROOT}/appointments/{appointment}").status_code == 404


@pytest.fixture
def schedule_clock(monkeypatch):
    from datetime import datetime
    from app.core import booking_clock
    from app.services import appointments, availability, owner_appointments

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2027, 1, 4, 9, tzinfo=tz)

    for module in (appointments, availability, owner_appointments):
        monkeypatch.setattr(module, "datetime", FrozenDatetime)
    monkeypatch.setattr(booking_clock, "scheduling_clock_ready", lambda zone: zone == "Asia/Jerusalem")


SLOT = {"staff_id": 1, "start_datetime": "2027-01-04T10:00:00", "service_ids": [1]}


def booking_body(client, headers, *, slot=SLOT, customer=None):
    quote = client.post(ROOT + "/appointment-quotes", headers=headers, json=slot)
    assert quote.status_code == 200 and quote.json()["available"], quote.text
    return {**slot, "confirmation": quote.json()["quote"]["confirmation"],
            "customer": customer or {"type": "EXISTING", "customer_id": 1}}


def counts(path):
    with closing(database.open_development_database(path)) as connection:
        return tuple(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                     for table in ("customers", "customer_accounts", "appointments", "appointment_items"))


def test_picker_and_booking_options(client, owner, http_data):
    path = http_data[0]
    execute(path, "UPDATE customers SET phone='0501234567', national_id='private' WHERE id=1")
    execute(path, "INSERT INTO businesses(id,business_name) VALUES (2,'Foreign')")
    execute(path, "INSERT INTO customers(business_id,name,phone,customer_type) VALUES (2,'Synthetic foreign','0501234567','GUEST')")
    execute(path, "INSERT INTO customers(business_id,name,phone,customer_type,is_active) VALUES (1,'Synthetic inactive','0501234567','GUEST',0)")
    assert client.get(ROOT + "/customers?name_prefix=Synthetic").json()["customers"] == [
        {"customer_id": 1, "name": "Synthetic Customer", "phone": "0501234567"}]
    assert client.get(ROOT + "/customers?phone=0501234567").json()["customers"][0]["customer_id"] == 1
    assert client.get(ROOT + "/customers?name_prefix=%25_").json()["customers"] == []
    response = client.get(ROOT + "/booking-options?service_id=2&service_id=1")
    quote = response.json()["staff"][0]["quote"]
    assert [item["service_id"] for item in quote["items"]] == [2, 1]
    assert quote["total_price"] == "120.00"
    assert client.get(ROOT + "/booking-options?service_id=1&service_id=1").status_code == 422


@pytest.mark.parametrize("amount", [79, 81])
def test_existing_customer_reconfirms_changed_quote(client, owner, schedule_clock, http_data, amount):
    body = booking_body(client, owner)
    execute(http_data[0], "UPDATE services SET default_price=? WHERE id=1", (amount,))
    response = client.post(ROOT + "/appointments", headers=owner, json=body)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "BOOKING_QUOTE_CHANGED"
    assert counts(http_data[0]) == (1, 1, 0, 0)


def test_existing_customer_booking_success_and_overlap(client, owner, schedule_clock, http_data):
    body = booking_body(client, owner)
    assert counts(http_data[0]) == (1, 1, 0, 0)
    result = client.post(ROOT + "/appointments", headers=owner, json=body)
    assert result.status_code == 201, result.text
    detail = client.get(result.headers["location"])
    assert detail.json() == result.json() and detail.headers["etag"] == result.headers["etag"]
    assert result.json()["total_price"] == "80.00"
    assert client.post(ROOT + "/appointments", headers=owner, json=body).status_code == 409
    assert counts(http_data[0]) == (1, 1, 1, 1)


@pytest.mark.parametrize("suffix", ["/appointments", "/appointment-quotes"])
def test_owner_post_requires_csrf(client, owner, suffix):
    assert client.post(ROOT + suffix, headers={"Origin": owner["Origin"]}, json={}).status_code == 403


@pytest.mark.parametrize("phone", [None, "+972501234567"])
def test_guest_booking_survives_real_lifespan_restart(http_data, schedule_clock, phone):
    from starlette.testclient import TestClient
    from app.core.local_security import DEVELOPMENT_ORIGIN
    from app.main import app

    with TestClient(app, base_url=DEVELOPMENT_ORIGIN, client=("127.0.0.1", 50000)) as first:
        session = login(first, http_data[2]["OWNER"])
        headers = {"Origin": DEVELOPMENT_ORIGIN, "X-CSRF-Token": session.json()["csrf_token"]}
        guest = {"type": "NEW", "name": "Walk-in"}
        if phone is not None:
            guest["phone"] = phone
        result = first.post(ROOT + "/appointments", headers=headers, json=booking_body(first, headers, customer=guest))
        assert result.status_code == 201, result.text
        assert result.json()["customer_phone"] == phone

    with TestClient(app, base_url=DEVELOPMENT_ORIGIN, client=("127.0.0.1", 50000)) as second:
        assert login(second, http_data[2]["OWNER"]).status_code == 200
        assert second.get(result.headers["location"]).json() == result.json()
        assert second.get(ROOT + "/customers?name_prefix=Walk").json()["customers"] == [
            {"customer_id": result.json()["customer_id"], "name": "Walk-in", "phone": phone}]
    assert counts(http_data[0]) == (2, 1, 1, 1)


@pytest.mark.parametrize("active", [0, 1])
def test_guest_exact_duplicate_rejected(client, owner, schedule_clock, http_data, active):
    execute(http_data[0], "UPDATE customers SET phone='0501234567', is_active=? WHERE id=1", (active,))
    body = booking_body(client, owner, customer={"type": "NEW", "name": "Walk-in", "phone": " 0501234567 "})
    result = client.post(ROOT + "/appointments", headers=owner, json=body)
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "CUSTOMER_PHONE_ALREADY_EXISTS"
    assert counts(http_data[0]) == (1, 1, 0, 0)


@pytest.mark.parametrize("failure", ["quote", "response", "item"])
def test_guest_booking_rolls_back_everything(client, owner, schedule_clock, http_data, monkeypatch, failure):
    from app.api import owner_appointments
    body = booking_body(client, owner, customer={"type": "NEW", "name": "Walk-in"})
    if failure == "quote":
        execute(http_data[0], "UPDATE services SET default_price=81 WHERE id=1")
        status = 409
    else:
        def fail(*args, **kwargs):
            raise RuntimeError("private")
        if failure == "response":
            monkeypatch.setattr(owner_appointments, "detail_response", fail)
        else:
            execute(http_data[0], """CREATE TRIGGER deny_item BEFORE INSERT ON appointment_items
                BEGIN SELECT RAISE(ABORT, 'private'); END""")
        status = 500
    result = client.post(ROOT + "/appointments", headers=owner, json=body)
    assert result.status_code == status and "private" not in result.text
    assert counts(http_data[0]) == (1, 1, 0, 0)


@pytest.mark.parametrize("change", [
    "UPDATE customers SET name='Altered' WHERE id=1",
    "INSERT INTO customers(business_id,name,customer_type) VALUES (1,'Unexpected','REGISTERED')",
    """INSERT INTO customer_accounts(business_id,customer_id,username,password_hash)
       VALUES (1,2,'unexpected','unusable')""",
])
def test_development_lifespan_still_rejects_seed_or_account_changes(http_data, change):
    from starlette.testclient import TestClient
    from app import dev_data
    from app.main import app

    execute(http_data[0], "INSERT INTO customers(business_id,name,customer_type) VALUES (1,'Guest','GUEST')")
    execute(http_data[0], change)
    with pytest.raises(dev_data.DevelopmentDataError, match="DEVELOPMENT_DATA_INCONSISTENT"):
        with TestClient(app):
            pytest.fail("startup must fail closed")


@pytest.fixture
def booked_owner(client, owner, schedule_clock):
    response = client.post(ROOT + "/appointments", headers=owner, json=booking_body(client, owner))
    assert response.status_code == 201, response.text
    return response


def move(client, booked, headers, fields, token=None):
    return client.patch(booked.headers["location"], json=fields,
                        headers={**headers, "If-Match": token or booked.headers["etag"]})


def test_edit_if_match_and_csrf(client, owner, booked_owner):
    url = booked_owner.headers["location"]
    assert client.patch(url, headers={"Origin": owner["Origin"]}, json={}).status_code == 403
    assert client.patch(url, headers=owner, json={"staff_id": 2}).status_code == 428
    for invalid in ("*", 'W/"abc"', '"' + "a" * 64 + '", "other"'):
        assert move(client, booked_owner, owner, {"staff_id": 2}, token=invalid).status_code == 422
    for invalid in ({}, {"staff_id": None}, {"start_datetime": None}, {"notes": "no"},
                    {"start_datetime": "2027-01-04T11:00:00Z"}):
        assert move(client, booked_owner, owner, invalid).status_code == 422
    stale = move(client, booked_owner, owner, {"staff_id": 1}, token='"' + "0" * 64 + '"')
    assert stale.status_code == 412


def test_noop_and_rapid_a_to_b_to_a_moves(client, owner, booked_owner, http_data):
    url = booked_owner.headers["location"]
    original_tag = booked_owner.headers["etag"]
    assert move(client, booked_owner, owner, {"staff_id": 1}).headers["etag"] == original_tag
    execute(http_data[0], "UPDATE services SET name='Changed', default_duration_minutes=90, default_price=91 WHERE id=1")
    partial = move(client, booked_owner, owner, {"start_datetime": "2027-01-04T10:15:00"})
    assert partial.status_code == 200, partial.text
    assert partial.json()["end_datetime"] == "2027-01-04T11:00:00"
    restored = move(client, booked_owner, owner, {"start_datetime": SLOT["start_datetime"]}, token=partial.headers["etag"])
    assert restored.status_code == 200 and restored.json() == booked_owner.json()
    assert len({original_tag, partial.headers["etag"], restored.headers["etag"]}) == 3
    assert move(client, booked_owner, owner, {"staff_id": 1}).status_code == 412
    assert client.get(url).headers["etag"] == restored.headers["etag"]


@pytest.mark.parametrize("patch,field,expected", [
    ({"staff_id": 2}, "staff_id", 2),
    ({"start_datetime": "2027-01-05T10:00:00"}, "start_datetime", "2027-01-05T10:00:00"),
])
def test_partial_moves(client, owner, booked_owner, patch, field, expected):
    result = move(client, booked_owner, owner, patch)
    assert result.status_code == 200, result.text
    assert result.json()[field] == expected
    for key in ("customer_id", "notes", "items", "total_price", "total_duration_minutes", "status"):
        assert result.json()[key] == booked_owner.json()[key]


@pytest.mark.parametrize("change,code", [
    ("UPDATE appointments SET status='COMPLETED'", "APPOINTMENT_NOT_EDITABLE"),
    ("UPDATE appointments SET status='CANCELLED'", "APPOINTMENT_NOT_EDITABLE"),
    ("UPDATE appointments SET status='NO_SHOW'", "APPOINTMENT_NOT_EDITABLE"),
    ("UPDATE appointments SET start_datetime='2027-01-04T08:00:00'", "APPOINTMENT_NOT_EDITABLE"),
    ("UPDATE services SET is_active=0 WHERE id=1", "BOOKED_SERVICE_INACTIVE"),
    ("UPDATE staff SET is_active=0 WHERE id=2", "STAFF_NOT_FOUND"),
    ("UPDATE staff_services SET is_active=0 WHERE staff_id=2 AND service_id=1", "SERVICE_NOT_SUPPORTED"),
])
def test_ineligible_edit_preserves_visible_appointment(client, owner, booked_owner, http_data, change, code):
    execute(http_data[0], change)
    detail = client.get(booked_owner.headers["location"])
    assert detail.status_code == 200
    result = move(client, booked_owner, owner, {"staff_id": 2}, token=detail.headers["etag"])
    assert result.json() == {"error": {"code": code}}
    assert client.get(booked_owner.headers["location"]).json() == detail.json()


def test_edit_checks_other_appointments(client, owner, booked_owner, http_data):
    insert_appointment(http_data[0], "2027-01-04T11:00:00", "2027-01-04T11:45:00")
    conflict = move(client, booked_owner, owner, {"start_datetime": "2027-01-04T10:30:00"})
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "APPOINTMENT_UNAVAILABLE"


def test_edit_response_failure_rolls_back_schedule_and_revision(client, owner, booked_owner, monkeypatch):
    from app.api import owner_appointments

    def fail(*args, **kwargs):
        raise RuntimeError("private")

    with monkeypatch.context() as patch:
        patch.setattr(owner_appointments, "detail_response", fail)
        result = move(client, booked_owner, owner, {"staff_id": 2})
    assert result.status_code == 500 and "private" not in result.text
    detail = client.get(booked_owner.headers["location"])
    assert detail.json() == booked_owner.json()
    assert detail.headers["etag"] == booked_owner.headers["etag"]


def concurrently(*operations):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    ready = Barrier(len(operations))

    def run(operation):
        ready.wait(timeout=5)
        return operation()

    with ThreadPoolExecutor(max_workers=len(operations)) as pool:
        return list(pool.map(run, operations))


def test_concurrent_guest_phone_check_serializes_across_distinct_slots(client, owner, schedule_clock, http_data):
    guest = {"type": "NEW", "name": "Walk-in", "phone": "0501234567"}
    first = booking_body(client, owner, customer=guest)
    second = booking_body(client, owner, slot={**SLOT, "staff_id": 2}, customer=guest)

    def book(body):
        return client.post(ROOT + "/appointments", headers=owner, json=body)

    results = concurrently(lambda: book(first), lambda: book(second))
    assert sorted(result.status_code for result in results) == [201, 409]
    assert next(result.json() for result in results if result.status_code == 409) == {
        "error": {"code": "CUSTOMER_PHONE_ALREADY_EXISTS"}}
    assert counts(http_data[0]) == (2, 1, 1, 1)


def test_concurrent_owner_booking_has_one_winner(client, owner, schedule_clock, http_data):
    body = booking_body(client, owner)

    def book():
        return client.post(ROOT + "/appointments", headers=owner, json=body)

    assert sorted(result.status_code for result in concurrently(book, book)) == [201, 409]
    assert counts(http_data[0]) == (1, 1, 1, 1)


def test_concurrent_edits_from_one_revision_have_one_winner(client, owner, booked_owner):
    results = concurrently(
        lambda: move(client, booked_owner, owner, {"staff_id": 2}),
        lambda: move(client, booked_owner, owner, {"start_datetime": "2027-01-04T12:00:00"}),
    )
    assert sorted(result.status_code for result in results) == [200, 412]
    winner = next(result for result in results if result.status_code == 200)
    assert client.get(booked_owner.headers["location"]).headers["etag"] == winner.headers["etag"]


def test_competing_booking_and_edit_do_not_overlap(client, owner, booked_owner, http_data):
    body = booking_body(client, owner, slot={**SLOT, "staff_id": 2})
    booking, edit = concurrently(
        lambda: client.post(ROOT + "/appointments", headers=owner, json=body),
        lambda: move(client, booked_owner, owner, {"staff_id": 2}),
    )
    assert (booking.status_code, edit.status_code) in {(201, 409), (409, 200)}
    with closing(database.open_development_database(http_data[0])) as connection:
        assert connection.execute("SELECT count(*) FROM appointments WHERE staff_id=2").fetchone()[0] == 1


def test_foreign_customer_id_cannot_book_and_guest_phone_is_tenant_scoped(client, owner, schedule_clock, http_data):
    path = http_data[0]
    execute(path, "INSERT INTO businesses(id,business_name) VALUES (2,'Foreign')")
    execute(path, "INSERT INTO customers(id,business_id,name,phone,customer_type) VALUES (9,2,'Foreign','0501234567','GUEST')")
    body = booking_body(client, owner, customer={"type": "EXISTING", "customer_id": 9})
    assert client.post(ROOT + "/appointments", headers=owner, json=body).json() == {
        "error": {"code": "CUSTOMER_NOT_FOUND"}}
    body["customer"] = {"type": "NEW", "name": "Walk-in", "phone": "0501234567"}
    response = client.post(ROOT + "/appointments", headers=owner, json=body)
    assert response.status_code == 201
    assert response.json()["customer_id"] != 9


def test_customer_cannot_use_owner_mutations_and_still_sees_moved_booking(client, owner, booked_owner, http_data):
    edited = move(client, booked_owner, owner, {"staff_id": 2})
    assert edited.status_code == 200
    session = login(client, http_data[2]["CUSTOMER"])
    assert session.status_code == 200
    headers = {**owner, "X-CSRF-Token": session.json()["csrf_token"]}
    for suffix in ("/appointments", "/appointment-quotes"):
        assert client.post(ROOT + suffix, headers=headers, json={}).status_code == 403
    assert move(client, booked_owner, headers, {"staff_id": 1}).status_code == 403
    detail = client.get("/api/v1/customer/appointments/" + str(edited.json()["appointment_id"]))
    assert detail.status_code == 200
    assert detail.json()["staff_id"] == 2
    assert detail.json()["items"] == booked_owner.json()["items"]


@pytest.mark.parametrize("statement,start,code", [
    ("INSERT INTO schedule_breaks(staff_id,day_of_week,start_time,end_time) VALUES (1,1,'11:00','11:30')",
     "2027-01-04T11:00:00", "APPOINTMENT_UNAVAILABLE"),
    ("""INSERT INTO schedule_blocks(business_id,start_datetime,end_datetime)
        VALUES (1,'2027-01-04T11:00:00','2027-01-04T11:30:00')""",
     "2027-01-04T11:00:00", "APPOINTMENT_UNAVAILABLE"),
    ("UPDATE business_settings SET minimum_booking_notice_minutes=60",
     "2027-01-04T09:59:59", "MINIMUM_BOOKING_NOTICE_VIOLATION"),
    ("UPDATE business_settings SET booking_window_months=1",
     "2027-02-05T10:00:00", "BOOKING_WINDOW_EXCEEDED"),
])
def test_edit_obeys_destination_policy_and_schedule(client, owner, booked_owner, http_data, statement, start, code):
    execute(http_data[0], statement)
    result = move(client, booked_owner, owner, {"start_datetime": start})
    assert result.json() == {"error": {"code": code}}
    assert client.get(booked_owner.headers["location"]).headers["etag"] == booked_owner.headers["etag"]


def test_calendar_reads_one_batch_of_items_in_its_snapshot(client, owner, http_data, monkeypatch):
    ids = [
        insert_appointment(http_data[0], "2027-01-04T10:00:00", "2027-01-04T10:45:00"),
        insert_appointment(http_data[0], "2027-01-04T11:00:00", "2027-01-04T11:45:00"),
    ]
    original = repositories.get_owner_appointment_items
    batches = []

    def items(connection, business_id, appointment_ids):
        assert connection.in_transaction
        batches.append((business_id, appointment_ids))
        return original(connection, business_id, appointment_ids)

    monkeypatch.setattr(repositories, "get_owner_appointment_items", items)
    result = client.get(CALENDAR)
    assert result.status_code == 200
    assert batches == [(1, ids)]


def test_booking_options_rejects_oversized_catalog(client, owner, http_data):
    execute(http_data[0], """WITH RECURSIVE n(value) AS (
        SELECT 3 UNION ALL SELECT value + 1 FROM n WHERE value < 201
    ) INSERT INTO services(business_id,name,default_duration_minutes,default_price)
       SELECT 1,'Test service ' || value,45,80 FROM n""")
    response = client.get(ROOT + "/booking-options")
    assert response.status_code == 503
    assert response.json() == {"error": {"code": "BOOKING_OPTIONS_LIMIT_EXCEEDED"}}


def test_owner_rejects_ambiguous_duplicate_json_members(client, owner, schedule_clock, http_data):
    import json

    body = booking_body(client, owner, customer={"type": "NEW", "name": "Walk-in"})
    raw = json.dumps(body).replace('"type": "NEW"', '"type": "EXISTING", "type": "NEW"')
    result = client.post(ROOT + "/appointments", content=raw,
                         headers={**owner, "Content-Type": "application/json"})
    assert result.status_code == 422
    assert counts(http_data[0]) == (1, 1, 0, 0)


def test_exhausted_revision_is_configuration_error(client, owner, booked_owner, http_data):
    execute(http_data[0], "UPDATE appointments SET updated_at='9999-12-31 23:59:59.999999'")
    detail = client.get(booked_owner.headers["location"])
    result = move(client, booked_owner, owner, {"staff_id": 2}, token=detail.headers["etag"])
    assert result.status_code == 503
    assert result.json() == {"error": {"code": "BOOKING_CONFIGURATION_INVALID"}}
    assert client.get(booked_owner.headers["location"]).headers["etag"] == detail.headers["etag"]


@pytest.mark.parametrize("boundary", [
    "duplicate", "guest_insert", "confirmation", "availability", "appointment_insert",
    "item_insert", "etag", "serialize", "render_201", "success",
])
def test_guest_uses_one_write_transaction_at_every_boundary(
    client, owner, schedule_clock, http_data, monkeypatch, boundary,
):
    from app.api import owner_appointments as owner_api
    from app.services import owner_appointments as owner_service

    phone = "0501234567"
    body = booking_body(client, owner, customer={"type": "NEW", "name": "Guest", "phone": phone})
    path = http_data[0]
    if boundary == "duplicate":
        execute(path, "UPDATE customers SET phone=?", (phone,))
    elif boundary in ("guest_insert", "appointment_insert", "item_insert"):
        table = {"guest_insert": "customers", "appointment_insert": "appointments",
                 "item_insert": "appointment_items"}[boundary]
        execute(path, f"""CREATE TRIGGER audit_failure BEFORE INSERT ON {table}
            BEGIN SELECT RAISE(ABORT, 'audit sentinel'); END""")
    elif boundary == "confirmation":
        execute(path, "UPDATE services SET default_price=81 WHERE id=1")
    elif boundary == "availability":
        insert_appointment(path, "2027-01-04T10:00:00", "2027-01-04T10:45:00")

    before = counts(path)
    transactions = []
    open_database = database.open_development_database

    def traced(*args, **kwargs):
        connection = open_database(*args, **kwargs)
        connection.set_trace_callback(lambda sql: transactions.append(sql)
                                      if sql in {"BEGIN IMMEDIATE", "COMMIT", "ROLLBACK"} else None)
        return connection

    def fail(*args, **kwargs):
        assert transactions == ["BEGIN IMMEDIATE"]
        raise ValueError("audit sentinel")

    with monkeypatch.context() as patch:
        patch.setattr(database, "open_development_database", traced)
        if boundary == "etag":
            patch.setattr(owner_service, "appointment_etag", fail)
        elif boundary == "serialize":
            patch.setattr(owner_api.AppointmentDetail, "model_validate", fail)
        elif boundary == "render_201":
            def fail_render(value, *, status_code, headers):
                assert status_code == 201
                assert headers["Location"].startswith(ROOT + "/appointments/")
                assert headers["ETag"]
                fail()
            patch.setattr(owner_api, "JSONResponse", fail_render)
        result = client.post(ROOT + "/appointments", headers=owner, json=body)

    assert "audit sentinel" not in result.text
    if boundary == "success":
        assert result.status_code == 201
        assert transactions == ["BEGIN IMMEDIATE", "COMMIT"]
        assert counts(path) == (before[0] + 1, before[1], before[2] + 1, before[3] + 1)
    else:
        assert result.status_code == (409 if boundary in {"duplicate", "confirmation", "availability"} else 500)
        assert transactions == ["BEGIN IMMEDIATE", "ROLLBACK"]
        assert counts(path) == before


def test_existing_customer_rechecks_activity_inside_write_transaction(
    client, owner, schedule_clock, http_data, monkeypatch,
):
    from contextlib import contextmanager
    from app.services import appointments

    body = booking_body(client, owner)
    original = appointments.transaction

    @contextmanager
    def deactivate_after_preflight(connection):
        execute(http_data[0], "UPDATE customers SET is_active=0 WHERE id=1")
        with original(connection) as transaction_connection:
            yield transaction_connection

    monkeypatch.setattr(appointments, "transaction", deactivate_after_preflight)
    result = client.post(ROOT + "/appointments", headers=owner, json=body)
    assert result.status_code == 404
    assert result.json() == {"error": {"code": "CUSTOMER_NOT_FOUND"}}
    assert counts(http_data[0]) == (1, 1, 0, 0)


@pytest.mark.parametrize("revision", ["2027-01-04 09:00:00", "2027-01-04 09:00:00.123456"])
def test_edit_advances_utc_revision_at_fixed_clock(client, owner, booked_owner, http_data, revision):
    from datetime import datetime, timedelta

    execute(http_data[0], "UPDATE appointments SET updated_at=?", (revision,))
    detail = client.get(booked_owner.headers["location"])
    result = move(client, booked_owner, owner, {"staff_id": 2}, token=detail.headers["etag"])
    assert result.status_code == 200
    with closing(database.open_development_database(http_data[0])) as connection:
        stored, = connection.execute("SELECT updated_at FROM appointments").fetchone()
    assert stored == (datetime.fromisoformat(revision) + timedelta(microseconds=1)).isoformat(
        sep=" ", timespec="microseconds")


@pytest.mark.parametrize("revision", ["unparseable", "2027-01-04", "2027-01-04T09:00:00+02:00"])
def test_invalid_revision_fails_safely(client, owner, booked_owner, http_data, revision):
    execute(http_data[0], "UPDATE appointments SET updated_at=?", (revision,))
    url = booked_owner.headers["location"]
    for result in (client.get(url), move(client, booked_owner, owner, {"staff_id": 2})):
        assert result.status_code == 503 and "etag" not in result.headers
        assert result.json() == {"error": {"code": "BOOKING_CONFIGURATION_INVALID"}}


def test_owner_duplicate_query_and_if_match_are_rejected(client, owner, booked_owner):
    assert client.get(CALENDAR + "&limit=1&limit=2").status_code == 422
    tag = booked_owner.headers["etag"]
    headers = [*owner.items(), ("If-Match", tag), ("If-Match", tag)]
    result = client.patch(booked_owner.headers["location"], headers=headers, json={"staff_id": 2})
    assert result.status_code == 422


def test_noop_does_not_issue_update(client, owner, booked_owner, http_data):
    execute(http_data[0], """CREATE TRIGGER deny_update BEFORE UPDATE ON appointments
        BEGIN SELECT RAISE(ABORT, 'no writes expected'); END""")
    result = move(client, booked_owner, owner, {"staff_id": 1, "start_datetime": SLOT["start_datetime"]})
    assert result.status_code == 200
    assert result.headers["etag"] == booked_owner.headers["etag"]


@pytest.mark.parametrize("statement", [
    "UPDATE appointment_items SET duration_minutes=45.25",
    "UPDATE appointment_items SET price=80.001",
    "UPDATE appointment_items SET duration_minutes=40",
])
def test_invalid_booked_snapshot_cannot_move(client, owner, booked_owner, http_data, statement):
    execute(http_data[0], statement)
    detail = client.get(booked_owner.headers["location"])
    result = move(client, booked_owner, owner, {"staff_id": 2},
                  token=detail.headers.get("etag", booked_owner.headers["etag"]))
    assert result.status_code == 503
    assert result.json() == {"error": {"code": "BOOKING_CONFIGURATION_INVALID"}}
    with closing(database.open_development_database(http_data[0])) as connection:
        assert connection.execute("SELECT staff_id FROM appointments").fetchone()[0] == 1
