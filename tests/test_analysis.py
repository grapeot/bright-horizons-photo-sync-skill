import json
from pathlib import Path

from bright_horizons_photo_sync.analysis import (
    attachment_keys,
    load_cached_events,
    load_legacy_attachment_keys,
)


def event(event_key: str, member: str, display: str, attachments: list[dict]) -> dict:
    return {
        "key": event_key,
        "event_time": 1,
        "event_date": "2026-01-01",
        "member": member,
        "member_display": display,
        "new_attachments": attachments,
    }


def test_cache_and_legacy_loaders_use_unique_attachment_sets(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache/api/events"
    cache_dir.mkdir(parents=True)
    cached_events = [
        event("e1", "child", "Child", [{"key": "target", "mime_type": "image/jpeg"}]),
        event("e2", "other", "Other", [{"key": "old-other", "mime_type": "image/jpeg"}]),
        event("e3", "other", "Other", [{"key": "new-image", "mime_type": "image/jpeg"}]),
        event("e4", "other", "Other", [{"key": "new-video", "mime_type": "video/mp4"}]),
        event("e5", "", "", [{"key": "new-unknown"}]),
    ]
    (cache_dir / "1_2_300.json").write_text(
        json.dumps({"fetched_at": "2026-01-01T00:00:00Z", "events": cached_events})
    )
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    (legacy_dir / "merged_events.json").write_text(
        json.dumps(
            {
                "events": [
                    event(
                        "legacy-1",
                        "child",
                        "Child",
                        [{"key": "target", "mime_type": "image/jpeg"}],
                    ),
                    event(
                        "legacy-2",
                        "other",
                        "Other",
                        [{"key": "old-other", "mime_type": "image/jpeg"}],
                    ),
                ]
            }
        )
    )

    events, cache_files = load_cached_events(tmp_path)

    assert cache_files == 1
    assert attachment_keys(events) == {
        "target",
        "old-other",
        "new-image",
        "new-video",
        "new-unknown",
    }
    assert load_legacy_attachment_keys(tmp_path) == {"target", "old-other"}
