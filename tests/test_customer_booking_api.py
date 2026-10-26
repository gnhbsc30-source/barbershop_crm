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
    from app.core import booking_clock
    # Clock readiness itself has real isolated-process tests. HTTP tests should
    # not depend on the developer/CI machine's timezone.
    monkeypatch.setattr(booking_clock, "scheduling_clock_ready", lambda zone: zone == "Asia/Jerusalem")
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
        from app.core import booking_clock
        monkeypatch.setattr(booking_clock, "scheduling_clock_ready", lambda zone: False)
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


def test_history_snapshots_status_and_keyset_pagination(client, http_data, booking_customer):
    headers, _ = booking_customer
    for hour in (10, 11, 12):
        request = search_body(start_datetime=f"2027-01-04T{hour}:00:00")
        expected = confirmation(preview(client, headers, request)["requested_availability"]["quote"])
        assert client.post(ROOT + "/appointments", json={**request, "confirmation": expected}, headers=headers).status_code == 201
    execute(http_data[0], "UPDATE appointments SET status='CANCELLED' WHERE id=2")
    execute(http_data[0], "UPDATE services SET name='Changed',default_price=999,is_active=0 WHERE id=1")
    first = client.get(ROOT + "/appointments?limit=2")
    assert first.status_code == 200
    assert [a["appointment_id"] for a in first.json()["appointments"]] == [3, 2]
    assert first.json()["has_more"] and first.json()["next_before_id"] == 2
    second = client.get(ROOT + "/appointments?limit=2&before_id=2").json()
    assert [a["appointment_id"] for a in second["appointments"]] == [1]
    assert not second["has_more"] and second["next_before_id"] is None
    cancelled = client.get(ROOT + "/appointments?status=CANCELLED").json()["appointments"]
    assert len(cancelled) == 1 and cancelled[0]["appointment_id"] == 2
    detail = client.get(ROOT + "/appointments/1").json()
    assert detail["items"][0]["service_name"] == "Haircut"
    assert detail["total_price"] == "80.00"


def test_account_id_is_not_customer_id_in_booking_and_history(client, http_data, booking_customer):
    _, _ = booking_customer
    path = http_data[0]
    execute(path, "INSERT INTO customers (id,business_id,name,customer_type) VALUES (10,1,'Linked','REGISTERED')")
    execute(path, "UPDATE customer_accounts SET customer_id=10 WHERE id=1")
    result = login(client, http_data[2]["CUSTOMER"])
    assert result.json()["principal"]["subject"] == "customer_account:1"
    assert result.json()["principal"]["customer_id"] == 10
    headers = {"Origin": DEVELOPMENT_ORIGIN, "X-CSRF-Token": result.json()["csrf_token"]}
    occupy(http_data)  # Customer 1's appointment must not appear for customer 10.
    request = search_body(start_datetime="2027-01-04T12:00:00")
    expected = confirmation(preview(client, headers, request)["requested_availability"]["quote"])
    booked = client.post(ROOT + "/appointments", json={**request, "confirmation": expected}, headers=headers)
    assert booked.status_code == 201
    own_id = booked.json()["appointment_id"]
    with closing(database.open_development_database(path)) as conn:
        assert conn.execute("SELECT customer_id FROM appointments WHERE id=?", (own_id,)).fetchone()[0] == 10
    assert [row["appointment_id"] for row in client.get(ROOT + "/appointments").json()["appointments"]] == [own_id]
    assert client.get(ROOT + "/appointments/1").status_code == 404
    assert client.get(ROOT + "/appointments/999").status_code == 404


@pytest.mark.parametrize("query", ["?limit=0", "?limit=101", "?limit=true", "?limit=1&limit=2", "?before_id=-1", "?status=OTHER", "?customer_id=2", "?before_id=" + "9" * 100])
def test_history_query_bounds(client, booking_customer, query):
    assert client.get(ROOT + "/appointments" + query).status_code == 422


