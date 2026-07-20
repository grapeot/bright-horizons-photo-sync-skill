from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "America/Los_Angeles"


@dataclass(frozen=True, order=True)
class TimeRange:
    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start >= self.end:
            raise ValueError("时间范围必须满足 start < end")

    @property
    def api_latest(self) -> int:
        return self.end - 1

    def split(self) -> tuple[TimeRange, TimeRange]:
        midpoint = self.start + (self.end - self.start) // 2
        if midpoint <= self.start:
            raise ValueError("时间窗口已无法继续切分")
        return TimeRange(self.start, midpoint), TimeRange(midpoint, self.end)


def parse_date_range(since: str, until: str, timezone: str = DEFAULT_TIMEZONE) -> TimeRange:
    zone = ZoneInfo(timezone)
    since_date = date.fromisoformat(since)
    until_date = date.fromisoformat(until)
    start = int(datetime.combine(since_date, datetime.min.time(), zone).timestamp())
    end = int(datetime.combine(until_date, datetime.min.time(), zone).timestamp())
    return TimeRange(start, end)


def today_until(timezone: str = DEFAULT_TIMEZONE) -> str:
    return (datetime.now(ZoneInfo(timezone)).date() + timedelta(days=1)).isoformat()


def epoch_to_date(value: int, timezone: str = DEFAULT_TIMEZONE) -> str:
    return datetime.fromtimestamp(value, ZoneInfo(timezone)).date().isoformat()


def chunk_range(time_range: TimeRange, days: int = 7) -> list[TimeRange]:
    chunks: list[TimeRange] = []
    cursor = time_range.start
    max_seconds = days * 24 * 60 * 60
    while cursor < time_range.end:
        boundary = min(cursor + max_seconds, time_range.end)
        chunks.append(TimeRange(cursor, boundary))
        cursor = boundary
    return chunks
