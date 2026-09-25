from datetime import datetime, timedelta

import pytest

from app.services import alternatives
from app.services.alternatives import AlternativeDiscoveryService
from app.services.appointments import rank_alternative_options
from app.services.appointments import AlternativeReasonCode, AppointmentBookingError


def add_hours(connection, business_id, staff_ids, target_date, start="09:00", end="17:00"):
    day_of_week = (target_date.weekday() + 1) % 7
    connection.execute(
        """
        INSERT OR IGNORE INTO business_working_hours (
            business_id, day_of_week, start_time, end_time
        ) VALUES (?, ?, ?, ?)
        """,
        (business_id, day_of_week, start, end),
    )
    for staff_id in staff_ids:
        connection.execute(
            """
            INSERT OR IGNORE INTO working_hours (
                staff_id, day_of_week, start_time, end_time
            ) VALUES (?, ?, ?, ?)
            """,
            (staff_id, day_of_week, start, end),
        )


def setup_discovery(connection, *, settings=None, dates=None):
    connection.execute("INSERT INTO businesses (business_name) VALUES (?)", ("Shop",))
    business_id = connection.execute("SELECT id FROM businesses").fetchone()["id"]
    settings = settings or {}
    connection.execute(
        """
        INSERT INTO business_settings (
            business_id, booking_window_months, minimum_booking_notice_minutes,
            alternative_search_window_days, booking_interval_minutes
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            business_id,
            settings.get("booking_window_months", 3),
            settings.get("minimum_booking_notice_minutes", 0),
            settings.get("alternative_search_window_days", 1),
            settings.get("booking_interval_minutes", 30),
        ),
    )
    staff_ids = []
    for name in ("Requested", "Other"):
        connection.execute(
            "INSERT INTO staff (business_id, name) VALUES (?, ?)",
            (business_id, name),
        )
        staff_ids.append(connection.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])
    connection.execute(
        """
        INSERT INTO services (business_id, name, default_duration_minutes, default_price)
        VALUES (?, ?, ?, ?)
        """,
        (business_id, "Haircut", 45, 80.0),
    )
    service_id = connection.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    for staff_id in staff_ids:
        connection.execute(
            """
            INSERT INTO staff_services (business_id, staff_id, service_id)
            VALUES (?, ?, ?)
            """,
            (business_id, staff_id, service_id),
        )
    for target_date in dates or [datetime(2027, 1, 15).date(), datetime(2027, 1, 16).date()]:
        add_hours(connection, business_id, staff_ids, target_date)
    connection.commit()
    return business_id, staff_ids, service_id


def discover(connection, business_id, requested_staff_id, service_ids, requested_start, now):
    return AlternativeDiscoveryService(connection, now_provider=lambda: now).discover(
        business_id=business_id,
        requested_staff_id=requested_staff_id,
        requested_start_datetime=requested_start,
        requested_service_ids=service_ids,
    )


def test_discovery_returns_all_specific_priority_groups_and_same_day_earlier_time(test_database):
    business_id, (requested_staff_id, other_staff_id), service_id = setup_discovery(test_database)
    result = discover(
        test_database,
        business_id,
        requested_staff_id,
        [service_id],
        datetime(2027, 1, 15, 12, 0),
        datetime(2027, 1, 15, 8, 0),
    )

    assert {option.reason_code for option in result.alternatives} == {
        AlternativeReasonCode.SAME_STAFF_SAME_DAY,
        AlternativeReasonCode.OTHER_STAFF_SAME_DAY,
        AlternativeReasonCode.SAME_STAFF_OTHER_DAY,
        AlternativeReasonCode.OTHER_STAFF_OTHER_DAY,
    }
    assert [option.priority_group for option in result.alternatives] == sorted(
        option.priority_group for option in result.alternatives
    )
    assert any(
        option.staff_id == requested_staff_id
        and option.start_datetime == datetime(2027, 1, 15, 9, 0)
        for option in result.alternatives
    )
    assert all(option.start_datetime.date() >= datetime(2027, 1, 15).date() for option in result.alternatives)
    assert other_staff_id in [option.staff_id for option in result.alternatives]


def test_discovery_does_not_search_beyond_alternative_search_window(test_database):
    business_id, (requested_staff_id, _), service_id = setup_discovery(
        test_database,
        settings={"alternative_search_window_days": 1},
        dates=[
            datetime(2027, 1, 15).date(),
            datetime(2027, 1, 16).date(),
            datetime(2027, 1, 17).date(),
        ],
    )
    result = discover(
        test_database,
        business_id,
        requested_staff_id,
        [service_id],
        datetime(2027, 1, 15, 12, 0),
        datetime(2027, 1, 15, 8, 0),
    )

    assert {option.start_datetime.date() for option in result.alternatives} == {
        datetime(2027, 1, 15).date(),
        datetime(2027, 1, 16).date(),
    }


def test_discovery_respects_search_and_booking_window_date_boundaries(test_database):
    business_id, (requested_staff_id, _), service_id = setup_discovery(
        test_database,
        settings={"alternative_search_window_days": 7, "booking_window_months": 3},
        dates=[datetime(2027, 4, 30).date(), datetime(2027, 5, 1).date()],
    )
    result = discover(
        test_database,
        business_id,
        requested_staff_id,
        [service_id],
        datetime(2027, 4, 30, 12, 0),
        datetime(2027, 1, 31, 17, 0),
    )

    assert result.alternatives
    assert {option.start_datetime.date() for option in result.alternatives} == {
        datetime(2027, 4, 30).date()
    }


def test_discovery_respects_minimum_notice_boundary(test_database):
    business_id, (requested_staff_id, _), service_id = setup_discovery(
        test_database,
        settings={"minimum_booking_notice_minutes": 120},
    )
    result = discover(
        test_database,
        business_id,
        requested_staff_id,
        [service_id],
        datetime(2027, 1, 15, 12, 0),
        datetime(2027, 1, 15, 10, 0),
    )

    assert all(option.start_datetime >= datetime(2027, 1, 15, 12, 0) for option in result.alternatives)


def test_discovery_uses_policy_defaults_when_business_settings_are_missing(test_database):
    business_id, (requested_staff_id, _), service_id = setup_discovery(test_database)
    test_database.execute("DELETE FROM business_settings WHERE business_id = ?", (business_id,))
    test_database.commit()

    result = discover(
        test_database,
        business_id,
        requested_staff_id,
        [service_id],
        datetime(2027, 1, 15, 12, 0),
        datetime(2027, 1, 15, 8, 0),
    )

    assert result.alternatives
    assert result.alternatives[0].start_datetime.minute in {0, 30}


def test_discovery_uses_staff_overrides_and_multi_service_totals(test_database):
    business_id, (requested_staff_id, other_staff_id), haircut_id = setup_discovery(test_database)
    test_database.execute(
        """
        INSERT INTO services (business_id, name, default_duration_minutes, default_price)
        VALUES (?, ?, ?, ?)
        """,
        (business_id, "Beard", 20, 40.0),
    )
    beard_id = test_database.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    for staff_id in (requested_staff_id, other_staff_id):
        test_database.execute(
            "INSERT INTO staff_services (business_id, staff_id, service_id) VALUES (?, ?, ?)",
            (business_id, staff_id, beard_id),
        )
    test_database.execute(
        """
        UPDATE staff_services SET duration_minutes = ?, price = ?
        WHERE staff_id = ? AND service_id = ?
        """,
        (60, 100.0, other_staff_id, haircut_id),
    )
    test_database.commit()

    result = discover(
        test_database,
        business_id,
        requested_staff_id,
        [haircut_id, beard_id],
        datetime(2027, 1, 15, 12, 0),
        datetime(2027, 1, 15, 8, 0),
    )

    other_option = next(option for option in result.alternatives if option.staff_id == other_staff_id)
    assert other_option.total_duration_minutes == 80
    assert other_option.total_price == 140.0
    assert [item.service_name for item in other_option.items] == ["Haircut", "Beard"]


def test_staff_duration_override_controls_candidate_fitting(test_database):
    target_date = datetime(2027, 1, 15).date()
    business_id, (requested_staff_id, other_staff_id), service_id = setup_discovery(
        test_database,
        dates=[target_date],
    )
    day_of_week = (target_date.weekday() + 1) % 7
    test_database.execute(
        """
        UPDATE business_working_hours SET end_time = ?
        WHERE business_id = ? AND day_of_week = ?
        """,
        ("09:45", business_id, day_of_week),
    )
    test_database.execute(
        "UPDATE working_hours SET end_time = ? WHERE staff_id IN (?, ?)",
        ("09:45", requested_staff_id, other_staff_id),
    )
    test_database.execute(
        """
        UPDATE staff_services SET duration_minutes = ?
        WHERE staff_id = ? AND service_id = ?
        """,
        (60, other_staff_id, service_id),
    )
    test_database.commit()

    result = discover(
        test_database,
        business_id,
        requested_staff_id,
        [service_id],
        datetime(2027, 1, 15, 12, 0),
        datetime(2027, 1, 15, 8, 0),
    )

    assert {option.staff_id for option in result.alternatives} == {requested_staff_id}


def test_discovery_excludes_busy_break_and_blocked_times(test_database):
    business_id, (requested_staff_id, other_staff_id), service_id = setup_discovery(test_database)
    test_database.execute(
        "INSERT INTO customers (business_id, name, customer_type) VALUES (?, ?, ?)",
        (business_id, "Customer", "GUEST"),
    )
    customer_id = test_database.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    test_database.execute(
        """
        INSERT INTO appointments (business_id, customer_id, staff_id, start_datetime, end_datetime, status)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (business_id, customer_id, requested_staff_id, "2027-01-15T09:00:00", "2027-01-15T10:00:00", "BOOKED"),
    )
    day_of_week = (datetime(2027, 1, 15).weekday() + 1) % 7
    test_database.execute(
        "INSERT INTO schedule_breaks (staff_id, day_of_week, start_time, end_time) VALUES (?, ?, ?, ?)",
        (requested_staff_id, day_of_week, "11:00", "12:00"),
    )
    test_database.execute(
        """
        INSERT INTO schedule_blocks (business_id, staff_id, start_datetime, end_datetime)
        VALUES (?, ?, ?, ?)
        """,
        (business_id, requested_staff_id, "2027-01-15T13:00:00", "2027-01-15T14:00:00"),
    )
    test_database.commit()

    result = discover(
        test_database,
        business_id,
        requested_staff_id,
        [service_id],
        datetime(2027, 1, 15, 12, 0),
        datetime(2027, 1, 15, 8, 0),
    )
    requested_staff_options = [option for option in result.alternatives if option.staff_id == requested_staff_id]

    blocked_ranges = [
        (datetime(2027, 1, 15, 9, 0), datetime(2027, 1, 15, 10, 0)),
        (datetime(2027, 1, 15, 11, 0), datetime(2027, 1, 15, 12, 0)),
        (datetime(2027, 1, 15, 13, 0), datetime(2027, 1, 15, 14, 0)),
    ]
    assert all(
        option.end_datetime <= blocked_start or option.start_datetime >= blocked_end
        for option in requested_staff_options
        for blocked_start, blocked_end in blocked_ranges
    )
    assert other_staff_id in [option.staff_id for option in result.alternatives]


