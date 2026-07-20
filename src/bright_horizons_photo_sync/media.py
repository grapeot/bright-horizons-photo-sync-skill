from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import uuid
from concurrent.futures import ProcessPoolExecutor
from contextlib import suppress
from datetime import UTC, datetime
from itertools import repeat
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image

from .client import AuthenticationError, IntegrityError, MyBrightDayClient, StorageError, SyncError
from .store import Store

CONTENT_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "application/pdf": ".pdf",
}
SAFE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")
SAFE_VIEW_FILE = re.compile(r"^[A-Za-z0-9_-]+\.(?:jpg|png|gif|webp|mp4|mov)$")
PHOTOS_EXPORT_VERSION = "bright-horizons-photo-sync/photos-export-v2"


def sync_media(client: MyBrightDayClient, store: Store) -> tuple[int, int]:
    downloaded = 0
    failed = 0
    first_error: SyncError | None = None
    media_dir = store.data_dir / "objects"
    for row in store.pending_attachments():
        key = row["attachment_key"]
        try:
            _download_one(client, store, media_dir, key)
            downloaded += 1
        except AuthenticationError:
            raise
        except SyncError as exc:
            store.mark_attachment_failed(key, str(exc))
            failed += 1
            if first_error is None:
                first_error = exc
    if first_error is not None:
        raise first_error
    return downloaded, failed


def build_photos_export(
    store: Store, *, workers: int | None = None, timezone: str = "America/Los_Angeles"
) -> dict[str, int | str]:
    photos_timezone = ZoneInfo(timezone)
    export_dir = store.data_dir / "exports/photos_import_all"
    export_dir.mkdir(parents=True, exist_ok=True)
    candidates: list[dict[str, object]] = []
    canonical_rows = store.media_for_photos_export()
    for row in canonical_rows:
        candidates.append(
            {
                "name": str(row["attachment_key"]),
                "source": store.data_dir / row["local_path"],
                "content_type": str(row["mime_type"]),
                "event_time": float(row["first_event_time"]),
                "sha256": str(row["sha256"]),
                "origin": "canonical",
                "timezone": timezone,
            }
        )

    legacy_skipped = 0
    legacy_dir = store.data_dir / "legacy/photos_by_date"
    if legacy_dir.exists():
        for source in sorted(legacy_dir.iterdir()):
            if not source.is_file() or source.name.startswith(".") or source.stat().st_size == 0:
                if source.is_file() and not source.name.startswith("."):
                    legacy_skipped += 1
                continue
            fallback = "video/mp4" if source.suffix.lower() == ".mp4" else "image/jpeg"
            try:
                content_type = sniff_content_type(source, fallback)
                event_time = _legacy_event_time(source, content_type, photos_timezone)
            except IntegrityError:
                legacy_skipped += 1
                continue
            if not (content_type.startswith("image/") or content_type.startswith("video/")):
                legacy_skipped += 1
                continue
            candidates.append(
                {
                    "name": f"legacy_{source.stem}",
                    "source": source,
                    "content_type": content_type,
                    "event_time": event_time,
                    "sha256": _sha256_file(source),
                    "origin": "legacy",
                    "timezone": timezone,
                }
            )

    copied = 0
    existing = 0
    duplicates = 0
    images = 0
    videos = 0
    unique_candidates: list[dict[str, object]] = []
    expected: set[str] = set()
    seen_hashes: set[str] = set()
    for candidate in candidates:
        source = candidate["source"]
        assert isinstance(source, Path)
        content_type = str(candidate["content_type"])
        source_sha256 = str(candidate["sha256"])
        if source_sha256 in seen_hashes:
            duplicates += 1
            continue
        seen_hashes.add(source_sha256)
        extension = (
            ".jpg" if content_type.startswith("image/") else CONTENT_EXTENSIONS[content_type]
        )
        target = export_dir / f"{candidate['name']}{extension}"
        candidate["target_name"] = target.name
        expected.add(target.name)
        images += int(content_type.startswith("image/"))
        videos += int(content_type.startswith("video/"))
        unique_candidates.append(candidate)

    workers = workers or max(1, (os.cpu_count() or 2) - 2)
    if workers == 1:
        outcomes = map(_export_photos_candidate, unique_candidates, repeat(export_dir))
    else:
        executor = ProcessPoolExecutor(max_workers=workers)
        outcomes = executor.map(
            _export_photos_candidate, unique_candidates, repeat(export_dir), chunksize=4
        )
    try:
        for outcome in outcomes:
            copied += int(outcome == "copied")
            existing += int(outcome == "existing")
    finally:
        if workers > 1:
            executor.shutdown()
    removed = 0
    for path in export_dir.iterdir():
        if path.is_file() and SAFE_VIEW_FILE.fullmatch(path.name) and path.name not in expected:
            path.unlink()
            removed += 1
    return {
        "path": str(export_dir),
        "media": copied + existing,
        "images": images,
        "videos": videos,
        "workers": workers,
        "timezone": timezone,
        "copied": copied,
        "existing": existing,
        "duplicates_skipped": duplicates,
        "pdfs_excluded": len(store.complete_attachments()) - len(canonical_rows),
        "legacy_invalid_skipped": legacy_skipped,
        "removed": removed,
    }


