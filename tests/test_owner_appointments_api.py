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
