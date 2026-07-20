from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .client import IntegrityError
from .store import extract_attachments


def load_cached_events(data_dir: Path) -> tuple[list[dict[str, Any]], int]:
    cache_files = sorted((data_dir / "cache/api/events").glob("*.json"))
    latest: dict[str, tuple[str, dict[str, Any]]] = {}
    for path in cache_files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise IntegrityError(f"无法读取 API cache：{path.name}") from exc
        events = payload.get("events") if isinstance(payload, dict) else None
        fetched_at = payload.get("fetched_at", "") if isinstance(payload, dict) else ""
        if not isinstance(events, list):
            raise IntegrityError(f"API cache 缺少 events 数组：{path.name}")
        for event in events:
            if not isinstance(event, dict) or not event.get("key"):
                continue
            key = str(event["key"])
            previous = latest.get(key)
            if previous is None or fetched_at >= previous[0]:
                latest[key] = (fetched_at, event)
    return [item[1] for item in latest.values()], len(cache_files)


def attachment_keys(events: list[dict[str, Any]]) -> set[str]:
    return {str(attachment["key"]) for event in events for attachment in extract_attachments(event)}


def load_legacy_attachment_keys(data_dir: Path) -> set[str]:
    path = data_dir / "legacy/merged_events.json"
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IntegrityError("无法读取 legacy merged_events.json") from exc
    events = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(events, list):
        raise IntegrityError("legacy merged_events.json 缺少 events 数组")
    return attachment_keys([event for event in events if isinstance(event, dict)])