def test_discovery_assigns_ids_after_ranking_and_is_deterministic(test_database):
    business_id, (requested_staff_id, _), service_id = setup_discovery(test_database)
    arguments = {
        "business_id": business_id,
        "requested_staff_id": requested_staff_id,
        "service_ids": [service_id],
        "requested_start": datetime(2027, 1, 15, 12, 0),
        "now": datetime(2027, 1, 15, 8, 0),
    }

    first = discover(test_database, **arguments)
    second = discover(test_database, **arguments)

    assert first == second
    assert first.total_available == len(first.alternatives)
    assert first.has_more is False
    assert [option.option_id for option in first.alternatives] == [
        f"option_{index:03d}" for index in range(1, len(first.alternatives) + 1)
    ]


def test_discovery_rejects_any_staff_requests(test_database):
    business_id, (_, _), service_id = setup_discovery(test_database)

    with pytest.raises(AppointmentBookingError, match="ANY_STAFF_DISCOVERY_UNSUPPORTED"):
        discover(
            test_database,
            business_id,
            None,
            [service_id],
            datetime(2027, 1, 15, 12, 0),
            datetime(2027, 1, 15, 8, 0),
        )


def test_discovery_rejects_yesterday_as_past_datetime(test_database):
    business_id, (requested_staff_id, _), service_id = setup_discovery(test_database)

    with pytest.raises(AppointmentBookingError, match="PAST_DATETIME"):
        discover(
            test_database,
            business_id,
            requested_staff_id,
            [service_id],
            datetime(2027, 1, 14, 12, 0),
            datetime(2027, 1, 15, 8, 0),
        )