def test_history_uses_two_queries_and_one_consistent_snapshot(client, http_data, booking_customer, monkeypatch):
    headers, _ = booking_customer
    occupy(http_data)
    execute(http_data[0], "PRAGMA journal_mode=WAL")
    original = repositories.get_customer_appointment_rows
    actual_items = repositories.get_customer_appointment_items
    item_calls = []

    def race(conn, *args, **kwargs):
        assert conn.in_transaction
        rows = original(conn, *args, **kwargs)
        execute(http_data[0], "UPDATE appointment_items SET price=90 WHERE appointment_id=1")
        return rows

    def tracked(conn, *args, **kwargs):
        assert conn.in_transaction
        item_calls.append(True)
        return actual_items(conn, *args, **kwargs)

    monkeypatch.setattr(repositories, "get_customer_appointment_rows", race)
    monkeypatch.setattr(repositories, "get_customer_appointment_items", tracked)
    response = client.get(ROOT + "/appointments")
    assert response.status_code == 200
    assert response.json()["appointments"][0]["total_price"] == "80.00"
    assert len(item_calls) == 1
    monkeypatch.setattr(repositories, "get_customer_appointment_rows", original)
    assert client.get(ROOT + "/appointments/1").json()["total_price"] == "90.00"


def static_confirmation():
    return {"items": [{"service_id": 1, "duration_minutes": 45, "price": "80.00"}],
            "total_duration_minutes": 45, "total_price": "80.00", "currency": "ILS"}


CUSTOMER_ROUTES = [
    ("GET", "/services"), ("GET", "/staff?service_id=1"), ("GET", "/appointments"), ("GET", "/appointments/1"),
    ("POST", "/booking-searches"), ("POST", "/appointments"), ("POST", "/booking-searches/unknown/selection"),
]


def route_body(path):
    if path.endswith("/selection"):
        return {"option_id": "option_001", "confirmation": static_confirmation()}
    if path == "/appointments":
        return {**search_body(), "confirmation": static_confirmation()}
    return search_body()


@pytest.mark.parametrize("method,path", CUSTOMER_ROUTES)
def test_owner_is_rejected_by_every_customer_route(client, http_data, booking_customer, method, path):
    response = login(client, http_data[2]["OWNER"])
    headers = {"Origin": DEVELOPMENT_ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]}
    result = client.request(method, ROOT + path, headers=headers, **({"json": route_body(path)} if method == "POST" else {}))
    assert result.status_code == 403
    assert not app.state.booking_contexts._contexts


@pytest.mark.parametrize("path", ["/booking-searches", "/appointments", "/booking-searches/unknown/selection"])
@pytest.mark.parametrize("failure", ["missing", "wrong", "duplicate", "origin"])
def test_every_customer_post_requires_origin_and_session_csrf(client, booking_customer, path, failure):
    headers, _ = booking_customer
    if failure == "missing":
        headers = {"Origin": DEVELOPMENT_ORIGIN}
    elif failure == "wrong":
        headers = {**headers, "X-CSRF-Token": "wrong"}
    elif failure == "duplicate":
        headers = [("Origin", DEVELOPMENT_ORIGIN), ("X-CSRF-Token", headers["X-CSRF-Token"]), ("X-CSRF-Token", headers["X-CSRF-Token"])]
    else:
        headers = {"X-CSRF-Token": headers["X-CSRF-Token"]}
    response = client.post(ROOT + path, json=route_body(path), headers=headers)
    assert response.status_code == 403
    assert not app.state.booking_contexts._contexts


@pytest.mark.parametrize("change", [{"business_id": 2}, {"customer_id": 2}, {"end_datetime": "2027-01-04T10:45:00"},
                                    {"staff_selection": {"type": "ANY", "mode": "AUTO"}}, {"confirmation": None},
                                    {"confirmation": {**static_confirmation(), "currency": "USD"}}])
def test_booking_rejects_identity_overrides_and_missing_confirmation(client, booking_customer, change):
    headers, _ = booking_customer
    response = client.post(ROOT + "/appointments", json={**search_body(), "confirmation": static_confirmation(), **change}, headers=headers)
    assert response.status_code == 422


def test_customer_body_bound_content_type_and_no_redirect(client, booking_customer):
    headers, _ = booking_customer
    response = client.post(ROOT + "/booking-searches", content="x" * 17000, headers={**headers, "Content-Type": "application/json"})
    assert response.status_code == 413
    assert client.post(ROOT + "/booking-searches", content="{}", headers=headers).status_code == 415
    response = client.post(ROOT + "/appointments/?access_code=AUDIT_ONLY_VALUE", json={}, headers=headers, follow_redirects=False)
    assert response.status_code == 404 and "location" not in response.headers
    assert "AUDIT_ONLY_VALUE" not in response.text
    wrong_method = client.delete(ROOT + "/appointments", headers=headers)
    assert wrong_method.status_code == 405 and "POST" in wrong_method.headers["allow"]


