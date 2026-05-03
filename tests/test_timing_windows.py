from datetime import datetime, timedelta, timezone

from src.models import TimingWindow


def test_timing_window_tradeable_mid_window():
    now = datetime(2026, 1, 1, 12, 5, tzinfo=timezone.utc)
    window = TimingWindow(now - timedelta(minutes=5), now + timedelta(minutes=5))

    assert window.is_tradeable(now, min_seconds_before_end=45)


def test_timing_window_rejects_too_close_to_end():
    now = datetime(2026, 1, 1, 12, 14, 30, tzinfo=timezone.utc)
    window = TimingWindow(
        datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
        datetime(2026, 1, 1, 12, 15, tzinfo=timezone.utc),
    )

    assert not window.is_tradeable(now, min_seconds_before_end=45)


def test_timing_window_rejects_after_max_seconds_from_start():
    now = datetime(2026, 1, 1, 12, 2, 1, tzinfo=timezone.utc)
    window = TimingWindow(
        datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
        datetime(2026, 1, 1, 12, 15, tzinfo=timezone.utc),
    )

    assert not window.is_tradeable(now, max_seconds_after_start=120)

