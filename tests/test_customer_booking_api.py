"""Customer HTTP integration uses the isolated provisioned auth test fixture."""

import pytest
from datetime import datetime, timezone, timedelta
from contextlib import closing
from app.db import database, repositories
from app.main import app
from app.services import appointments, alternatives, availability
from app.core.auth_sessions import InMemorySessionStore
from app.services.alternative_selection import InMemoryAlternativeSelectionContextStore
from app.core.booking_search_ownership import BookingSearchOwnership
from app.core.local_security import DEVELOPMENT_ORIGIN

from test_auth_api import client, execute, http_data, login


ROOT = "/api/v1/customer"


@pytest.fixture
def booking_customer(client, http_data, monkeypatch):
    clock = [datetime(2027, 1, 4, 9)]

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].replace(tzinfo=timezone.utc) if tz else clock[0]

    for module in (appointments, alternatives, availability):
        monkeypatch.setattr(module, "datetime", FrozenDatetime)
    app.state.auth_sessions = InMemorySessionStore(lambda: clock[0].replace(tzinfo=timezone.utc))
    app.state.booking_contexts = InMemoryAlternativeSelectionContextStore(lambda: clock[0].replace(tzinfo=timezone.utc))
    app.state.booking_searches = BookingSearchOwnership(app.state.booking_contexts, lambda: clock[0].replace(tzinfo=timezone.utc))
    response = login(client, http_data[2]["CUSTOMER"])
    assert response.status_code == 200
    return {"Origin": DEVELOPMENT_ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]}, clock


def search_body(**changes):
    return {"service_ids": [1], "staff_selection": {"type": "SPECIFIC", "staff_id": 1},
            "start_datetime": "2027-01-04T10:00:00", **changes}


def occupy(http_data):
    with closing(database.open_development_database(http_data[0])) as conn:
        with database.transaction(conn):
            repositories.create_appointment(conn, 1, 1, 1, "2027-01-04T10:00:00", "2027-01-04T10:45:00",
                items=[{"service_id": 1, "service_name": "Haircut", "duration_minutes": 45, "price": 80.0}])


def confirmation(quote):
    return {"items": [{k: item[k] for k in ("service_id", "duration_minutes", "price")} for item in quote["items"]],
            "total_price": quote["total_price"], "total_duration_minutes": quote["total_duration_minutes"], "currency": "ILS"}


