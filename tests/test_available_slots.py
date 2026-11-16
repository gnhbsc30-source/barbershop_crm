from datetime import datetime, timedelta
from threading import Event, Lock, Thread

import pytest

from test_tenant_context import browser

from app.main import app
from app.services import available_slots
from test_customer_booking_api import (
    ROOT, booking_customer, client, confirmation, execute, http_data, login, occupy, preview, search_body,
)


def daily(client, day="2027-01-04", services=(1,), staff=1):
    return client.get(ROOT + "/available-slots", params=[
        ("date", day), ("staff_id", staff), *(("service_id", service) for service in services),
    ])


def test_slots_use_real_windows_and_ordered_durations(client, http_data, booking_customer):
    headers, _ = booking_customer
    path = http_data[0]
    execute(path, "UPDATE staff_services SET duration_minutes = 30 WHERE staff_id = 1 AND service_id = 1")
    execute(path, "INSERT INTO schedule_breaks (staff_id,day_of_week,start_time,end_time) VALUES (1,1,'12:00','12:30')")
    execute(path, """INSERT INTO schedule_blocks (business_id,start_datetime,end_datetime)
                      VALUES (1,'2027-01-04T14:00:00','2027-01-04T14:20:00')""")
    occupy(http_data)
    response = daily(client, services=(2, 1))
    assert response.status_code == 200
    result = response.json()
    assert result["service_ids"] == [2, 1]
    assert result["slot_interval_minutes"] == 30
    slots = result["slots"]
    starts = [slot["start_datetime"][11:] for slot in slots]
    assert starts == sorted(set(starts))
    assert starts[0] == "09:00:00"
    assert "10:00:00" not in starts
    assert "10:45:00" in starts
    assert "12:00:00" not in starts
    assert "14:20:00" in starts
    assert slots[0]["end_datetime"] == "2027-01-04T09:50:00"
    assert set(slots[0]) == {"start_datetime", "end_datetime"}
    assert response.headers["cache-control"] == "no-store"
    for slot in slots:
        quote = preview(client, headers, search_body(service_ids=[2, 1], start_datetime=slot["start_datetime"]))
        assert quote["requested_availability"]["quote"]["end_datetime"] == slot["end_datetime"]


def test_policy_boundaries_and_empty_days(client, http_data, booking_customer):
    _, clock = booking_customer
    execute(http_data[0], "UPDATE business_settings SET minimum_booking_notice_minutes = 17")
    assert daily(client).json()["slots"][0]["start_datetime"] == "2027-01-04T09:30:00"
    assert daily(client, "2027-01-03").json()["error"]["code"] == "PAST_DATETIME"
    assert daily(client, "2027-01-09").json()["slots"] == []
    assert daily(client, "2027-04-04").json()["slots"][0]["start_datetime"] == "2027-04-04T09:00:00"
    assert daily(client, "2027-04-05").json()["error"]["code"] == "BOOKING_WINDOW_EXCEEDED"
    execute(http_data[0], """INSERT INTO schedule_blocks (business_id,start_datetime,end_datetime)
                              VALUES (1,'2027-01-04T08:00:00','2027-01-04T19:00:00')""")
    assert daily(client).json()["slots"] == []
    clock[0] = datetime(2027, 1, 4, 17, 30)
    assert login(client, http_data[2]["CUSTOMER"]).status_code == 200
    assert daily(client).json()["slots"] == []


def test_month_end_clamping(client, http_data, booking_customer):
    _, clock = booking_customer
    clock[0] = datetime(2027, 1, 31, 9)
    assert login(client, http_data[2]["CUSTOMER"]).status_code == 200
    execute(http_data[0], "UPDATE business_settings SET booking_window_months = 1")
    assert daily(client, "2027-02-28").json()["slots"][0]["start_datetime"] == "2027-02-28T09:00:00"
    assert daily(client, "2027-03-01").json()["error"]["code"] == "BOOKING_WINDOW_EXCEEDED"


def test_quote_and_booking_revalidate_selected_slot(client, http_data, booking_customer):
    headers, _ = booking_customer
    assert any(slot["start_datetime"].endswith("T10:00:00") for slot in daily(client).json()["slots"])
    body = search_body()
    quote = preview(client, headers, body)["requested_availability"]["quote"]
    assert quote["total_price"] == "80.00"
    occupy(http_data)
    response = client.post(ROOT + "/appointments", headers=headers, json={**body, "confirmation": confirmation(quote)})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "APPOINTMENT_UNAVAILABLE"
    assert not preview(client, headers, body)["requested_availability"]["available"]


