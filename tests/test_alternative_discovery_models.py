from datetime import datetime

from app.services.appointments import (
    AlternativeOption,
    AlternativeReasonCode,
    AlternativeSearchResult,
    ResolvedAppointmentItem,
)


def test_alternative_option_preserves_business_truth_ranking_and_display_fields():
    item = ResolvedAppointmentItem(
        service_id=1,
        service_name="Haircut",
        duration_minutes=45,
        price=80.0,
    )
    start = datetime(2027, 1, 15, 10, 0)
    end = datetime(2027, 1, 15, 10, 45)

    option = AlternativeOption(
        option_id="option-1",
        staff_id=3,
        staff_name="Noa",
        start_datetime=start,
        end_datetime=end,
        items=[item],
        total_duration_minutes=45,
        total_price=80.0,
        priority_group=1,
        reason_code=AlternativeReasonCode.SAME_STAFF_SAME_DAY,
        distance_from_requested_start_minutes=30,
        distance_from_requested_date_days=0,
        date="2027-01-15",
        day_of_week="Friday",
        display_date="15 Jan 2027",
        display_time="10:00",
    )

    assert option.option_id == "option-1"
    assert option.start_datetime == start
    assert option.end_datetime == end
    assert option.items == [item]
    assert option.reason_code is AlternativeReasonCode.SAME_STAFF_SAME_DAY
    assert option.display_date == "15 Jan 2027"


def test_alternative_search_result_preserves_request_and_option_structure():
    requested_start = datetime(2027, 1, 15, 10, 0)
    option = AlternativeOption(
        option_id="option-1",
        staff_id=3,
        staff_name="Noa",
        start_datetime=requested_start,
        end_datetime=datetime(2027, 1, 15, 10, 45),
        items=[],
        total_duration_minutes=45,
        total_price=80.0,
        priority_group=1,
        reason_code=AlternativeReasonCode.SAME_STAFF_SAME_DAY,
        distance_from_requested_start_minutes=0,
        distance_from_requested_date_days=0,
        date="2027-01-15",
        day_of_week="Friday",
        display_date="15 Jan 2027",
        display_time="10:00",
    )

    result = AlternativeSearchResult(
        requested_staff_id=3,
        requested_start_datetime=requested_start,
        requested_service_ids=[1],
        alternatives=[option],
        total_available=1,
        has_more=False,
    )

    assert result.requested_staff_id == 3
    assert result.requested_start_datetime == requested_start
    assert result.requested_service_ids == [1]
    assert result.alternatives == [option]
    assert result.total_available == 1
    assert result.has_more is False


def test_alternative_reason_codes_are_the_approved_string_values():
    assert [reason_code.value for reason_code in AlternativeReasonCode] == [
        "SAME_STAFF_SAME_DAY",
        "OTHER_STAFF_SAME_DAY",
        "SAME_STAFF_OTHER_DAY",
        "OTHER_STAFF_OTHER_DAY",
    ]
