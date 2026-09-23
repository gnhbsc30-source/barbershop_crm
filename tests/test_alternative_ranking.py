from datetime import datetime, timedelta

import pytest

from app.services.appointments import (
    AlternativeOption,
    AlternativeReasonCode,
    classify_alternative_priority,
    deduplicate_alternative_options,
    rank_alternative_options,
)


def make_option(staff_id, start_datetime, **overrides):
    values = {
        "option_id": f"placeholder-{staff_id}-{start_datetime.isoformat()}",
        "staff_id": staff_id,
        "staff_name": f"Staff {staff_id}",
        "start_datetime": start_datetime,
        "end_datetime": start_datetime + timedelta(minutes=45),
        "items": [],
        "total_duration_minutes": 45,
        "total_price": 80.0,
        "priority_group": 0,
        "reason_code": AlternativeReasonCode.SAME_STAFF_SAME_DAY,
        "distance_from_requested_start_minutes": 0,
        "distance_from_requested_date_days": 0,
        "date": start_datetime.date().isoformat(),
        "day_of_week": start_datetime.strftime("%A"),
        "display_date": start_datetime.date().isoformat(),
        "display_time": start_datetime.strftime("%H:%M"),
    }
    values.update(overrides)
    return AlternativeOption(**values)


@pytest.mark.parametrize(
    ("candidate_staff_id", "candidate_start", "priority_group", "reason_code"),
    [
        (3, datetime(2027, 1, 15, 11, 0), 1, AlternativeReasonCode.SAME_STAFF_SAME_DAY),
        (4, datetime(2027, 1, 15, 11, 0), 2, AlternativeReasonCode.OTHER_STAFF_SAME_DAY),
        (3, datetime(2027, 1, 16, 11, 0), 3, AlternativeReasonCode.SAME_STAFF_OTHER_DAY),
        (4, datetime(2027, 1, 16, 11, 0), 4, AlternativeReasonCode.OTHER_STAFF_OTHER_DAY),
    ],
)
def test_classify_alternative_priority_for_specific_staff(
    candidate_staff_id,
    candidate_start,
    priority_group,
    reason_code,
):
    assert classify_alternative_priority(
        requested_staff_id=3,
        requested_start_datetime=datetime(2027, 1, 15, 10, 0),
        candidate_staff_id=candidate_staff_id,
        candidate_start_datetime=candidate_start,
    ) == (priority_group, reason_code)


def test_classify_alternative_priority_rejects_any_staff_requests():
    with pytest.raises(ValueError, match="ANY_STAFF_PRIORITY_UNSUPPORTED"):
        classify_alternative_priority(
            requested_staff_id=None,
            requested_start_datetime=datetime(2027, 1, 15, 10, 0),
            candidate_staff_id=4,
            candidate_start_datetime=datetime(2027, 1, 15, 11, 0),
        )


def test_priority_group_beats_a_much_closer_option():
    requested_start = datetime(2027, 1, 15, 10, 0)
    ranked = rank_alternative_options(
        [
            make_option(4, datetime(2027, 1, 15, 10, 1)),
            make_option(3, datetime(2027, 1, 15, 14, 0)),
        ],
        requested_staff_id=3,
        requested_start_datetime=requested_start,
        booking_interval_minutes=30,
    )

    assert [option.staff_id for option in ranked] == [3, 4]
    assert ranked[0].priority_group == 1
    assert ranked[0].reason_code is AlternativeReasonCode.SAME_STAFF_SAME_DAY
    assert ranked[0].distance_from_requested_date_days == 0
    assert ranked[0].distance_from_requested_start_minutes == 240


def test_closer_date_wins_within_the_same_priority_group():
    ranked = rank_alternative_options(
        [
            make_option(3, datetime(2027, 1, 17, 10, 0)),
            make_option(3, datetime(2027, 1, 16, 10, 0)),
        ],
        requested_staff_id=3,
        requested_start_datetime=datetime(2027, 1, 15, 10, 0),
        booking_interval_minutes=30,
    )

    assert [option.start_datetime.date().isoformat() for option in ranked] == [
        "2027-01-16",
        "2027-01-17",
    ]


def test_closer_clock_time_wins_within_the_same_group_and_date():
    ranked = rank_alternative_options(
        [
            make_option(3, datetime(2027, 1, 15, 11, 30)),
            make_option(3, datetime(2027, 1, 15, 10, 20)),
        ],
        requested_staff_id=3,
        requested_start_datetime=datetime(2027, 1, 15, 10, 0),
        booking_interval_minutes=30,
    )

    assert [option.start_datetime.time().isoformat() for option in ranked] == [
        "10:20:00",
        "11:30:00",
    ]


def test_booking_interval_alignment_breaks_a_previous_ranking_tie():
    ranked = rank_alternative_options(
        [
            make_option(1, datetime(2027, 1, 15, 10, 30)),
            make_option(2, datetime(2027, 1, 15, 10, 0)),
        ],
        requested_staff_id=99,
        requested_start_datetime=datetime(2027, 1, 15, 10, 15),
        booking_interval_minutes=20,
    )

    assert [option.staff_id for option in ranked] == [2, 1]


def test_ranking_uses_deterministic_staff_and_start_tie_breakers():
    requested_start = datetime(2027, 1, 15, 10, 0)
    ranked_by_staff = rank_alternative_options(
        [
            make_option(4, datetime(2027, 1, 15, 9, 30)),
            make_option(2, datetime(2027, 1, 15, 10, 30)),
        ],
        requested_staff_id=99,
        requested_start_datetime=requested_start,
        booking_interval_minutes=30,
    )
    ranked_by_start = rank_alternative_options(
        [
            make_option(2, datetime(2027, 1, 15, 10, 30)),
            make_option(2, datetime(2027, 1, 15, 9, 30)),
        ],
        requested_staff_id=99,
        requested_start_datetime=requested_start,
        booking_interval_minutes=30,
    )

    assert [option.staff_id for option in ranked_by_staff] == [2, 4]
    assert [option.start_datetime.time().isoformat() for option in ranked_by_start] == [
        "09:30:00",
        "10:30:00",
    ]


def test_deduplication_removes_identical_staff_and_start():
    start = datetime(2027, 1, 15, 10, 0)

    deduplicated = deduplicate_alternative_options(
        [make_option(3, start), make_option(3, start)]
    )

    assert len(deduplicated) == 1


def test_deduplication_preserves_different_staff_with_the_same_start():
    start = datetime(2027, 1, 15, 10, 0)

    deduplicated = deduplicate_alternative_options(
        [make_option(3, start), make_option(4, start)]
    )

    assert [option.staff_id for option in deduplicated] == [3, 4]


def test_deduplication_rejects_conflicting_business_truth():
    start = datetime(2027, 1, 15, 10, 0)

    with pytest.raises(ValueError, match="CONFLICTING_ALTERNATIVE_DUPLICATE"):
        deduplicate_alternative_options(
            [
                make_option(3, start),
                make_option(3, start, total_price=90.0),
            ]
        )
