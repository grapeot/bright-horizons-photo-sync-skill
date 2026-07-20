from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from .client import StorageError, SyncError
from .locking import photos_import_lock, photos_operation_lock
from .store import Store

OSXPHOTOS_VERSION = "0.76.1"
SUPPORTED_IMPORT_SUFFIXES = {".gif", ".jpeg", ".jpg", ".mov", ".mp4", ".png", ".webp"}
DRY_RUN_SUMMARY = re.compile(
    r"Done: imported (?P<imported>\d+) file groups?, "
    r"(?P<errors>\d+) errors?, (?P<skipped>\d+) skipped"
)


class PhotosImportError(SyncError):
    exit_code = 7


def list_photos_albums() -> list[str]:
    if sys.platform != "darwin":
        raise PhotosImportError("photos-import 只支持 macOS")
    script = 'const Photos = Application("Photos"); JSON.stringify(Photos.albums.name())'
    result = _run(
        ["/usr/bin/osascript", "-l", "JavaScript", "-e", script],
        error_context="读取 Photos albums",
    )
    try:
        albums = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise PhotosImportError("无法解析 Photos album 列表") from exc
    if not isinstance(albums, list) or not all(isinstance(name, str) for name in albums):
        raise PhotosImportError("Photos 返回了无效的 album 列表")
    return albums


def import_photos(store: Store, *, album: str, dry_run: bool = False) -> dict[str, object]:
    if not album.strip() or "\x00" in album:
        raise PhotosImportError("--album 必须是非空的 Photos album 名称")
    if "{" in album or "}" in album:
        raise PhotosImportError("--album 不能包含 osxphotos 模板字符 { 或 }")
    if sys.platform != "darwin":
        raise PhotosImportError("photos-import 只支持 macOS")

    try:
        with photos_operation_lock(store.data_dir):
            return _import_photos_locked(store, album=album, dry_run=dry_run)
    except StorageError as exc:
        raise PhotosImportError(str(exc)) from exc


def _import_photos_locked(
    store: Store, *, album: str, dry_run: bool
) -> dict[str, object]:

    export_dir = (store.data_dir / "exports/photos_import_all").resolve()
    if not export_dir.is_dir():
        raise PhotosImportError("Photos 导出目录不存在；请先运行 photos-export")
    entries = sorted(export_dir.iterdir())
    invalid_entries = [
        path
        for path in entries
        if path.is_symlink()
        or not path.is_file()
        or path.suffix.lower() not in SUPPORTED_IMPORT_SUFFIXES
    ]
    if invalid_entries:
        raise PhotosImportError(
            f"Photos 导出目录包含不受支持的项目：{invalid_entries[0].name}"
        )
    candidates = entries
    if not candidates:
        raise PhotosImportError("Photos 导出目录中没有可导入的媒体")

    _check_osxphotos_version()
    library = _get_last_photos_library()
    with photos_import_lock():
        _open_photos_library(library)
        if _get_last_photos_library() != library:
            raise PhotosImportError("无法锁定目标 Photos library，请关闭其他 Photos 操作后重试")
        return _import_into_library(
            store,
            export_dir=export_dir,
            candidates=candidates,
            library=library,
            album=album,
            dry_run=dry_run,
        )


