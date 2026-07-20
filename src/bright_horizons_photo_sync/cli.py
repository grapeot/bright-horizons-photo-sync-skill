from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import timedelta
from pathlib import Path

from .client import MyBrightDayClient, SyncError
from .config import Config, ConfigError, load_environment, resolve_data_dir, resolve_timezone
from .media import build_photos_export, fix_formats, sync_media
from .photos import PhotosImportError, import_photos
from .ranges import DEFAULT_TIMEZONE, TimeRange, epoch_to_date, parse_date_range, today_until
from .store import Store
from .sync import run_sync


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bh-photo-sync", description="My Bright Day 照片增量备份")
    parser.add_argument("--env-file", type=Path, help="本地 .env 路径")
    parser.add_argument("--data-dir", type=Path, help="repo 外的私有数据目录")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_parser = subparsers.add_parser("sync", help="同步事件和媒体")
    sync_parser.add_argument("--since", help="包含的起始日 YYYY-MM-DD")
    sync_parser.add_argument("--until", help="不包含的结束日 YYYY-MM-DD")
    sync_parser.add_argument("--timezone", help=f"IANA 时区（默认 {DEFAULT_TIMEZONE}）")
    sync_parser.add_argument("--lookback-days", type=int, default=14)
    sync_parser.add_argument("--events-only", action="store_true")

    discover_parser = subparsers.add_parser("discover", help="只发现事件和附件，不下载媒体")
    discover_parser.add_argument("--since", help="包含的起始日 YYYY-MM-DD")
    discover_parser.add_argument("--until", help="不包含的结束日 YYYY-MM-DD")
    discover_parser.add_argument("--timezone", help=f"IANA 时区（默认 {DEFAULT_TIMEZONE}）")
    discover_parser.add_argument("--lookback-days", type=int, default=14)

    subparsers.add_parser("download", help="断点续传数据库中的 pending/failed 附件")
    subparsers.add_parser("fix-formats", help="按文件签名修正规范对象的扩展名和 MIME")
    subparsers.add_parser("status", help="显示本地同步状态")
    verify_parser = subparsers.add_parser("verify", help="验证媒体对象")
    verify_parser.add_argument("--deep", action="store_true", help="重新计算全部 SHA-256")
    subparsers.add_parser("export", help="从 SQLite 重建 JSON 和 CSV 导出")
    query_parser = subparsers.add_parser("query", help="按数据库分类查询附件")
    query_parser.add_argument("--status", choices=("pending", "complete", "failed", "legacy"))
    query_parser.add_argument("--mime-prefix", help="例如 image/ 或 video/")
    query_parser.add_argument("--limit", type=int, default=100)
    photos_parser = subparsers.add_parser(
        "photos-export", help="合并规范库和 Legacy，生成 Photos 导入目录"
    )
    photos_parser.add_argument(
        "--timezone", help=f"EXIF 使用的 IANA 时区（默认 {DEFAULT_TIMEZONE}）"
    )
    photos_import_parser = subparsers.add_parser(
        "photos-import", help="将 Photos 导出目录导入指定的现有 album"
    )
    photos_import_parser.add_argument("--album", required=True, help="现有 Photos album 的精确名称")
    photos_import_parser.add_argument(
        "--dry-run", action="store_true", help="只检查待导入和重复数量，不修改 Photos"
    )
    return parser


def _emit(payload: object, json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    if isinstance(payload, dict):
        for key, value in payload.items():
            print(f"{key}: {value}")
    else:
        print(payload)


def _resolve_sync_range(args: argparse.Namespace, store: Store) -> TimeRange:
    timezone = resolve_timezone(args.timezone)
    if bool(args.since) != bool(args.until):
        raise ConfigError("--since 和 --until 必须同时提供")
    if args.since:
        requested = parse_date_range(args.since, args.until, timezone)
        last_until = store.last_success_until()
        if last_until is not None and requested.start > last_until:
            raise ConfigError(
                "显式区间从当前水位之后开始，会制造永久缺口；请让 --since 不晚于当前水位"
            )
        return requested
    last_until = store.last_success_until()
    if last_until is None:
        raise ConfigError("首次同步必须显式提供 --since 和 --until")
    since_date = epoch_to_date(last_until, timezone)
    from datetime import date

    since = (date.fromisoformat(since_date) - timedelta(days=args.lookback_days)).isoformat()
    return parse_date_range(since, today_until(timezone), timezone)


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        load_environment(args.env_file)
        data_dir = resolve_data_dir(args.data_dir)
        store = Store(data_dir)
        try:
            if args.command == "sync":
                config = Config.load(env_file=args.env_file, data_dir=data_dir)
                time_range = _resolve_sync_range(args, store)
                result = run_sync(
                    MyBrightDayClient(config), store, time_range, events_only=args.events_only
                )
                _emit(result.__dict__, args.json)
            elif args.command == "discover":
                config = Config.load(env_file=args.env_file, data_dir=data_dir)
                time_range = _resolve_sync_range(args, store)
                result = run_sync(MyBrightDayClient(config), store, time_range, events_only=True)
                _emit(result.__dict__, args.json)
            elif args.command == "download":
                config = Config.load(env_file=args.env_file, data_dir=data_dir)
                downloaded, failed = sync_media(MyBrightDayClient(config), store)
                _emit({"downloaded": downloaded, "failed": failed}, args.json)
                if failed:
                    raise SyncError(f"仍有 {failed} 个附件下载失败，可更新凭证后重跑")
            elif args.command == "fix-formats":
                _emit(fix_formats(store), args.json)
            elif args.command == "status":
                _emit(store.status(), args.json)
            elif args.command == "verify":
                result = store.verify(args.deep)
                _emit(result, args.json)
                if not result["ok"]:
                    raise SystemExit(6)
            elif args.command == "export":
                _emit(store.export(), args.json)
            elif args.command == "query":
                if args.limit < 1 or args.limit > 10_000:
                    raise ConfigError("--limit 必须在 1 到 10000 之间")
                _emit(
                    store.query_attachments(
                        status=args.status,
                        mime_prefix=args.mime_prefix,
                        limit=args.limit,
                    ),
                    args.json,
                )
            elif args.command == "photos-export":
                _emit(
                    build_photos_export(store, timezone=resolve_timezone(args.timezone)), args.json
                )
            elif args.command == "photos-import":
                _emit(import_photos(store, album=args.album, dry_run=args.dry_run), args.json)
        finally:
            store.close()
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    except PhotosImportError as exc:
        print(f"Photos 导入失败：{exc}", file=sys.stderr)
        raise SystemExit(exc.exit_code) from exc
    except SyncError as exc:
        print(f"同步失败：{exc}", file=sys.stderr)
        raise SystemExit(exc.exit_code) from exc
    except (OSError, sqlite3.Error) as exc:
        print(f"存储失败：{exc}", file=sys.stderr)
        raise SystemExit(6) from exc


if __name__ == "__main__":
    main()