def test_discovery_rejects_earlier_today_as_past_datetime(test_database):
    business_id, (requested_staff_id, _), service_id = setup_discovery(test_database)

    with pytest.raises(AppointmentBookingError, match="PAST_DATETIME"):
        discover(
            test_database,
            business_id,
            requested_staff_id,
            [service_id],
            datetime(2027, 1, 15, 7, 59),
            datetime(2027, 1, 15, 8, 0),
        )


def test_discovery_allows_original_request_at_exact_now_boundary(test_database):
    business_id, (requested_staff_id, _), service_id = setup_discovery(test_database)

    result = discover(
        test_database,
        business_id,
        requested_staff_id,
        [service_id],
        datetime(2027, 1, 15, 8, 0),
        datetime(2027, 1, 15, 8, 0),
    )

    assert result.alternatives


def test_discovery_rejects_original_request_below_minimum_notice(test_database):
    business_id, (requested_staff_id, _), service_id = setup_discovery(
        test_database,
        settings={"minimum_booking_notice_minutes": 120},
    )

    with pytest.raises(
        AppointmentBookingError,
        match="MINIMUM_BOOKING_NOTICE_VIOLATION",
    ):
        discover(
            test_database,
            business_id,
            requested_staff_id,
            [service_id],
            datetime(2027, 1, 15, 9, 59),
            datetime(2027, 1, 15, 8, 0),
        )


