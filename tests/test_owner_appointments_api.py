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
