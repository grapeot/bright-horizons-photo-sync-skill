import json
import sqlite3
from pathlib import Path

import pytest

from bright_horizons_photo_sync.client import StorageError
from bright_horizons_photo_sync.store import Store, extract_attachments


def event(key: str = "event-1") -> dict:
    return {
        "key": key,
        "event_time": 1_700_000_000,
        "event_date": "2023-11-14",
        "category": "photo",
        "attachments": ["asset-1"],
        "new_attachments": [
            {"key": "asset-1", "filename": "one.jpg", "mime_type": "image/jpeg"},
            {"key": "asset-2", "filename": "two.jpg", "mime_type": "image/jpeg"},
        ],
        "media": [{"key": "asset-2", "mime_type": "image/jpeg"}],
    }


def test_extract_attachments_deduplicates_all_shapes() -> None:
    attachments = extract_attachments(event())
    assert {item["key"] for item in attachments} == {"asset-1", "asset-2"}
    assert next(item for item in attachments if item["key"] == "asset-1")["filename"] == "one.jpg"


def test_store_upsert_and_export(tmp_path: Path) -> None:
    store = Store(tmp_path)
    try:
        original = event()
        original["member"] = "child-1"
        original["member_display"] = "Child One"
        assert store.publish_events([original]) == 1
        changed = event()
        changed["member"] = "child-1"
        changed["comment"] = "updated"
        changed["member_display"] = "Child One"
        assert store.publish_events([changed]) == 1
        status = store.status()
        assert status["events"] == 1
        assert status["attachments"] == 2
        assert len(store.pending_attachments()) == 2
        context = store.connection.execute("SELECT * FROM v_attachment_context").fetchone()
        assert context["member_id"] == "child-1"
        assert context["member_display"] == "Child One"

        exported = store.export()
        payload = json.loads(Path(str(exported["json"])).read_text())
        assert payload["total_events"] == 1
        assert payload["events"][0]["comment"] == "updated"
    finally:
        store.close()


def test_store_records_other_and_legacy_attachments(tmp_path: Path) -> None:
    store = Store(tmp_path)
    try:
        other = event("other-event")
        other["member"] = "other-child"
        other["member_display"] = "Other"
        store.publish_events([other], {"asset-1"})

        assert store.status()["attachment_status"] == {"legacy": 1, "pending": 1}
        assert [row["attachment_key"] for row in store.pending_attachments()] == ["asset-2"]
        assert (
            store.connection.execute(
                "SELECT member_display FROM events WHERE event_key='other-event'"
            ).fetchone()[0]
            == "Other"
        )
        store.publish_events([other], set())
        assert store.status()["attachment_status"] == {"legacy": 1, "pending": 1}
        store.publish_events([other], {"asset-2"})
        assert store.status()["attachment_status"] == {"legacy": 2}
    finally:
        store.close()


def test_v1_database_migrates_without_losing_state(tmp_path: Path) -> None:
    database = sqlite3.connect(tmp_path / "state.sqlite3")
    database.executescript(
        """
        CREATE TABLE events (
          event_key TEXT PRIMARY KEY, child_id TEXT NOT NULL, child_name TEXT NOT NULL,
          event_time REAL NOT NULL, event_date TEXT, payload_json TEXT NOT NULL,
          payload_hash TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL
        );
        CREATE TABLE attachments (
          attachment_key TEXT PRIMARY KEY, filename TEXT, mime_type TEXT, local_path TEXT,
          size_bytes INTEGER, sha256 TEXT, status TEXT NOT NULL DEFAULT 'pending', last_error TEXT
        );
        CREATE TABLE event_attachments (
          event_key TEXT NOT NULL REFERENCES events(event_key) ON DELETE CASCADE,
          attachment_key TEXT NOT NULL REFERENCES attachments(attachment_key) ON DELETE CASCADE,
          PRIMARY KEY(event_key,attachment_key)
        );
        CREATE TABLE sync_state (name TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE sync_runs (
          run_id TEXT PRIMARY KEY,since_epoch INTEGER NOT NULL,until_epoch INTEGER NOT NULL,
          started_at TEXT NOT NULL,completed_at TEXT,status TEXT NOT NULL,
          event_count INTEGER NOT NULL DEFAULT 0,downloaded_count INTEGER NOT NULL DEFAULT 0,
          error TEXT
        );
        INSERT INTO events VALUES(
          'event','child','Child',1,'2026-01-01','{}','hash','first','last'
        );
        INSERT INTO attachments VALUES(
          'asset','one.jpg','image/jpeg','objects/as/asset.jpg',5,'digest','complete',NULL
        );
        INSERT INTO event_attachments VALUES('event','asset');
        INSERT INTO sync_state VALUES('last_success_until','200');
        """
    )
    database.commit()
    database.close()

    store = Store(tmp_path)
    try:
        row = store.connection.execute("SELECT * FROM events").fetchone()
        assert row["member_id"] == "child"
        assert row["member_display"] is None
        assert "is_target_child" not in row
        assert store.connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert store.last_success_until() == 200
        assert store.attachment_count() == 1
        assert store.connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert len(list((tmp_path / "backups").glob("state-v1-*.sqlite3"))) == 1
    finally:
        store.close()