def test_discovery_allows_original_request_at_minimum_notice_boundary(test_database):
    business_id, (requested_staff_id, _), service_id = setup_discovery(
        test_database,
        settings={"minimum_booking_notice_minutes": 120},
    )

    result = discover(
        test_database,
        business_id,
        requested_staff_id,
        [service_id],
        datetime(2027, 1, 15, 10, 0),
        datetime(2027, 1, 15, 8, 0),
    )

    assert result.alternatives
    assert all(option.start_datetime >= datetime(2027, 1, 15, 10, 0) for option in result.alternatives)


def test_discovery_rejects_original_request_beyond_booking_window(test_database):
    business_id, (requested_staff_id, _), service_id = setup_discovery(test_database)

    with pytest.raises(AppointmentBookingError, match="BOOKING_WINDOW_EXCEEDED"):
        discover(
            test_database,
            business_id,
            requested_staff_id,
            [service_id],
            datetime(2027, 4, 30, 17, 1),
            datetime(2027, 1, 31, 17, 0),
        )


def test_discovery_allows_original_request_at_booking_window_boundary(test_database):
    boundary_date = datetime(2027, 4, 30).date()
    business_id, (requested_staff_id, _), service_id = setup_discovery(
        test_database,
        dates=[boundary_date],
    )

    result = discover(
        test_database,
        business_id,
        requested_staff_id,
        [service_id],
        datetime(2027, 4, 30, 17, 0),
        datetime(2027, 1, 31, 17, 0),
    )

    assert result.requested_start_datetime == datetime(2027, 4, 30, 17, 0)


