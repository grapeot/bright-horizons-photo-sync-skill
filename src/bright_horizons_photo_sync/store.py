from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from collections.abc import Iterable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .client import IntegrityError, StorageError

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS events (
    event_key TEXT PRIMARY KEY,
    member_id TEXT,
    member_display TEXT,
    event_time REAL NOT NULL,
    event_date TEXT,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attachments (
    attachment_key TEXT PRIMARY KEY,
    filename TEXT,
    mime_type TEXT,
    local_path TEXT,
    size_bytes INTEGER,
    sha256 TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    legacy_covered INTEGER NOT NULL DEFAULT 0 CHECK(legacy_covered IN (0,1)),
    last_error TEXT
);
CREATE TABLE IF NOT EXISTS event_attachments (
    event_key TEXT NOT NULL REFERENCES events(event_key) ON DELETE CASCADE,
    attachment_key TEXT NOT NULL REFERENCES attachments(attachment_key) ON DELETE CASCADE,
    PRIMARY KEY (event_key, attachment_key)
);
CREATE TABLE IF NOT EXISTS sync_state (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_runs (
    run_id TEXT PRIMARY KEY,
    since_epoch INTEGER NOT NULL,
    until_epoch INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,
    event_count INTEGER NOT NULL DEFAULT 0,
    downloaded_count INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_member ON events(member_id);
CREATE INDEX IF NOT EXISTS idx_events_time ON events(event_time);
CREATE INDEX IF NOT EXISTS idx_event_attachments_attachment
ON event_attachments(attachment_key);
DROP VIEW IF EXISTS v_attachment_context;
CREATE VIEW v_attachment_context AS
SELECT a.attachment_key,a.filename,a.mime_type,a.local_path,a.size_bytes,a.sha256,
       a.status,a.legacy_covered,e.event_key,e.event_time,e.event_date,e.member_id,
       e.member_display
FROM attachments a
LEFT JOIN event_attachments ea ON ea.attachment_key=a.attachment_key
LEFT JOIN events e ON e.event_key=ea.event_key;
DROP VIEW IF EXISTS v_attachments;
CREATE VIEW v_attachments AS
SELECT a.attachment_key,a.filename,a.mime_type,a.local_path,a.size_bytes,a.sha256,
       a.status,a.legacy_covered,COUNT(DISTINCT ea.event_key) AS event_count,
       MIN(e.event_time) AS first_event_time,MAX(e.event_time) AS last_event_time
FROM attachments a
LEFT JOIN event_attachments ea ON ea.attachment_key=a.attachment_key
LEFT JOIN events e ON e.event_key=ea.event_key
GROUP BY a.attachment_key;
PRAGMA user_version=3;
"""


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def member_display_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, dict):
        name = f"{value.get('first_name', '')} {value.get('last_name', '')}".strip()
        return name or canonical_json(value)
    if isinstance(value, (list, tuple)):
        return canonical_json(value)
    return str(value)


def extract_attachments(event: dict[str, Any]) -> list[dict[str, str | None]]:
    found: dict[str, dict[str, str | None]] = {}
    for key in event.get("attachments") or []:
        if isinstance(key, str) and key:
            found[key] = {"key": key, "filename": None, "mime_type": None}
    for field in ("new_attachments", "media"):
        for item in event.get(field) or []:
            if not isinstance(item, dict) or not item.get("key"):
                continue
            key = str(item["key"])
            previous = found.get(key, {})
            found[key] = {
                "key": key,
                "filename": item.get("filename") or previous.get("filename"),
                "mime_type": item.get("mime_type") or previous.get("mime_type"),
            }
    return list(found.values())


class Store:
    def __init__(self, data_dir: Path) -> None:
        try:
            self.data_dir = data_dir
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self.db_path = self.data_dir / "state.sqlite3"
            self.connection = sqlite3.connect(self.db_path)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA foreign_keys=ON")
            columns = self._table_columns("events")
            if columns and "child_id" in columns:
                if self.connection.execute("PRAGMA foreign_key_check").fetchone():
                    raise StorageError("SQLite v1 外键检查失败；拒绝迁移并保留原数据库")
                self._backup_v1_database()
                self._migrate_v1()
            elif columns and "is_target_child" in columns:
                if self.connection.execute("PRAGMA foreign_key_check").fetchone():
                    raise StorageError("SQLite v2 外键检查失败；拒绝迁移并保留原数据库")
                self._backup_database(2)
                self._migrate_v2()
            self.connection.executescript(SCHEMA)
        except StorageError:
            if hasattr(self, "connection"):
                with suppress(sqlite3.Error):
                    self.connection.close()
            raise
        except (OSError, sqlite3.Error) as exc:
            if hasattr(self, "connection"):
                with suppress(sqlite3.Error):
                    self.connection.close()
            raise StorageError(f"SQLite 初始化或迁移失败：{exc}") from exc

    def _table_columns(self, table: str) -> set[str]:
        return {row["name"] for row in self.connection.execute(f"PRAGMA table_info({table})")}

    def _backup_v1_database(self) -> None:
        self._backup_database(1)

    def _backup_database(self, version: int) -> None:
        backup_dir = self.data_dir / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        target = backup_dir / f"state-v{version}-{stamp}.sqlite3"
        suffix = 1
        while target.exists():
            target = backup_dir / f"state-v{version}-{stamp}-{suffix}.sqlite3"
            suffix += 1
        backup = sqlite3.connect(target)
        try:
            self.connection.backup(backup)
        finally:
            backup.close()

    def _migrate_v1(self) -> None:
        self.connection.commit()
        self.connection.execute("PRAGMA foreign_keys=OFF")
        try:
            self.connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE events_v3 (
                    event_key TEXT PRIMARY KEY,
                    member_id TEXT,
                    member_display TEXT,
                    event_time REAL NOT NULL,
                    event_date TEXT,
                    payload_json TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                );
                INSERT INTO events_v3(
                    event_key,member_id,member_display,
                    event_time,event_date,payload_json,payload_hash,first_seen_at,last_seen_at
                )
                SELECT event_key,child_id,NULL,event_time,event_date,
                       payload_json,payload_hash,first_seen_at,last_seen_at
                FROM events;
                CREATE TABLE event_attachments_v3 (
                    event_key TEXT NOT NULL REFERENCES events_v3(event_key) ON DELETE CASCADE,
                    attachment_key TEXT NOT NULL REFERENCES attachments(attachment_key)
                        ON DELETE CASCADE,
                    PRIMARY KEY(event_key,attachment_key)
                );
                INSERT INTO event_attachments_v3 SELECT * FROM event_attachments;
                DROP TABLE event_attachments;
                DROP TABLE events;
                ALTER TABLE events_v3 RENAME TO events;
                ALTER TABLE event_attachments_v3 RENAME TO event_attachments;
                ALTER TABLE attachments ADD COLUMN legacy_covered INTEGER NOT NULL DEFAULT 0
                    CHECK(legacy_covered IN (0,1));
                PRAGMA user_version=3;
                COMMIT;
                """
            )
        except sqlite3.Error:
            self.connection.rollback()
            raise
        finally:
            self.connection.execute("PRAGMA foreign_keys=ON")

    def _migrate_v2(self) -> None:
        self.connection.commit()
        self.connection.execute("PRAGMA foreign_keys=OFF")
        try:
            self.connection.executescript(
                """
                BEGIN IMMEDIATE;
                DROP VIEW IF EXISTS v_attachment_context;
                DROP VIEW IF EXISTS v_attachments;
                CREATE TABLE events_v3 (
                    event_key TEXT PRIMARY KEY,
                    member_id TEXT,
                    member_display TEXT,
                    event_time REAL NOT NULL,
                    event_date TEXT,
                    payload_json TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                );
                INSERT INTO events_v3(
                    event_key,member_id,member_display,event_time,event_date,
                    payload_json,payload_hash,first_seen_at,last_seen_at
                )
                SELECT event_key,member_id,member_display,event_time,event_date,
                       payload_json,payload_hash,first_seen_at,last_seen_at
                FROM events;
                CREATE TABLE event_attachments_v3 (
                    event_key TEXT NOT NULL REFERENCES events_v3(event_key) ON DELETE CASCADE,
                    attachment_key TEXT NOT NULL REFERENCES attachments(attachment_key)
                        ON DELETE CASCADE,
                    PRIMARY KEY(event_key,attachment_key)
                );
                INSERT INTO event_attachments_v3 SELECT * FROM event_attachments;
                DROP TABLE event_attachments;
                DROP TABLE events;
                ALTER TABLE events_v3 RENAME TO events;
                ALTER TABLE event_attachments_v3 RENAME TO event_attachments;
                PRAGMA user_version=3;
                COMMIT;
                """
            )
        except sqlite3.Error:
            self.connection.rollback()
            raise
        finally:
            self.connection.execute("PRAGMA foreign_keys=ON")

    def close(self) -> None:
        self.connection.close()

    def start_run(self, run_id: str, since_epoch: int, until_epoch: int) -> None:
        self.connection.execute(
            """
            INSERT INTO sync_runs(run_id,since_epoch,until_epoch,started_at,status)
            VALUES(?,?,?,?,?)
            """,
            (run_id, since_epoch, until_epoch, utc_now(), "running"),
        )
        self.connection.commit()

    def fail_run(self, run_id: str, error: str) -> None:
        self.connection.execute(
            "UPDATE sync_runs SET completed_at=?, status='failed', error=? WHERE run_id=?",
            (utc_now(), error[:1000], run_id),
        )
        self.connection.commit()

    def publish_events(
        self,
        events: Iterable[dict[str, Any]],
        legacy_attachment_keys: set[str] | None = None,
    ) -> int:
        now = utc_now()
        count = 0
        legacy_attachment_keys = legacy_attachment_keys or set()
        try:
            with self.connection:
                for event in events:
                    event_key = event.get("key")
                    event_time = event.get("event_time")
                    member_id = str(event.get("member") or "") or None
                    if not event_key or event_time is None:
                        raise IntegrityError("事件缺少稳定 key 或 event_time")
                    member_display = member_display_text(event.get("member_display"))
                    payload = canonical_json(event)
                    payload_hash = hashlib.sha256(payload.encode()).hexdigest()
                    self.connection.execute(
                        """
                        INSERT INTO events(
                          event_key,member_id,member_display,event_time,event_date,
                          payload_json,payload_hash,
                          first_seen_at,last_seen_at
                        )
                        VALUES(?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(event_key) DO UPDATE SET
                          member_id=excluded.member_id,
                          member_display=excluded.member_display,
                          event_time=excluded.event_time,
                          event_date=excluded.event_date,
                          payload_json=excluded.payload_json,
                          payload_hash=excluded.payload_hash,
                          last_seen_at=excluded.last_seen_at
                        """,
                        (
                            str(event_key),
                            member_id,
                            member_display,
                            float(event_time),
                            event.get("event_date"),
                            payload,
                            payload_hash,
                            now,
                            now,
                        ),
                    )
                    self.connection.execute(
                        "DELETE FROM event_attachments WHERE event_key=?",
                        (str(event_key),),
                    )
                    for attachment in extract_attachments(event):
                        key = str(attachment["key"])
                        legacy_covered = int(key in legacy_attachment_keys)
                        initial_status = "legacy" if legacy_covered else "pending"
                        self.connection.execute(
                            """
                            INSERT INTO attachments(
                              attachment_key,filename,mime_type,status,legacy_covered
                            ) VALUES(?,?,?,?,?)
                            ON CONFLICT(attachment_key) DO UPDATE SET
                              filename=COALESCE(excluded.filename,attachments.filename),
                              mime_type=COALESCE(excluded.mime_type,attachments.mime_type),
                              legacy_covered=MAX(excluded.legacy_covered,attachments.legacy_covered),
                              status=CASE
                                WHEN excluded.legacy_covered=1
                                  AND attachments.status IN ('pending','failed')
                                THEN 'legacy'
                                ELSE attachments.status
                              END
                            """,
                            (
                                key,
                                attachment["filename"],
                                attachment["mime_type"],
                                initial_status,
                                legacy_covered,
                            ),
                        )
                        self.connection.execute(
                            """
                            INSERT OR IGNORE INTO event_attachments(event_key,attachment_key)
                            VALUES(?,?)
                            """,
                            (str(event_key), key),
                        )
                    count += 1
        except sqlite3.Error as exc:
            raise StorageError(f"SQLite 写入失败：{exc}") from exc
        return count

    def pending_attachments(self) -> list[sqlite3.Row]:
        rows = list(self.connection.execute("SELECT * FROM attachments ORDER BY attachment_key"))
        pending: list[sqlite3.Row] = []
        for row in rows:
            if row["status"] in {"pending", "failed"}:
                pending.append(row)
                continue
            if row["status"] == "legacy":
                continue
            path = self.data_dir / row["local_path"] if row["local_path"] else None
            if not path or not path.is_file() or path.stat().st_size != row["size_bytes"]:
                self.connection.execute(
                    "UPDATE attachments SET status='pending',last_error=? WHERE attachment_key=?",
                    ("本地对象缺失或大小不匹配，等待重新下载", row["attachment_key"]),
                )
                pending.append(row)
        self.connection.commit()
        return pending

    def attachment_count(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM attachments").fetchone()[0])

    def complete_attachments(self) -> list[sqlite3.Row]:
        return list(
            self.connection.execute(
                "SELECT * FROM attachments WHERE status='complete' ORDER BY attachment_key"
            )
        )

    def mark_attachment_complete(
        self, key: str, local_path: str, size_bytes: int, sha256: str, mime_type: str
    ) -> None:
        self.connection.execute(
            """
            UPDATE attachments SET
              local_path=?,size_bytes=?,sha256=?,mime_type=?,status='complete',last_error=NULL
            WHERE attachment_key=?
            """,
            (local_path, size_bytes, sha256, mime_type, key),
        )
        self.connection.commit()

    def mark_attachment_failed(self, key: str, error: str) -> None:
        self.connection.execute(
            "UPDATE attachments SET status='failed',last_error=? WHERE attachment_key=?",
            (error[:1000], key),
        )
        self.connection.commit()

    def update_attachment_format(self, key: str, local_path: str, mime_type: str) -> None:
        try:
            self.connection.execute(
                "UPDATE attachments SET local_path=?,mime_type=? WHERE attachment_key=?",
                (local_path, mime_type, key),
            )
            self.connection.commit()
        except sqlite3.Error as exc:
            self.connection.rollback()
            raise StorageError(f"更新附件格式失败：{exc}") from exc

    def complete_run(
        self, run_id: str, until_epoch: int, event_count: int, downloaded_count: int
    ) -> None:
        with self.connection:
            current_until = self.last_success_until()
            published_until = max(current_until or until_epoch, until_epoch)
            self.connection.execute(
                """
                UPDATE sync_runs SET
                  completed_at=?,status='complete',event_count=?,downloaded_count=?
                WHERE run_id=?
                """,
                (utc_now(), event_count, downloaded_count, run_id),
            )
            self.connection.execute(
                """
                INSERT INTO sync_state(name,value) VALUES('last_success_until',?)
                ON CONFLICT(name) DO UPDATE SET value=excluded.value
                """,
                (str(published_until),),
            )

    def last_success_until(self) -> int | None:
        row = self.connection.execute(
            "SELECT value FROM sync_state WHERE name='last_success_until'"
        ).fetchone()
        return int(row["value"]) if row else None

    def status(self) -> dict[str, Any]:
        counts = {}
        for table in ("events", "attachments", "sync_runs"):
            counts[table] = self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        attachment_status = dict(
            self.connection.execute(
                "SELECT status,COUNT(*) FROM attachments GROUP BY status"
            ).fetchall()
        )
        last_run = self.connection.execute(
            "SELECT * FROM sync_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        return {
            **counts,
            "attachment_status": attachment_status,
            "last_success_until": self.last_success_until(),
            "last_run": dict(last_run) if last_run else None,
            "data_dir": str(self.data_dir),
        }

    def verify(self, deep: bool = False) -> dict[str, Any]:
        missing: list[str] = []
        invalid: list[str] = []
        for row in self.connection.execute(
            """
            SELECT attachment_key,local_path,size_bytes,sha256
            FROM attachments WHERE status='complete'
            """
        ):
            path = self.data_dir / row["local_path"]
            if not path.is_file():
                missing.append(row["attachment_key"])
                continue
            if path.stat().st_size != row["size_bytes"] or path.stat().st_size == 0:
                invalid.append(row["attachment_key"])
                continue
            if deep:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if digest != row["sha256"]:
                    invalid.append(row["attachment_key"])
        return {"ok": not missing and not invalid, "missing": missing, "invalid": invalid}

    def query_attachments(
        self,
        *,
        status: str | None = None,
        mime_prefix: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if status:
            clauses.append("a.status=?")
            parameters.append(status)
        if mime_prefix:
            clauses.append("a.mime_type LIKE ?")
            parameters.append(f"{mime_prefix}%")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(limit)
        rows = self.connection.execute(
            f"SELECT * FROM v_attachments a {where} "
            "ORDER BY a.last_event_time DESC,a.attachment_key LIMIT ?",
            parameters,
        )
        return [dict(row) for row in rows]

    def media_for_photos_export(self) -> list[sqlite3.Row]:
        return list(
            self.connection.execute(
                """
                SELECT attachment_key,local_path,mime_type,sha256,first_event_time
                FROM v_attachments
                WHERE status='complete'
                  AND (mime_type LIKE 'image/%' OR mime_type LIKE 'video/%')
                ORDER BY attachment_key
                """
            )
        )

    def export(self) -> dict[str, str | int]:
        export_dir = self.data_dir / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        rows = list(
            self.connection.execute("SELECT payload_json FROM events ORDER BY event_time,event_key")
        )
        events = [json.loads(row["payload_json"]) for row in rows]
        json_path = export_dir / "merged_events.json"
        csv_path = export_dir / "events_summary.csv"
        attachment_csv_path = export_dir / "event_attachments.csv"
        self._atomic_text(
            json_path,
            json.dumps(
                {"total_events": len(events), "events": events},
                ensure_ascii=False,
                indent=2,
            ),
        )
        temp_csv = csv_path.with_suffix(".csv.tmp")
        with temp_csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=(
                    "date",
                    "time",
                    "category",
                    "member_display",
                    "member_id",
                    "type",
                    "comment",
                    "key",
                ),
            )
            writer.writeheader()
            for event in events:
                member = event.get("member_display") or ""
                if isinstance(member, dict):
                    member = f"{member.get('first_name', '')} {member.get('last_name', '')}".strip()
                writer.writerow(
                    {
                        "date": event.get("event_date", ""),
                        "time": datetime.fromtimestamp(
                            float(event.get("event_time", 0))
                        ).isoformat(),
                        "category": event.get("category", ""),
                        "member_display": member,
                        "member_id": event.get("member", ""),
                        "type": event.get("type", ""),
                        "comment": event.get("comment", ""),
                        "key": event.get("key", ""),
                    }
                )
        temp_csv.replace(csv_path)
        temp_attachment_csv = attachment_csv_path.with_suffix(".csv.tmp")
        attachment_rows = list(
            self.connection.execute(
                "SELECT * FROM v_attachment_context ORDER BY attachment_key,event_time,event_key"
            )
        )
        with temp_attachment_csv.open("w", newline="", encoding="utf-8") as handle:
            fieldnames = (
                tuple(attachment_rows[0].keys())
                if attachment_rows
                else (
                    "attachment_key",
                    "event_key",
                )
            )
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(dict(row) for row in attachment_rows)
        temp_attachment_csv.replace(attachment_csv_path)
        return {
            "events": len(events),
            "attachments": self.attachment_count(),
            "json": str(json_path),
            "csv": str(csv_path),
            "attachment_csv": str(attachment_csv_path),
        }

    @staticmethod
    def _atomic_text(path: Path, content: str) -> None:
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(content, encoding="utf-8")
        temp.replace(path)