def test_v2_database_removes_local_child_classification(tmp_path: Path) -> None:
    database = sqlite3.connect(tmp_path / "state.sqlite3")
    database.executescript(
        """
        CREATE TABLE events (
          event_key TEXT PRIMARY KEY, member_id TEXT, member_display TEXT,
          is_target_child INTEGER NOT NULL, target_child_name TEXT,
          event_time REAL NOT NULL, event_date TEXT, payload_json TEXT NOT NULL,
          payload_hash TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL
        );
        CREATE TABLE attachments (
          attachment_key TEXT PRIMARY KEY, filename TEXT, mime_type TEXT, local_path TEXT,
          size_bytes INTEGER, sha256 TEXT, status TEXT NOT NULL, legacy_covered INTEGER NOT NULL,
          last_error TEXT
        );
        CREATE TABLE event_attachments (
          event_key TEXT NOT NULL REFERENCES events(event_key) ON DELETE CASCADE,
          attachment_key TEXT NOT NULL REFERENCES attachments(attachment_key) ON DELETE CASCADE,
          PRIMARY KEY(event_key,attachment_key)
        );
        CREATE TABLE sync_state (name TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE sync_runs (
          run_id TEXT PRIMARY KEY,since_epoch INTEGER NOT NULL,until_epoch INTEGER NOT NULL,
          started_at TEXT NOT NULL,completed_at TEXT,status TEXT NOT NULL,
          event_count INTEGER NOT NULL DEFAULT 0,downloaded_count INTEGER NOT NULL DEFAULT 0,
          error TEXT
        );
        INSERT INTO events VALUES(
          'event','member','Member',1,'Local Label',1,'1970-01-01','{}','hash','first','last'
        );
        INSERT INTO attachments VALUES(
          'asset','one.jpg','image/jpeg',NULL,NULL,NULL,'pending',0,NULL
        );
        INSERT INTO event_attachments VALUES('event','asset');
        PRAGMA user_version=2;
        """
    )
    database.commit()
    database.close()

    store = Store(tmp_path)
    try:
        row = store.connection.execute("SELECT * FROM events").fetchone()
        assert row["member_id"] == "member"
        assert row["member_display"] == "Member"
        assert "is_target_child" not in row
        assert "target_child_name" not in row
        assert store.connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert store.attachment_count() == 1
        assert store.connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert len(list((tmp_path / "backups").glob("state-v2-*.sqlite3"))) == 1
    finally:
        store.close()


def test_v2_database_with_broken_foreign_keys_is_not_migrated(tmp_path: Path) -> None:
    database = sqlite3.connect(tmp_path / "state.sqlite3")
    database.executescript(
        """
        CREATE TABLE events (
          event_key TEXT PRIMARY KEY, member_id TEXT, member_display TEXT,
          is_target_child INTEGER NOT NULL, target_child_name TEXT,
          event_time REAL NOT NULL, event_date TEXT, payload_json TEXT NOT NULL,
          payload_hash TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL
        );
        CREATE TABLE attachments (
          attachment_key TEXT PRIMARY KEY, filename TEXT, mime_type TEXT, local_path TEXT,
          size_bytes INTEGER, sha256 TEXT, status TEXT NOT NULL, legacy_covered INTEGER NOT NULL,
          last_error TEXT
        );
        CREATE TABLE event_attachments (
          event_key TEXT NOT NULL REFERENCES events(event_key) ON DELETE CASCADE,
          attachment_key TEXT NOT NULL REFERENCES attachments(attachment_key) ON DELETE CASCADE,
          PRIMARY KEY(event_key,attachment_key)
        );
        INSERT INTO attachments VALUES(
          'asset','one.jpg','image/jpeg',NULL,NULL,NULL,'pending',0,NULL
        );
        INSERT INTO event_attachments VALUES('missing-event','asset');
        PRAGMA user_version=2;
        """
    )
    database.commit()
    database.close()

    with pytest.raises(StorageError, match="v2 外键检查失败"):
        Store(tmp_path)

    database = sqlite3.connect(tmp_path / "state.sqlite3")
    try:
        assert database.execute("PRAGMA user_version").fetchone()[0] == 2
        assert database.execute("SELECT COUNT(*) FROM event_attachments").fetchone()[0] == 1
    finally:
        database.close()


def test_watermark_advances_only_on_complete_run(tmp_path: Path) -> None:
    store = Store(tmp_path)
    try:
        store.start_run("failed", 100, 200)
        store.fail_run("failed", "network")
        assert store.last_success_until() is None

        store.start_run("complete", 100, 200)
        store.complete_run("complete", 200, 0, 0)
        assert store.last_success_until() == 200

        store.start_run("historical", 50, 100)
        store.complete_run("historical", 100, 0, 0)
        assert store.last_success_until() == 200
    finally:
        store.close()


def test_missing_complete_attachment_becomes_pending(tmp_path: Path) -> None:
    store = Store(tmp_path)
    try:
        original = event()
        original["member"] = "child-1"
        store.publish_events([original])
        object_path = tmp_path / "objects/as/asset-1.jpg"
        object_path.parent.mkdir(parents=True)
        object_path.write_bytes(b"image")
        store.mark_attachment_complete(
            "asset-1", "objects/as/asset-1.jpg", 5, "digest", "image/jpeg"
        )
        assert [row["attachment_key"] for row in store.pending_attachments()] == ["asset-2"]

        object_path.unlink()
        assert {row["attachment_key"] for row in store.pending_attachments()} == {
            "asset-1",
            "asset-2",
        }
    finally:
        store.close()