def test_discovery_passes_configured_booking_interval_to_ranking(test_database, monkeypatch):
    business_id, (requested_staff_id, _), service_id = setup_discovery(
        test_database,
        settings={"booking_interval_minutes": 15},
    )
    captured: dict[str, int] = {}

    def spy_rank(options, **kwargs):
        captured["booking_interval_minutes"] = kwargs["booking_interval_minutes"]
        return rank_alternative_options(options, **kwargs)

    monkeypatch.setattr(alternatives, "rank_alternative_options", spy_rank)

    result = discover(
        test_database,
        business_id,
        requested_staff_id,
        [service_id],
        datetime(2027, 1, 15, 12, 0),
        datetime(2027, 1, 15, 8, 0),
    )

    assert result.alternatives
    assert captured["booking_interval_minutes"] == 15


def test_discovery_returns_date_as_iso_string(test_database):
    business_id, (requested_staff_id, _), service_id = setup_discovery(test_database)

    result = discover(
        test_database,
        business_id,
        requested_staff_id,
        [service_id],
        datetime(2027, 1, 15, 12, 0),
        datetime(2027, 1, 15, 8, 0),
    )

    assert isinstance(result.alternatives[0].date, str)
    assert result.alternatives[0].date == result.alternatives[0].start_datetime.date().isoformat()


def test_discovery_uses_other_staff_when_requested_staff_is_incompatible(test_database):
    business_id, (requested_staff_id, other_staff_id), service_id = setup_discovery(
        test_database
    )
    test_database.execute(
        "DELETE FROM staff_services WHERE staff_id = ? AND service_id = ?",
        (requested_staff_id, service_id),
    )
    test_database.commit()

    result = discover(
        test_database,
        business_id,
        requested_staff_id,
        [service_id],
        datetime(2027, 1, 15, 12, 0),
        datetime(2027, 1, 15, 8, 0),
    )

    assert result.alternatives
    assert {option.staff_id for option in result.alternatives} == {other_staff_id}
    assert {option.reason_code for option in result.alternatives} == {
        AlternativeReasonCode.OTHER_STAFF_SAME_DAY,
        AlternativeReasonCode.OTHER_STAFF_OTHER_DAY,
    }
    assert {option.priority_group for option in result.alternatives} == {2, 4}


def test_discovery_rejects_invalid_requested_staff(test_database):
    business_id, (_, _), service_id = setup_discovery(test_database)

    with pytest.raises(AppointmentBookingError, match="STAFF_NOT_FOUND"):
        discover(
            test_database,
            business_id,
            999,
            [service_id],
            datetime(2027, 1, 15, 12, 0),
            datetime(2027, 1, 15, 8, 0),
        )


def test_discovery_rejects_inactive_requested_staff(test_database):
    business_id, (requested_staff_id, _), service_id = setup_discovery(test_database)
    test_database.execute(
        "UPDATE staff SET is_active = 0 WHERE id = ?",
        (requested_staff_id,),
    )
    test_database.commit()

    with pytest.raises(AppointmentBookingError, match="STAFF_INACTIVE"):
        discover(
            test_database,
            business_id,
            requested_staff_id,
            [service_id],
            datetime(2027, 1, 15, 12, 0),
            datetime(2027, 1, 15, 8, 0),
        )


def test_discovery_rejects_inactive_service(test_database):
    business_id, (requested_staff_id, _), service_id = setup_discovery(test_database)
    test_database.execute(
        "UPDATE services SET is_active = 0 WHERE id = ?",
        (service_id,),
    )
    test_database.commit()

    with pytest.raises(AppointmentBookingError, match="SERVICE_NOT_FOUND"):
        discover(
            test_database,
            business_id,
            requested_staff_id,
            [service_id],
            datetime(2027, 1, 15, 12, 0),
            datetime(2027, 1, 15, 8, 0),
        )


def test_discovery_returns_empty_result_when_no_staff_is_eligible(test_database):
    business_id, (requested_staff_id, _), service_id = setup_discovery(test_database)
    test_database.execute(
        "DELETE FROM staff_services WHERE service_id = ?",
        (service_id,),
    )
    test_database.commit()

    result = discover(
        test_database,
        business_id,
        requested_staff_id,
        [service_id],
        datetime(2027, 1, 15, 12, 0),
        datetime(2027, 1, 15, 8, 0),
    )

    assert result.alternatives == []
    assert result.total_available == 0
    assert result.has_more is False