def _import_into_library(
    store: Store,
    *,
    export_dir: Path,
    candidates: list[Path],
    library: Path,
    album: str,
    dry_run: bool,
) -> dict[str, object]:
    matching_albums = [name for name in list_photos_albums() if name == album]
    if not matching_albums:
        raise PhotosImportError(f"Photos 中不存在精确名称为 {album!r} 的 album")
    if len(matching_albums) > 1:
        raise PhotosImportError(f"Photos 中有多个同名 album {album!r}，无法安全选择")

    state_dir = store.data_dir / "state/osxphotos"
    state_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "osxphotos",
        "import",
        str(export_dir),
        "--skip-dups",
        "--dup-albums",
        "--album",
        album,
        "--library",
        str(library),
        "--no-progress",
        "--theme",
        "plain",
    ]
    report_path: Path | None = None
    if dry_run:
        command.append("--dry-run")
    else:
        reports_dir = store.data_dir / "reports/photos_import"
        reports_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        report_path = reports_dir / f"photos_import_{timestamp}.json"
        command.extend(["--report", str(report_path)])

    if _get_last_photos_library() != library:
        raise PhotosImportError("当前 Photos library 在预检期间发生变化，请重试")
    result = _run(
        command,
        error_context="运行 osxphotos",
        extra_env={"XDG_DATA_HOME": str(state_dir)},
    )
    if _get_last_photos_library() != library:
        raise PhotosImportError("导入期间 Photos library 发生变化；请检查导入结果")
    if dry_run:
        counts = _parse_dry_run_summary(result.stdout)
    else:
        assert report_path is not None
        counts = _parse_import_report(report_path, album=album)
        if counts["errors"]:
            raise PhotosImportError(
                f"有 {counts['errors']} 个媒体导入失败；报告：{report_path}"
            )
    return {
        "mode": "dry-run" if dry_run else "import",
        "album": album,
        "library": str(library),
        "source": str(export_dir),
        "candidates": len(candidates),
        **counts,
        "report": str(report_path) if report_path is not None else None,
    }


def _check_osxphotos_version() -> None:
    result = _run(
        [sys.executable, "-m", "osxphotos", "--version"],
        error_context="检查 osxphotos",
    )
    if OSXPHOTOS_VERSION not in result.stdout.split():
        raise PhotosImportError(
            f"需要 osxphotos {OSXPHOTOS_VERSION}；请安装项目的 photos extra"
        )


def _get_last_photos_library() -> Path:
    result = _run(
        [sys.executable, "-m", "osxphotos", "list", "--json"],
        error_context="读取当前 Photos library",
    )
    try:
        payload = json.loads(result.stdout)
        raw_library = payload["last_library"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise PhotosImportError("无法确定当前 Photos library") from exc
    library = Path(raw_library).expanduser().resolve()
    if not library.is_dir() or library.suffix != ".photoslibrary":
        raise PhotosImportError(f"当前 Photos library 路径无效：{library}")
    return library


def _open_photos_library(library: Path) -> None:
    _run(
        ["/usr/bin/open", "-a", "Photos", str(library)],
        error_context="打开目标 Photos library",
    )


def _parse_dry_run_summary(output: str) -> dict[str, int]:
    match = DRY_RUN_SUMMARY.search(output)
    if match is None:
        raise PhotosImportError("无法解析 osxphotos dry-run 结果")
    return {key: int(value) for key, value in match.groupdict().items()}


def _parse_import_report(report_path: Path, *, album: str | None = None) -> dict[str, int]:
    try:
        payload = json.loads(report_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise PhotosImportError(f"无法读取 osxphotos 报告：{report_path}") from exc
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise PhotosImportError("osxphotos 报告格式无效")
    counts = {
        "imported": sum(bool(item.get("imported")) for item in payload),
        "errors": sum(bool(item.get("error")) for item in payload),
        "skipped": sum(bool(item.get("skipped")) for item in payload),
    }
    if album is not None:
        missing_album = sum(
            not item.get("error") and album not in (item.get("albums") or []) for item in payload
        )
        if missing_album:
            raise PhotosImportError(
                f"有 {missing_album} 个媒体未加入目标 album；报告：{report_path}"
            )
    return counts


def _subprocess_environment(extra: dict[str, str] | None = None) -> dict[str, str]:
    allowed = {"HOME", "LANG", "LC_ALL", "LC_CTYPE", "PATH", "TMPDIR"}
    environment = {key: value for key, value in os.environ.items() if key in allowed}
    if extra:
        environment.update(extra)
    return environment


def _run(
    command: list[str], *, error_context: str, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            env=_subprocess_environment(extra_env),
        )
    except FileNotFoundError as exc:
        raise PhotosImportError(f"{error_context}失败：缺少命令 {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "未知错误").strip()
        raise PhotosImportError(f"{error_context}失败：{detail}") from exc