def preview(client, headers, body=None):
    response = client.post(ROOT + "/booking-searches", json=body or search_body(), headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def counts(path):
    with closing(database.open_development_database(path)) as conn:
        return tuple(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in ("appointments", "appointment_items"))


def test_catalog_is_customer_only_and_safe(client, http_data):
    assert client.get(ROOT + "/services").status_code == 401
    assert login(client, http_data[2]["OWNER"]).status_code == 200
    assert client.get(ROOT + "/services").status_code == 403
    assert login(client, http_data[2]["CUSTOMER"]).status_code == 200
    response = client.get(ROOT + "/services")
    assert response.status_code == 200
    assert response.json()["services"][0]["default_price"] == "80.00"
    assert response.headers["cache-control"] == "no-store"
    staff = client.get(ROOT + "/staff?service_id=2&service_id=1")
    assert staff.status_code == 200
    assert staff.json()["service_ids"] == [2, 1]
    assert len(staff.json()["staff"]) == 2
    assert staff.json()["staff"][0]["total_price"] == "120.00"
    assert [item["service_id"] for item in staff.json()["staff"][0]["items"]] == [2, 1]
    assert not any(field in staff.text for field in ("phone", "email", "user_id", "password"))


@pytest.mark.parametrize("query,status", [("", 422), ("?service_id=true", 422), ("?service_id=1&service_id=1", 422),
                                              ("?service_id=999", 404), ("?service_id=1&business_id=2", 422)])
def test_staff_selection_validation(client, http_data, query, status):
    login(client, http_data[2]["CUSTOMER"])
    assert client.get(ROOT + "/staff" + query).status_code == status


def test_catalog_scope_overrides_and_compatibility(client, http_data):
    login(client, http_data[2]["CUSTOMER"])
    path = http_data[0]
    execute(path, "INSERT INTO businesses (id,business_name) VALUES (2,'Foreign')")
    execute(path, "INSERT INTO services (id,business_id,name,default_duration_minutes,default_price) VALUES (99,2,'Foreign',30,1)")
    execute(path, "UPDATE staff_services SET price=90,duration_minutes=60 WHERE staff_id=1 AND service_id=1")
    execute(path, "UPDATE staff_services SET is_active=0 WHERE staff_id=2 AND service_id=2")
    result = client.get(ROOT + "/staff?service_id=1&service_id=2").json()["staff"]
    assert len(result) == 1
    assert result[0]["total_price"] == "130.00" and result[0]["total_duration_minutes"] == 80
    assert client.get(ROOT + "/staff?service_id=99").status_code == 404
    assert "Foreign" not in client.get(ROOT + "/services").text


def test_invalid_catalog_precision_is_configuration_error(client, http_data):
    login(client, http_data[2]["CUSTOMER"])
    execute(http_data[0], "UPDATE services SET default_price=1.005 WHERE id=1")
    response = client.get(ROOT + "/services")
    assert response.status_code == 503
    assert response.json() == {"error": {"code": "BOOKING_CONFIGURATION_INVALID"}}


def test_requested_slot_and_any_preview(client, booking_customer):
    headers, _ = booking_customer
    response = client.post(ROOT + "/booking-searches", json=search_body(), headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["requested_availability"]["quote"]["total_price"] == "80.00"
    assert response.json()["alternatives"] is None
    response = client.post(ROOT + "/booking-searches", json=search_body(staff_selection={"type": "ANY", "mode": "CHOICE"}), headers=headers)
    assert response.status_code == 200 and response.json()["requested_availability"]["quote"]["staff_id"] == 1
    response = client.post(ROOT + "/booking-searches", json=search_body(staff_selection={"type": "ANY", "mode": "CHOICE"}, include_alternatives=True), headers=headers)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "ANY_STAFF_DISCOVERY_UNSUPPORTED"


def test_discovery_publishes_owned_context_and_final_options(client, http_data, booking_customer):
    headers, _ = booking_customer
    occupy(http_data)
    response = client.post(ROOT + "/booking-searches", json=search_body(include_alternatives=True), headers=headers)
    assert response.status_code == 200, response.text
    value = response.json()["alternatives"]
    assert value["search_id"] and "+00:00" in value["expires_at"]
    assert value["total_available"] == len(value["options"]) and not value["has_more"]
    assert value["options"][0]["option_id"] == "option_001"
    context = app.state.booking_contexts.get_search_context(value["search_id"])
    assert context.expires_at - context.created_at == timedelta(minutes=15)
    assert len(app.state.booking_searches._records) == 1
    assert not app.state.booking_searches._pending
    for item in value["options"]:
        stored = app.state.booking_contexts.resolve_selection(value["search_id"], item["option_id"])
        assert item["staff_id"] == stored.staff_id
        assert item["start_datetime"] == stored.start_datetime.isoformat()


@pytest.mark.parametrize("bad", ["csrf", "owner", "clock"])
def test_search_security_and_clock(client, http_data, booking_customer, monkeypatch, bad):
    headers, _ = booking_customer
    status = 403
    if bad == "csrf":
        headers = {"Origin": DEVELOPMENT_ORIGIN}
    elif bad == "owner":
        response = login(client, http_data[2]["OWNER"])
        headers = {"Origin": DEVELOPMENT_ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]}
    else:
        monkeypatch.setenv("TZ", "UTC")
        status = 503
    response = client.post(ROOT + "/booking-searches", json=search_body(), headers=headers)
    assert response.status_code == status
    assert not app.state.booking_contexts._contexts


def test_discovery_failure_cleans_new_context(client, http_data, booking_customer, monkeypatch):
    headers, _ = booking_customer
    occupy(http_data)
    execute(http_data[0], "UPDATE staff_services SET price=1.005 WHERE staff_id=2")
    response = client.post(ROOT + "/booking-searches", json=search_body(include_alternatives=True), headers=headers)
    assert response.status_code == 503
    assert not app.state.booking_contexts._contexts and not app.state.booking_searches._pending


def test_direct_booking_persists_confirmed_snapshots_and_conflict(client, http_data, booking_customer):
    headers, _ = booking_customer
    request = search_body(service_ids=[2, 1])
    quote = preview(client, headers, request)["requested_availability"]["quote"]
    body = {**request, "confirmation": confirmation(quote), "notes": "Synthetic booking"}
    response = client.post(ROOT + "/appointments", json=body, headers=headers)
    assert response.status_code == 201, response.text
    value = response.json()
    assert value["total_price"] == "120.00" and value["end_datetime"] == "2027-01-04T11:05:00"
    assert response.headers["location"] == ROOT + f"/appointments/{value['appointment_id']}"
    assert counts(http_data[0]) == (1, 2)
    with closing(database.open_development_database(http_data[0])) as conn:
        items = list(conn.execute("SELECT service_id,service_name,duration_minutes,price FROM appointment_items ORDER BY id"))
        assert [tuple(row) for row in items] == [(2, "Beard trim", 20, 40), (1, "Haircut", 45, 80)]
    assert client.post(ROOT + "/appointments", json=body, headers=headers).status_code == 409
    assert counts(http_data[0]) == (1, 2)


@pytest.mark.parametrize("change", ["higher", "lower", "duration", "total", "order", "allocation"])
def test_changed_quote_never_inserts(client, http_data, booking_customer, change):
    headers, _ = booking_customer
    request = search_body(service_ids=[1, 2])
    expected = confirmation(preview(client, headers, request)["requested_availability"]["quote"])
    if change in ("higher", "lower"):
        execute(http_data[0], "UPDATE services SET default_price=? WHERE id=1", (90 if change == "higher" else 70,))
    elif change == "duration":
        execute(http_data[0], "UPDATE services SET default_duration_minutes=50 WHERE id=1")
    elif change == "allocation":
        execute(http_data[0], "UPDATE services SET default_price=default_price+CASE WHEN id=1 THEN 10 ELSE -10 END")
    elif change == "total":
        expected["total_price"] = "119.00"
    else:
        expected["items"].reverse()
    response = client.post(ROOT + "/appointments", json={**request, "confirmation": expected}, headers=headers)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "BOOKING_QUOTE_CHANGED"
    assert counts(http_data[0]) == (0, 0)


@pytest.mark.parametrize("price,status", [(90, 409), (1.005, 503)])
def test_configuration_race_before_write_transaction(client, http_data, booking_customer, monkeypatch, price, status):
    from contextlib import contextmanager
    headers, _ = booking_customer
    expected = confirmation(preview(client, headers)["requested_availability"]["quote"])
    original = appointments.transaction
    statements = []

    @contextmanager
    def change_before_lock(conn):
        conn.set_trace_callback(statements.append)
        execute(http_data[0], "UPDATE services SET default_price=? WHERE id=1", (price,))
        with original(conn):
            yield conn

    monkeypatch.setattr(appointments, "transaction", change_before_lock)
    response = client.post(ROOT + "/appointments", json={**search_body(), "confirmation": expected}, headers=headers)
    assert response.status_code == status
    assert counts(http_data[0]) == (0, 0)
    assert not any(s.lstrip().upper().startswith("INSERT") for s in statements)


def test_item_insert_failure_rolls_back_whole_booking(client, http_data, booking_customer, monkeypatch):
    headers, _ = booking_customer
    expected = confirmation(preview(client, headers)["requested_availability"]["quote"])
    original = appointments.create_appointment

    def fail_after_insert(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("AUDIT_ONLY_PRIVATE_ERROR")

    monkeypatch.setattr(appointments, "create_appointment", fail_after_insert)
    response = client.post(ROOT + "/appointments", json={**search_body(), "confirmation": expected}, headers=headers)
    assert response.status_code == 500 and "AUDIT_ONLY_PRIVATE_ERROR" not in response.text
    assert counts(http_data[0]) == (0, 0)


def test_selected_booking_success_expires_search_and_replay_is_rejected(client, http_data, booking_customer):
    headers, _ = booking_customer
    occupy(http_data)
    found = preview(client, headers, search_body(include_alternatives=True))["alternatives"]
    selected = found["options"][0]
    url = ROOT + f"/booking-searches/{found['search_id']}/selection"
    body = {"option_id": selected["option_id"], "confirmation": confirmation(selected)}
    response = client.post(url, json=body, headers=headers)
    assert response.status_code == 201, response.text
    assert response.json()["start_datetime"] == selected["start_datetime"]
    assert not app.state.booking_contexts._contexts and not app.state.booking_searches._records
    assert client.post(url, json=body, headers=headers).status_code == 410
    assert counts(http_data[0]) == (2, 2)


def test_failed_selected_confirmation_releases_claim_and_allows_retry(client, http_data, booking_customer):
    headers, _ = booking_customer
    occupy(http_data)
    found = preview(client, headers, search_body(include_alternatives=True))["alternatives"]
    option = found["options"][0]
    expected = confirmation(option)
    url = ROOT + f"/booking-searches/{found['search_id']}/selection"
    expected["total_price"] = "1.00"
    bad = client.post(url, json={"option_id": option["option_id"], "confirmation": expected}, headers=headers)
    assert bad.status_code == 409 and bad.json()["error"]["code"] == "BOOKING_QUOTE_CHANGED"
    assert not app.state.booking_contexts._claimed_search_ids
    assert counts(http_data[0]) == (1, 1)
    assert client.post(url, json={"option_id": option["option_id"], "confirmation": confirmation(option)}, headers=headers).status_code == 201


def test_other_session_and_expired_search_fail_before_claim(client, http_data, booking_customer, monkeypatch):
    headers, clock = booking_customer
    occupy(http_data)
    found = preview(client, headers, search_body(include_alternatives=True))["alternatives"]
    option = found["options"][0]
    url = ROOT + f"/booking-searches/{found['search_id']}/selection"
    body = {"option_id": option["option_id"], "confirmation": confirmation(option)}
    monkeypatch.setattr(app.state.booking_contexts, "claim_selection", lambda *a: pytest.fail("must authorize first"))
    new = login(client, http_data[2]["CUSTOMER"])
    new_headers = {"Origin": DEVELOPMENT_ORIGIN, "X-CSRF-Token": new.json()["csrf_token"]}
    assert client.post(url, json=body, headers=new_headers).status_code == 410
    clock[0] += timedelta(minutes=15)
    assert client.post(url, json=body, headers=new_headers).status_code == 410
    clock[0] += timedelta(hours=1)
    assert client.post(url, json=body, headers=new_headers).status_code == 401


def test_competing_direct_bookings_share_real_final_conflict_protection(client, http_data, booking_customer, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    headers, _ = booking_customer
    expected = confirmation(preview(client, headers)["requested_availability"]["quote"])
    barrier = Barrier(2)
    original = appointments.AppointmentService._ensure_available

    def synchronized(**kwargs):
        original(**kwargs)
        if kwargs.get("expected_end") is None:
            barrier.wait(timeout=5)

    monkeypatch.setattr(appointments.AppointmentService, "_ensure_available", staticmethod(synchronized))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: client.post(ROOT + "/appointments", json={**search_body(), "confirmation": expected}, headers=headers), range(2)))
    assert sorted(r.status_code for r in results) == [201, 409]
    assert counts(http_data[0]) == (1, 1)