def test_daily_validation_and_no_partial_results(client, http_data, booking_customer, monkeypatch):
    assert daily(client, "2027-1-04").status_code == 422
    assert daily(client, services=(1, 1)).status_code == 422
    assert daily(client, staff=999).status_code == 404
    assert client.get(ROOT + "/available-slots?date=" + "1" * 1024).status_code == 422
    monkeypatch.setattr(available_slots, "MAX_BLOCKERS", 0)
    occupy(http_data)
    response = daily(client)
    assert response.status_code == 503
    assert set(response.json()) == {"error"}


@pytest.mark.parametrize(("notice", "now", "expected"), [
    (0, (9, 17, 0), "09:30:00"),
    (17, (9, 0, 0), "09:30:00"),
    (17, (9, 12, 30), "10:00:00"),  # The 09:30 grid start has only 30 seconds of slack.
    (17, (9, 12, 0), "09:30:00"),  # Exactly one minute of slack is sufficient.
])
def test_clock_clipped_windows_leave_time_to_quote(client, http_data, booking_customer, notice, now, expected):
    headers, clock = booking_customer
    clock[0] = datetime(2027, 1, 4, *now)
    execute(http_data[0], "UPDATE business_settings SET minimum_booking_notice_minutes = ?", (notice,))
    start = daily(client).json()["slots"][0]["start_datetime"]
    assert start == "2027-01-04T" + expected
    clock[0] += timedelta(seconds=1)
    quote = preview(client, headers, search_body(start_datetime=start))
    assert quote["requested_availability"]["available"]


def test_close_genuine_off_grid_start_remains_available(client, http_data, booking_customer):
    headers, clock = booking_customer
    execute(http_data[0], "UPDATE business_settings SET minimum_booking_notice_minutes = 17")
    execute(http_data[0], """INSERT INTO schedule_blocks (business_id,start_datetime,end_datetime)
                             VALUES (1,'2027-01-04T09:00:00','2027-01-04T09:17:30')""")
    start = daily(client).json()["slots"][0]["start_datetime"]
    assert start == "2027-01-04T09:17:30"
    clock[0] += timedelta(seconds=1)
    assert preview(client, headers, search_body(start_datetime=start))["requested_availability"]["available"]


def test_three_http_sessions_overload_and_release_after_exception(client, http_data, booking_customer, monkeypatch):
    both_running, release = Event(), Event()
    lock = Lock()
    implementation = available_slots.available_slots
    active = 0

    def paused(customer, day, staff, services):
        nonlocal active
        with lock:
            active += 1
            if active == 2:
                both_running.set()
        try:
            assert release.wait(5)
            if staff == 1:
                raise RuntimeError("sensitive diagnostic must remain private")
            return implementation(customer, day, staff, services)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr("app.api.customer_booking.available_slots", paused)
    responses = {}
    second, third = browser(), browser()
    try:
        for other in (second, third):
            assert login(other, http_data[2]["CUSTOMER"]).status_code == 200
        tokens = {current.cookies["barbershop_dev_session"] for current in (client, second, third)}
        assert len(tokens) == 3
        workers = [
            Thread(target=lambda: responses.update(first=daily(client, staff=1))),
            Thread(target=lambda: responses.update(second=daily(second, staff=2))),
        ]
        for worker in workers:
            worker.start()
        try:
            assert both_running.wait(5)
            for rejected in (daily(client), daily(third)):
                assert rejected.status_code == 429
                assert rejected.json()["error"]["code"] == "AVAILABLE_SLOTS_BUSY"
                assert rejected.headers["retry-after"] == "1"
        finally:
            release.set()
            for worker in workers:
                worker.join(5)
        assert responses["first"].status_code == 500
        assert responses["first"].json() == {"error": {"code": "INTERNAL_ERROR"}}
        assert responses["second"].status_code == 200
        monkeypatch.setattr("app.api.customer_booking.available_slots", implementation)
        assert daily(client).status_code == 200
        assert daily(third).status_code == 200
    finally:
        second.close()
        third.close()
