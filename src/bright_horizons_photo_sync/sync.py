from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Protocol

from .analysis import load_legacy_attachment_keys
from .client import (
    AuthenticationError,
    IntegrityError,
    MyBrightDayClient,
    StorageError,
    SyncError,
)
from .media import sync_media
from .ranges import TimeRange, chunk_range
from .store import Store


class EventClient(Protocol):
    def fetch_events(self, time_range: TimeRange, limit: int = 300) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class SyncResult:
    run_id: str
    event_count: int
    attachment_count: int
    downloaded_count: int
    failed_downloads: int


def payload_hash(event: dict[str, Any]) -> str:
    payload = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def fetch_complete_range(
    client: EventClient,
    time_range: TimeRange,
    *,
    limit: int = 300,
) -> list[dict[str, Any]]:
    try:
        events = client.fetch_events(time_range, limit)
    except (AuthenticationError, IntegrityError, StorageError):
        raise
    except SyncError as exc:
        if time_range.end - time_range.start <= 1:
            raise SyncError(
                f"单秒事件窗口 [{time_range.start}, {time_range.end}) 仍请求失败"
            ) from exc
        left, right = time_range.split()
        return fetch_complete_range(client, left, limit=limit) + fetch_complete_range(
            client, right, limit=limit
        )
    if len(events) < limit:
        return events
    if time_range.end - time_range.start <= 1:
        raise IntegrityError("单秒事件窗口仍达到结果上限，无法证明数据完整")
    left, right = time_range.split()
    return fetch_complete_range(client, left, limit=limit) + fetch_complete_range(
        client, right, limit=limit
    )


def collect_events(client: EventClient, time_range: TimeRange) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    hashes: dict[str, str] = {}
    for window in chunk_range(time_range):
        try:
            window_events = fetch_complete_range(client, window)
        except SyncError:
            raise
        for event in window_events:
            key = event.get("key")
            if not key:
                raise IntegrityError("事件缺少稳定 key")
            digest = payload_hash(event)
            key = str(key)
            if key in hashes and hashes[key] != digest:
                raise IntegrityError(f"同一次同步中事件 {key} 出现冲突 payload")
            hashes[key] = digest
            by_key[key] = event
    return sorted(
        by_key.values(),
        key=lambda event: (float(event.get("event_time", 0)), str(event["key"])),
    )


def run_sync(
    client: MyBrightDayClient,
    store: Store,
    time_range: TimeRange,
    *,
    events_only: bool = False,
) -> SyncResult:
    run_id = uuid.uuid4().hex
    store.start_run(run_id, time_range.start, time_range.end)
    try:
        events = collect_events(client, time_range)
        event_count = store.publish_events(
            events,
            load_legacy_attachment_keys(store.data_dir),
        )
        downloaded = 0
        failed = 0
        if not events_only:
            downloaded, failed = sync_media(client, store)
            if failed:
                raise SyncError(f"仍有 {failed} 个附件下载失败，可更新凭证后重跑")
        attachment_count = store.attachment_count()
        store.complete_run(run_id, time_range.end, event_count, downloaded)
        return SyncResult(run_id, event_count, attachment_count, downloaded, failed)
    except Exception as exc:
        with suppress(StorageError):
            store.fail_run(run_id, str(exc))
        raise