def _export_photos_candidate(candidate: dict[str, object], export_dir: Path) -> str:
    source = candidate["source"]
    assert isinstance(source, Path)
    target = export_dir / str(candidate["target_name"])
    content_type = str(candidate["content_type"])
    event_time = float(candidate["event_time"])
    source_sha256 = str(candidate["sha256"])
    timezone = str(candidate["timezone"])
    if (
        target.exists()
        and not target.samefile(source)
        and _is_current_photos_export(target, content_type, event_time, source_sha256, timezone)
    ):
        return "existing"
    temp = export_dir / f".{target.stem}.{uuid.uuid4().hex}.tmp{target.suffix}"
    try:
        if content_type.startswith("image/"):
            _write_photos_compatible_jpeg(source, temp, event_time, source_sha256, timezone)
        elif content_type in {"video/mp4", "video/quicktime"}:
            _write_photos_compatible_video(source, temp, event_time)
        else:
            shutil.copy2(source, temp)
        os.replace(temp, target)
    except OSError as exc:
        raise StorageError(f"创建 Photos 导出副本失败：{exc}") from exc
    finally:
        temp.unlink(missing_ok=True)
    return "copied"


def _is_current_photos_export(
    target: Path,
    content_type: str,
    event_time: float,
    source_sha256: str,
    timezone: str,
) -> bool:
    if not content_type.startswith("image/"):
        return abs(target.stat().st_mtime - event_time) < 1
    expected_date, expected_offset = _exif_datetime(event_time, timezone)
    try:
        with Image.open(target) as image:
            exif = image.getexif()
            exif_ifd = exif.get_ifd(34665)
            return (
                image.format == "JPEG"
                and image.mode == "RGB"
                and exif.get(305) == PHOTOS_EXPORT_VERSION
                and exif.get(270) == f"source-sha256:{source_sha256}"
                and exif_ifd.get(36867) == expected_date
                and exif_ifd.get(36881) == expected_offset
            )
    except (OSError, ValueError):
        return False


def _exif_datetime(event_time: float, timezone: str) -> tuple[str, str]:
    local = datetime.fromtimestamp(event_time, ZoneInfo(timezone))
    raw_offset = local.strftime("%z")
    return local.strftime("%Y:%m:%d %H:%M:%S"), f"{raw_offset[:3]}:{raw_offset[3:]}"


def _legacy_event_time(source: Path, content_type: str, timezone: ZoneInfo) -> float:
    if content_type.startswith("image/"):
        try:
            with Image.open(source) as image:
                exif = image.getexif()
                value = exif.get_ifd(34665).get(36867) or exif.get(36867) or exif.get(306)
            if not value:
                raise ValueError("missing EXIF date")
            local = datetime.strptime(str(value), "%Y:%m:%d %H:%M:%S").replace(tzinfo=timezone)
            return local.timestamp()
        except (OSError, ValueError) as exc:
            raise IntegrityError(f"Legacy 图片缺少有效拍摄时间：{source.name}") from exc
    try:
        local = datetime.strptime(source.stem[:8], "%Y%m%d").replace(hour=12, tzinfo=timezone)
    except ValueError as exc:
        raise IntegrityError(f"Legacy 视频文件名缺少有效日期：{source.name}") from exc
    return local.timestamp()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise StorageError(f"读取媒体 hash 失败：{path.name}") from exc
    return digest.hexdigest()


def _write_photos_compatible_jpeg(
    source: Path, target: Path, event_time: float, source_sha256: str, timezone: str
) -> None:
    try:
        with Image.open(source) as image:
            image.load()
            if image.mode in {"RGBA", "LA"}:
                alpha = image.getchannel("A")
                if alpha.getextrema() != (255, 255):
                    raise IntegrityError(f"图片包含实际透明像素，拒绝转为 JPEG：{source.name}")
            rgb = image.convert("RGB")
            date_value, offset_value = _exif_datetime(event_time, timezone)
            rgb.save(
                target,
                format="JPEG",
                quality=95,
                subsampling=0,
                optimize=True,
            )
            subprocess.run(
                [
                    "exiftool",
                    "-q",
                    "-q",
                    "-overwrite_original",
                    f"-DateTimeOriginal={date_value}",
                    f"-CreateDate={date_value}",
                    f"-ModifyDate={date_value}",
                    f"-OffsetTime={offset_value}",
                    f"-OffsetTimeOriginal={offset_value}",
                    f"-OffsetTimeDigitized={offset_value}",
                    f"-ExifImageWidth={rgb.width}",
                    f"-ExifImageHeight={rgb.height}",
                    "-YCbCrPositioning=Centered",
                    f"-Software={PHOTOS_EXPORT_VERSION}",
                    f"-ImageDescription=source-sha256:{source_sha256}",
                    str(target),
                ],
                check=True,
            )
            os.utime(target, (event_time, event_time))
    except IntegrityError:
        raise
    except FileNotFoundError as exc:
        raise StorageError("生成标准 EXIF 需要安装 exiftool") from exc
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        raise IntegrityError(f"无法生成 Photos 兼容 JPEG：{source.name}") from exc


