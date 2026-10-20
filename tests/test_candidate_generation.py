from datetime import datetime

from app.services.availability import generate_candidate_start_times


def test_candidate_generation_includes_an_off_grid_free_window_start():
    candidates = generate_candidate_start_times(
        free_window_start=datetime(2027, 1, 15, 12, 47),
        free_window_end=datetime(2027, 1, 15, 15, 30),
        service_duration_minutes=45,
        booking_interval_minutes=30,
    )

    assert candidates[0] == datetime(2027, 1, 15, 12, 47)


def test_candidate_generation_adds_interval_aligned_times_after_window_start():
    candidates = generate_candidate_start_times(
        free_window_start=datetime(2027, 1, 15, 12, 47),
        free_window_end=datetime(2027, 1, 15, 15, 30),
        service_duration_minutes=45,
        booking_interval_minutes=30,
    )

    assert candidates == [
        datetime(2027, 1, 15, 12, 47),
        datetime(2027, 1, 15, 13, 0),
        datetime(2027, 1, 15, 13, 30),
        datetime(2027, 1, 15, 14, 0),
        datetime(2027, 1, 15, 14, 30),
    ]


def test_candidate_generation_does_not_generate_every_minute():
    candidates = generate_candidate_start_times(
        free_window_start=datetime(2027, 1, 15, 12, 47),
        free_window_end=datetime(2027, 1, 15, 15, 30),
        service_duration_minutes=45,
        booking_interval_minutes=30,
    )

    assert datetime(2027, 1, 15, 12, 48) not in candidates
    assert datetime(2027, 1, 15, 13, 1) not in candidates


def test_candidate_generation_excludes_starts_that_overrun_the_window():
    candidates = generate_candidate_start_times(
        free_window_start=datetime(2027, 1, 15, 12, 47),
        free_window_end=datetime(2027, 1, 15, 13, 44),
        service_duration_minutes=45,
        booking_interval_minutes=30,
    )

    assert candidates == [datetime(2027, 1, 15, 12, 47)]


def test_candidate_generation_allows_an_exact_fit_at_window_end():
    candidates = generate_candidate_start_times(
        free_window_start=datetime(2027, 1, 15, 12, 47),
        free_window_end=datetime(2027, 1, 15, 13, 45),
        service_duration_minutes=45,
        booking_interval_minutes=30,
    )

    assert candidates == [
        datetime(2027, 1, 15, 12, 47),
        datetime(2027, 1, 15, 13, 0),
    ]


def test_candidate_generation_returns_no_candidates_for_a_short_window():
    candidates = generate_candidate_start_times(
        free_window_start=datetime(2027, 1, 15, 12, 47),
        free_window_end=datetime(2027, 1, 15, 13, 31),
        service_duration_minutes=45,
        booking_interval_minutes=30,
    )

    assert candidates == []


def test_candidate_generation_is_deterministically_ordered():
    arguments = {
        "free_window_start": datetime(2027, 1, 15, 12, 47),
        "free_window_end": datetime(2027, 1, 15, 15, 30),
        "service_duration_minutes": 45,
        "booking_interval_minutes": 30,
    }

    assert generate_candidate_start_times(**arguments) == generate_candidate_start_times(
        **arguments
    )
