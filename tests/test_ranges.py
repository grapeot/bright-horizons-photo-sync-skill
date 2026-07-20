from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from bright_horizons_photo_sync.ranges import TimeRange, chunk_range, parse_date_range


def test_parse_range_uses_half_open_dst_boundaries() -> None:
    spring = parse_date_range("2026-03-08", "2026-03-09")
    fall = parse_date_range("2026-11-01", "2026-11-02")

    assert spring.end - spring.start == 23 * 60 * 60
    assert fall.end - fall.start == 25 * 60 * 60
    assert spring.api_latest == spring.end - 1


def test_chunk_range_covers_without_gaps() -> None:
    target = TimeRange(10, 10 + 15 * 24 * 60 * 60)
    chunks = chunk_range(target)

    assert len(chunks) == 3
    assert chunks[0].start == target.start
    assert chunks[-1].end == target.end
    assert all(left.end == right.start for left, right in zip(chunks, chunks[1:], strict=False))


def test_invalid_range_fails() -> None:
    with pytest.raises(ValueError):
        TimeRange(10, 10)


def test_date_epoch_is_pacific_midnight() -> None:
    parsed = parse_date_range("2026-07-19", "2026-07-20")
    zone = ZoneInfo("America/Los_Angeles")
    assert datetime.fromtimestamp(parsed.start, zone).hour == 0
    assert datetime.fromtimestamp(parsed.end, zone).date().isoformat() == "2026-07-20"