def test_http_totals_sum_item_cents_and_history_preserves_them(client, http_data, booking_customer):
    headers, _ = booking_customer
    execute(http_data[0], "UPDATE services SET default_price=CASE WHEN id=1 THEN 0.1 ELSE 0.2 END")
    request = search_body(service_ids=[1, 2])
    quote = preview(client, headers, request)["requested_availability"]["quote"]
    assert quote["total_price"] == "0.30"
    response = client.post(ROOT + "/appointments", json={**request, "confirmation": confirmation(quote)}, headers=headers)
    assert response.status_code == 201 and response.json()["total_price"] == "0.30"
    assert client.get(ROOT + "/appointments/1").json()["total_price"] == "0.30"


@pytest.mark.parametrize("offset,status", [(timedelta(minutes=15) - timedelta(microseconds=1), 404),
                                          (timedelta(minutes=15), 410), (timedelta(minutes=15, microseconds=1), 410)])
def test_owned_search_exact_ttl_boundary(client, http_data, booking_customer, offset, status):
    headers, clock = booking_customer
    occupy(http_data)
    found = preview(client, headers, search_body(include_alternatives=True))["alternatives"]
    clock[0] += offset
    response = client.post(ROOT + f"/booking-searches/{found['search_id']}/selection",
        json={"option_id": "missing", "confirmation": static_confirmation()}, headers=headers)
    assert response.status_code == status
    assert counts(http_data[0]) == (1, 1)


def test_logout_makes_owned_search_unusable(client, http_data, booking_customer):
    headers, _ = booking_customer
    occupy(http_data)
    found = preview(client, headers, search_body(include_alternatives=True))["alternatives"]
    assert client.delete("/api/v1/auth/session", headers=headers).status_code == 204
    assert client.post(ROOT + f"/booking-searches/{found['search_id']}/selection", json={"option_id": "option_001", "confirmation": static_confirmation()}, headers=headers).status_code == 401


def test_search_response_validation_failure_cleans_context(client, http_data, booking_customer, monkeypatch):
    from app.services.customer_booking import CustomerBooking
    headers, _ = booking_customer
    occupy(http_data)
    original = CustomerBooking.search

    def invalid(*args, **kwargs):
        value = original(*args, **kwargs)
        value["unexpected"] = True
        return value

    monkeypatch.setattr(CustomerBooking, "search", invalid)
    response = client.post(ROOT + "/booking-searches", json=search_body(include_alternatives=True), headers=headers)
    assert response.status_code == 503
    assert not app.state.booking_contexts._contexts and not app.state.booking_searches._records
    assert not app.state.booking_searches._pending