def _write_photos_compatible_video(source: Path, target: Path, event_time: float) -> None:
    creation_time = datetime.fromtimestamp(event_time, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-i",
                str(source),
                "-map",
                "0",
                "-map_metadata",
                "0",
                "-c",
                "copy",
                "-metadata",
                f"creation_time={creation_time}",
                "-metadata:s",
                f"creation_time={creation_time}",
                str(target),
            ],
            check=True,
        )
        os.utime(target, (event_time, event_time))
    except FileNotFoundError as exc:
        raise StorageError("生成带拍摄时间的视频需要安装 ffmpeg") from exc
    except (OSError, subprocess.CalledProcessError) as exc:
        raise StorageError(f"无法写入视频拍摄时间：{source.name}") from exc


def sniff_content_type(path: Path, fallback: str) -> str:
    try:
        with path.open("rb") as handle:
            prefix = handle.read(16)
    except OSError as exc:
        raise StorageError(f"读取媒体签名失败：{exc}") from exc
    if prefix.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if prefix.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if prefix.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP":
        return "image/webp"
    if prefix.startswith(b"%PDF-"):
        return "application/pdf"
    if prefix[4:8] == b"ftyp" and fallback in {"video/mp4", "video/quicktime"}:
        return fallback
    raise IntegrityError(f"媒体文件签名与支持格式不匹配：{path.name}")


def fix_formats(store: Store) -> dict[str, int]:
    scanned = 0
    renamed = 0
    metadata_updated = 0
    for row in store.complete_attachments():
        scanned += 1
        source = store.data_dir / row["local_path"]
        content_type = sniff_content_type(source, str(row["mime_type"] or ""))
        expected = source.with_suffix(CONTENT_EXTENSIONS[content_type])
        old_relative = source.relative_to(store.data_dir).as_posix()
        new_relative = expected.relative_to(store.data_dir).as_posix()
        needs_metadata = row["mime_type"] != content_type or old_relative != new_relative
        if not needs_metadata:
            continue
        moved = False
        if expected != source:
            if expected.exists():
                raise StorageError(f"格式修复目标已存在：{expected.name}")
            try:
                os.replace(source, expected)
                moved = True
            except OSError as exc:
                raise StorageError(f"格式修复重命名失败：{exc}") from exc
        try:
            store.update_attachment_format(row["attachment_key"], new_relative, content_type)
        except StorageError:
            if moved:
                with suppress(OSError):
                    os.replace(expected, source)
            raise
        renamed += int(moved)
        metadata_updated += 1
    return {
        "scanned": scanned,
        "renamed": renamed,
        "metadata_updated": metadata_updated,
    }


def _download_one(client: MyBrightDayClient, store: Store, media_dir: Path, key: str) -> None:
    if not SAFE_KEY.fullmatch(key):
        raise IntegrityError(f"非法 attachment key：{key!r}")
    response, chunks = client.download_attachment(key)
    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower().strip()
    if content_type not in CONTENT_EXTENSIONS:
        raise IntegrityError(f"附件 {key} 返回不支持的 Content-Type：{content_type or 'missing'}")
    target_dir = media_dir / key[:2]
    target_dir.mkdir(parents=True, exist_ok=True)
    temp = target_dir / f".{key}.{uuid.uuid4().hex}.part"
    digest = hashlib.sha256()
    size = 0
    try:
        with temp.open("xb") as handle:
            for chunk in chunks:
                if not chunk:
                    continue
                handle.write(chunk)
                digest.update(chunk)
                size += len(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        if size == 0:
            raise IntegrityError(f"附件 {key} 是空文件")
        content_type = sniff_content_type(temp, content_type)
        target = target_dir / f"{key}{CONTENT_EXTENSIONS[content_type]}"
        os.replace(temp, target)
    except OSError as exc:
        raise StorageError(f"附件 {key} 落盘失败：{exc}") from exc
    finally:
        temp.unlink(missing_ok=True)
    relative = target.relative_to(store.data_dir).as_posix()
    store.mark_attachment_complete(key, relative, size, digest.hexdigest(), content_type)
