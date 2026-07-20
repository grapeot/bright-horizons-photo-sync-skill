from pathlib import Path

import pytest

from bright_horizons_photo_sync.client import IntegrityError, StorageError, SyncError
from bright_horizons_photo_sync.ranges import TimeRange
from bright_horizons_photo_sync.store import Store
from bright_horizons_photo_sync.sync import collect_events, fetch_complete_range, run_sync


class FakeEventClient:
    def __init__(self, responder):
        self.responder = responder
        self.calls: list[TimeRange] = []

    def fetch_events(self, time_range: TimeRange, limit: int = 300):
        self.calls.append(time_range)
        return self.responder(time_range, limit)


def make_event(key: str, timestamp: int = 1) -> dict:
    return {
        "key": key,
        "event_time": timestamp,
        "event_date": "2026-01-01",
        "member": "child-1",
    }


def test_full_page_splits_until_complete() -> None:
    def responder(time_range: TimeRange, limit: int):
        if time_range.end - time_range.start > 5:
            return [make_event(f"full-{index}") for index in range(limit)]
        return [make_event(f"{time_range.start}-{time_range.end}", time_range.start)]

    client = FakeEventClient(responder)
    events = fetch_complete_range(client, TimeRange(0, 20))

    assert len(events) == 4
    assert len(client.calls) == 7


def test_single_second_full_page_fails() -> None:
    client = FakeEventClient(lambda _range, limit: [make_event(str(i)) for i in range(limit)])
    with pytest.raises(IntegrityError):
        fetch_complete_range(client, TimeRange(0, 1))


def test_wide_server_error_splits_window() -> None:
    def responder(time_range: TimeRange, _limit: int):
        if time_range.end - time_range.start > 24 * 60 * 60:
            raise SyncError("server error")
        return [make_event(str(time_range.start), time_range.start)]

    client = FakeEventClient(responder)
    events = fetch_complete_range(client, TimeRange(0, 4 * 24 * 60 * 60))
    assert len(events) == 4


def test_storage_error_does_not_split_window() -> None:
    client = FakeEventClient(lambda _range, _limit: (_ for _ in ()).throw(StorageError("disk")))
    with pytest.raises(StorageError):
        fetch_complete_range(client, TimeRange(0, 4 * 24 * 60 * 60))
    assert len(client.calls) == 1


def test_collect_events_deduplicates_same_payload() -> None:
    shared = make_event("same", 10)
    client = FakeEventClient(lambda _range, _limit: [shared])
    events = collect_events(client, TimeRange(0, 8 * 24 * 60 * 60))
    assert events == [shared]


def test_collect_events_rejects_conflicting_payload() -> None:
    calls = 0

    def responder(_range, _limit):
        nonlocal calls
        calls += 1
        return [make_event("same", calls)]

    client = FakeEventClient(responder)
    with pytest.raises(IntegrityError, match="冲突"):
        collect_events(client, TimeRange(0, 8 * 24 * 60 * 60))


class FakeSyncClient:
    def fetch_events(self, _time_range, _limit=300):
        other = make_event("other")
        other["member"] = "other-child"
        other["attachments"] = ["other-asset"]
        return [make_event("event"), other]

    def download_attachment(self, _key):
        raise AssertionError("events-only must not download")


def test_failed_sync_does_not_advance_watermark(tmp_path: Path) -> None:
    class BrokenClient(FakeSyncClient):
        def fetch_events(self, _time_range, _limit=300):
            raise SyncError("boom")

    store = Store(tmp_path)
    try:
        with pytest.raises(SyncError):
            run_sync(BrokenClient(), store, TimeRange(0, 10), events_only=True)
        assert store.last_success_until() is None
        assert store.status()["last_run"]["status"] == "failed"
    finally:
        store.close()


def test_events_only_sync_is_idempotent(tmp_path: Path) -> None:
    store = Store(tmp_path)
    try:
        first = run_sync(FakeSyncClient(), store, TimeRange(0, 10), events_only=True)
        second = run_sync(FakeSyncClient(), store, TimeRange(0, 10), events_only=True)
        assert first.event_count == second.event_count == 2
        assert store.status()["events"] == 2
        assert store.status()["attachments"] == 1
        assert {row[0] for row in store.connection.execute("SELECT member_id FROM events")} == {
            "child-1",
            "other-child",
        }
        assert store.last_success_until() == 10
    finally:
        store.close()