def test_only_one_of_two_options_reaches_book_over_http(client, http_data, booking_customer, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    headers, _ = booking_customer
    occupy(http_data)
    found = preview(client, headers, search_body(include_alternatives=True))["alternatives"]
    options = [found["options"][0], found["options"][-1]]
    entered, release = Event(), Event()
    calls = []
    original = appointments.AppointmentService.book

    def blocked(*args, **kwargs):
        calls.append(True)
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(appointments.AppointmentService, "book", blocked)
    url = ROOT + f"/booking-searches/{found['search_id']}/selection"
    def choose(option):
        return client.post(url, json={"option_id": option["option_id"], "confirmation": confirmation(option)}, headers=headers)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(choose, options[0])
        assert entered.wait(5)
        try:
            second = choose(options[1])
            assert second.status_code == 409 and second.json()["error"]["code"] == "ALTERNATIVE_SELECTION_IN_PROGRESS"
            assert len(calls) == 1
        finally:
            release.set()
        assert first.result(5).status_code == 201
    assert counts(http_data[0]) == (2, 2)


@pytest.mark.parametrize("start,notice,months,status,code", [
    ("2027-01-04T08:59:59", 60, 3, 422, "PAST_DATETIME"),
    ("2027-01-04T09:59:59", 60, 3, 422, "MINIMUM_BOOKING_NOTICE_VIOLATION"),
    ("2027-01-04T10:00:00", 60, 3, 200, None),
    ("2027-02-04T09:00:00", 0, 1, 200, None),
    ("2027-02-04T09:00:00.000001", 0, 1, 422, "BOOKING_WINDOW_EXCEEDED"),
    ("2027-02-30T10:00:00", 0, 3, 422, "INVALID_DATETIME"),
    ("2027-01-04T10:00:00Z", 0, 3, 422, "INVALID_DATETIME"),
])
def test_customer_policy_and_datetime_boundaries(client, http_data, booking_customer, start, notice, months, status, code):
    headers, _ = booking_customer
    execute(http_data[0], "UPDATE business_settings SET minimum_booking_notice_minutes=?,booking_window_months=?", (notice, months))
    result = client.post(ROOT + "/booking-searches", json=search_body(start_datetime=start), headers=headers)
    assert result.status_code == status, result.text
    if code:
        assert result.json()["error"]["code"] == code
    assert counts(http_data[0]) == (0, 0)


def test_openapi_describes_bounded_request_and_response_models():
    schema = app.openapi()
    for path in ("/booking-searches", "/appointments", "/booking-searches/{search_id}/selection"):
        operation = schema["paths"][ROOT + path]["post"]
        body = operation["requestBody"]["content"]["application/json"]["schema"]
        assert body["additionalProperties"] is False
        assert "confirmation" in body["required"] if path != "/booking-searches" else "service_ids" in body["required"]


@pytest.mark.parametrize("tenant", [1, 2])
def test_other_customer_or_tenant_cannot_select_search_or_read_appointments(client, http_data, booking_customer, monkeypatch, tenant):
    from types import MappingProxyType
    from starlette.testclient import TestClient
    from app.services.authentication import IdentityReference, hash_access_secret
    headers, _ = booking_customer
    occupy(http_data)
    found = preview(client, headers, search_body(include_alternatives=True))["alternatives"]
    path = http_data[0]
    if tenant == 2:
        execute(path, "INSERT INTO businesses (id,business_name) VALUES (2,'Foreign')")
        execute(path, "INSERT INTO services (id,business_id,name,default_duration_minutes,default_price) VALUES (99,2,'Foreign',20,10)")
    execute(path, "INSERT INTO customers (id,business_id,name,customer_type) VALUES (20,?,'Other','REGISTERED')", (tenant,))
    selector = "b" * 16
    execute(path, "INSERT INTO customer_accounts (id,business_id,customer_id,username,password_hash) VALUES (2,?,20,?,?)",
            (tenant, selector, hash_access_secret("s" * 43)))
    app.state.auth_identities = MappingProxyType({**app.state.auth_identities, selector: IdentityReference("customer_account", tenant, 2)})
    monkeypatch.setattr(app.state.booking_contexts, "claim_selection", lambda *a: pytest.fail("foreign owner reached claim"))
    with_other = TestClient(app, base_url=DEVELOPMENT_ORIGIN, client=("127.0.0.1", 50001))
    try:
        result = login(with_other, selector + "." + "s" * 43)
        assert result.status_code == 200
        other_headers = {"Origin": DEVELOPMENT_ORIGIN, "X-CSRF-Token": result.json()["csrf_token"]}
        option = found["options"][0]
        response = with_other.post(ROOT + f"/booking-searches/{found['search_id']}/selection",
            json={"option_id": option["option_id"], "confirmation": confirmation(option)}, headers=other_headers)
        assert response.status_code == 410
        assert with_other.get(ROOT + "/appointments/1").status_code == 404
        assert with_other.get(ROOT + "/appointments").json()["appointments"] == []
        if tenant == 2:
            assert with_other.get(ROOT + "/staff?service_id=1").status_code == 404
            assert [s["service_id"] for s in with_other.get(ROOT + "/services").json()["services"]] == [99]
        assert counts(path) == (1, 1)
    finally:
        with_other.close()


def test_confirmed_transaction_persists_fresh_snapshot_and_identical_interval(client, http_data, booking_customer, monkeypatch):
    from contextlib import contextmanager
    headers, _ = booking_customer
    expected = static_confirmation()
    expected["items"][0].update(duration_minutes=60, price="90.00")
    expected.update(total_duration_minutes=60, total_price="90.00")
    original_transaction = appointments.transaction
    original_available = appointments.AppointmentService._ensure_available
    checked = []

    @contextmanager
    def update_before_lock(conn):
        execute(http_data[0], "UPDATE services SET name='Fresh snapshot',default_duration_minutes=60,default_price=90 WHERE id=1")
        with original_transaction(conn):
            yield conn

    def check(**kwargs):
        if kwargs.get("expected_end") is not None:
            assert kwargs["connection"].in_transaction
            checked.append(kwargs["expected_end"].isoformat())
        return original_available(**kwargs)

    monkeypatch.setattr(appointments, "transaction", update_before_lock)
    monkeypatch.setattr(appointments.AppointmentService, "_ensure_available", staticmethod(check))
    response = client.post(ROOT + "/appointments", json={**search_body(), "confirmation": expected}, headers=headers)
    assert response.status_code == 201, response.text
    value = response.json()
    assert value["end_datetime"] == checked[0] == "2027-01-04T11:00:00"
    assert value["items"][0]["service_name"] == "Fresh snapshot"
    assert value["items"][0]["price"] == "90.00" and value["items"][0]["duration_minutes"] == 60
    with closing(database.open_development_database(http_data[0])) as conn:
        assert conn.execute("SELECT end_datetime FROM appointments").fetchone()[0] == checked[0]


def test_actual_item_insert_error_rolls_back_parent(client, http_data, booking_customer, monkeypatch):
    import sqlite3
    headers, _ = booking_customer
    expected = confirmation(preview(client, headers)["requested_availability"]["quote"])
    original = database.open_development_database

    def deny_items(*args, **kwargs):
        conn = original(*args, **kwargs)
        conn.set_authorizer(lambda action, table, *rest: sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_INSERT and table == "appointment_items" else sqlite3.SQLITE_OK)
        return conn

    monkeypatch.setattr(database, "open_development_database", deny_items)
    result = client.post(ROOT + "/appointments", json={**search_body(), "confirmation": expected}, headers=headers)
    assert result.status_code == 500
    assert counts(http_data[0]) == (0, 0)


@pytest.mark.parametrize("eligible", [True, False])
def test_incompatible_requested_staff_can_discover_others_or_empty(client, http_data, booking_customer, eligible):
    headers, _ = booking_customer
    execute(http_data[0], "UPDATE staff_services SET is_active=0 WHERE staff_id=1")
    if not eligible:
        execute(http_data[0], "UPDATE staff_services SET is_active=0")
    result = preview(client, headers, search_body(include_alternatives=True))
    assert not result["requested_availability"]["available"]
    found = result["alternatives"]
    if eligible:
        assert found["search_id"] and all(option["staff_id"] == 2 for option in found["options"])
        assert {option["priority_group"] for option in found["options"]} == {2, 4}
    else:
        assert found["search_id"] is None and found["options"] == []
        assert not app.state.booking_contexts._contexts and not app.state.booking_searches._records


def test_http_search_admission_is_bounded_without_eviction(client, http_data, booking_customer):
    headers, _ = booking_customer
    occupy(http_data)
    searches = [preview(client, headers, search_body(include_alternatives=True))["alternatives"] for _ in range(5)]
    first = searches[0]
    app.state.booking_contexts.claim_selection(first["search_id"], first["options"][0]["option_id"])
    denied = client.post(ROOT + "/booking-searches", json=search_body(include_alternatives=True), headers=headers)
    assert denied.status_code == 429 and denied.headers["retry-after"] == "1"
    assert len(app.state.booking_searches._records) == len(app.state.booking_contexts._contexts) == 5
    assert first["search_id"] in app.state.booking_contexts._claimed_search_ids
    assert not app.state.booking_searches._pending


def test_failed_registration_cleans_context_and_admission(client, http_data, booking_customer, monkeypatch):
    headers, _ = booking_customer
    occupy(http_data)
    original = app.state.booking_searches.publish

    def fail(*args):
        original(*args)
        raise RuntimeError("AUDIT_ONLY_REGISTRATION_FAILURE")

    monkeypatch.setattr(app.state.booking_searches, "publish", fail)
    result = client.post(ROOT + "/booking-searches", json=search_body(include_alternatives=True), headers=headers)
    assert result.status_code == 500 and "AUDIT_ONLY_REGISTRATION_FAILURE" not in result.text
    assert not app.state.booking_searches._pending and not app.state.booking_searches._records
    assert not app.state.booking_contexts._contexts


def test_separate_owned_searches_can_book_independent_slots_concurrently(client, http_data, booking_customer, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    headers, _ = booking_customer
    occupy(http_data)
    searches = [preview(client, headers, search_body(include_alternatives=True))["alternatives"] for _ in range(2)]
    assert searches[0]["search_id"] != searches[1]["search_id"]
    barrier = Barrier(2)
    original = appointments.AppointmentService.book

    def together(*args, **kwargs):
        barrier.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(appointments.AppointmentService, "book", together)
    def choose(index):
        found = searches[index]
        option = found["options"][0 if index == 0 else -1]
        return client.post(ROOT + f"/booking-searches/{found['search_id']}/selection",
            json={"option_id": option["option_id"], "confirmation": confirmation(option)}, headers=headers)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(choose, range(2)))
    assert [r.status_code for r in results] == [201, 201], [r.text for r in results]
    assert counts(http_data[0]) == (3, 3)


def test_stale_selected_slot_maps_error_and_releases_claim(client, http_data, booking_customer):
    headers, _ = booking_customer
    occupy(http_data)
    found = preview(client, headers, search_body(include_alternatives=True))["alternatives"]
    option = found["options"][0]
    with closing(database.open_development_database(http_data[0])) as conn:
        with database.transaction(conn):
            repositories.create_appointment(conn, 1, 1, option["staff_id"], option["start_datetime"], option["end_datetime"],
                items=[{"service_id": 1, "service_name": "Haircut", "duration_minutes": 45, "price": 80.0}])
    result = client.post(ROOT + f"/booking-searches/{found['search_id']}/selection",
        json={"option_id": option["option_id"], "confirmation": confirmation(option)}, headers=headers)
    assert result.status_code == 409 and result.json()["error"]["code"] == "SELECTED_ALTERNATIVE_NO_LONGER_AVAILABLE"
    assert counts(http_data[0]) == (2, 2)
    assert not app.state.booking_contexts._claimed_search_ids
    assert found["search_id"] in app.state.booking_searches._records


def test_preview_quote_reads_one_configuration_snapshot(client, http_data, booking_customer, monkeypatch):
    headers, _ = booking_customer
    execute(http_data[0], "PRAGMA journal_mode=WAL")
    original = availability.AvailabilityEngine.check

    def change_after_availability(self, *args, **kwargs):
        assert self.connection.in_transaction
        result = original(self, *args, **kwargs)
        execute(http_data[0], "UPDATE services SET default_price=90,default_duration_minutes=60 WHERE id=1")
        return result

    monkeypatch.setattr(availability.AvailabilityEngine, "check", change_after_availability)
    quote = preview(client, headers)["requested_availability"]["quote"]
    assert quote["total_duration_minutes"] == 45 and quote["total_price"] == "80.00"
    assert quote["end_datetime"] == "2027-01-04T10:45:00"


def test_http_month_end_window_clamps_without_fixed_day_count(client, http_data, booking_customer):
    headers, clock = booking_customer
    clock[0] = datetime(2027, 1, 31, 9)
    session = login(client, http_data[2]["CUSTOMER"])
    headers = {"Origin": DEVELOPMENT_ORIGIN, "X-CSRF-Token": session.json()["csrf_token"]}
    execute(http_data[0], "UPDATE business_settings SET booking_window_months=1")
    exact = client.post(ROOT + "/booking-searches", json=search_body(start_datetime="2027-02-28T09:00:00"), headers=headers)
    assert exact.status_code == 200, exact.text
    beyond = client.post(ROOT + "/booking-searches", json=search_body(start_datetime="2027-02-28T09:00:00.000001"), headers=headers)
    assert beyond.status_code == 422 and beyond.json()["error"]["code"] == "BOOKING_WINDOW_EXCEEDED"


def test_off_grid_requested_booking_remains_legal(client, http_data, booking_customer):
    headers, _ = booking_customer
    request = search_body(start_datetime="2027-01-04T12:47:00")
    quote = preview(client, headers, request)["requested_availability"]["quote"]
    assert quote["start_datetime"] == "2027-01-04T12:47:00"
    result = client.post(ROOT + "/appointments", json={**request, "confirmation": confirmation(quote)}, headers=headers)
    assert result.status_code == 201 and result.json()["end_datetime"] == "2027-01-04T13:32:00"
