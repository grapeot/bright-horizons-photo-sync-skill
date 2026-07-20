import json
import subprocess
from pathlib import Path

import pytest

from bright_horizons_photo_sync.photos import (
    PhotosImportError,
    _batch_import_into_album,
    _parse_import_report,
    _photos_add_duplicate_groups,
    _photos_run_script,
    _subprocess_environment,
    import_photos,
)
from bright_horizons_photo_sync.store import Store


def completed(command: list[str], stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")


def prepare_export(tmp_path: Path) -> Store:
    export_dir = tmp_path / "exports/photos_import_all"
    export_dir.mkdir(parents=True)
    (export_dir / "photo.jpg").write_bytes(b"jpeg")
    return Store(tmp_path)


def test_photos_import_dry_run_builds_safe_command(tmp_path: Path, monkeypatch) -> None:
    store = prepare_export(tmp_path)
    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs["env"]))
        if command[0] == "/usr/bin/open":
            return completed(command)
        if command[0] == "/usr/bin/osascript":
            return completed(command, '["BrightHoizons"]\n')
        if command[-1] == "--version":
            return completed(command, "osxphotos, version 0.76.1\n")
        if command[-2:] == ["list", "--json"]:
            library = tmp_path / "Photos Library.photoslibrary"
            library.mkdir(exist_ok=True)
            return completed(command, json.dumps({"last_library": str(library)}))
        return completed(command, "Done: imported 1 file group, 0 errors, 0 skipped\n")

    monkeypatch.setattr("bright_horizons_photo_sync.photos.sys.platform", "darwin")
    monkeypatch.setattr("bright_horizons_photo_sync.photos.subprocess.run", fake_run)
    monkeypatch.setenv("BH_SESSION_COOKIE", "must-not-leak")
    try:
        result = import_photos(store, album="BrightHoizons", dry_run=True)
    finally:
        store.close()

    import_command, environment = next(
        call for call in calls if call[0][1:4] == ["-m", "osxphotos", "import"]
    )
    assert import_command[1:4] == ["-m", "osxphotos", "import"]
    assert import_command[-1] == "--dry-run"
    assert import_command[import_command.index("--album") + 1] == "BrightHoizons"
    assert "--resume" not in import_command
    assert "BH_SESSION_COOKIE" not in environment
    assert result["imported"] == 1
    assert result["mode"] == "dry-run"


def test_photos_import_requires_exact_existing_album(tmp_path: Path, monkeypatch) -> None:
    store = prepare_export(tmp_path)

    def fake_run(command, **_kwargs):
        if command[0] == "/usr/bin/open":
            return completed(command)
        if command[0] == "/usr/bin/osascript":
            return completed(command, '["Bright Horizons"]\n')
        if command[-1] == "--version":
            return completed(command, "osxphotos, version 0.76.1\n")
        library = tmp_path / "Photos Library.photoslibrary"
        library.mkdir(exist_ok=True)
        return completed(command, json.dumps({"last_library": str(library)}))

    monkeypatch.setattr("bright_horizons_photo_sync.photos.sys.platform", "darwin")
    monkeypatch.setattr("bright_horizons_photo_sync.photos.subprocess.run", fake_run)
    try:
        with pytest.raises(PhotosImportError, match="不存在精确名称"):
            import_photos(store, album="BrightHoizons")
    finally:
        store.close()


def test_photos_import_rejects_ambiguous_album(tmp_path: Path, monkeypatch) -> None:
    store = prepare_export(tmp_path)

    def fake_run(command, **_kwargs):
        if command[0] == "/usr/bin/open":
            return completed(command)
        if command[0] == "/usr/bin/osascript":
            return completed(command, '["Same Name", "Same Name"]\n')
        if command[-1] == "--version":
            return completed(command, "osxphotos, version 0.76.1\n")
        library = tmp_path / "Photos Library.photoslibrary"
        library.mkdir(exist_ok=True)
        return completed(command, json.dumps({"last_library": str(library)}))

    monkeypatch.setattr("bright_horizons_photo_sync.photos.sys.platform", "darwin")
    monkeypatch.setattr("bright_horizons_photo_sync.photos.subprocess.run", fake_run)
    try:
        with pytest.raises(PhotosImportError, match="多个同名"):
            import_photos(store, album="Same Name")
    finally:
        store.close()


def test_photos_import_requires_macos(tmp_path: Path, monkeypatch) -> None:
    store = prepare_export(tmp_path)
    monkeypatch.setattr("bright_horizons_photo_sync.photos.sys.platform", "linux")
    try:
        with pytest.raises(PhotosImportError, match="只支持 macOS"):
            import_photos(store, album="BrightHoizons")
    finally:
        store.close()


def test_photos_import_rejects_album_template(tmp_path: Path, monkeypatch) -> None:
    store = prepare_export(tmp_path)
    monkeypatch.setattr("bright_horizons_photo_sync.photos.sys.platform", "darwin")
    try:
        with pytest.raises(PhotosImportError, match="模板字符"):
            import_photos(store, album="Family {created.year}")
    finally:
        store.close()


def test_photos_import_apply_parses_report(tmp_path: Path, monkeypatch) -> None:
    store = prepare_export(tmp_path)
    album_ids = iter([set(), {"new-id"}])

    def fake_run(command, **_kwargs):
        if command[0] == "/usr/bin/open":
            return completed(command)
        if command[0] == "/usr/bin/osascript":
            return completed(command, '["BrightHoizons"]\n')
        if command[-1] == "--version":
            return completed(command, "osxphotos, version 0.76.1\n")
        if command[-2:] == ["list", "--json"]:
            library = tmp_path / "Photos Library.photoslibrary"
            library.mkdir(exist_ok=True)
            return completed(command, json.dumps({"last_library": str(library)}))
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("bright_horizons_photo_sync.photos.sys.platform", "darwin")
    monkeypatch.setattr("bright_horizons_photo_sync.photos.subprocess.run", fake_run)
    monkeypatch.setattr(
        "bright_horizons_photo_sync.photos._find_duplicates",
        lambda candidates, library: {candidates[0]: []},
    )
    monkeypatch.setattr(
        "bright_horizons_photo_sync.photos._photos_album_id",
        lambda album: "album-id",
    )
    monkeypatch.setattr(
        "bright_horizons_photo_sync.photos._photos_album_photo_ids",
        lambda album_id: next(album_ids),
    )
    monkeypatch.setattr(
        "bright_horizons_photo_sync.photos._photos_import_batch",
        lambda album_id, paths: ["new-id"],
    )
    try:
        result = import_photos(store, album="BrightHoizons")
    finally:
        store.close()

    assert result["mode"] == "import"
    assert result["imported"] == 1
    assert result["skipped"] == 0
    assert result["errors"] == 0
    assert result["report"]


def test_photos_import_fails_on_partial_batch_result(tmp_path: Path, monkeypatch) -> None:
    store = prepare_export(tmp_path)

    def fake_run(command, **_kwargs):
        if command[0] == "/usr/bin/open":
            return completed(command)
        if command[0] == "/usr/bin/osascript":
            return completed(command, '["BrightHoizons"]\n')
        if command[-1] == "--version":
            return completed(command, "osxphotos, version 0.76.1\n")
        if command[-2:] == ["list", "--json"]:
            library = tmp_path / "Photos Library.photoslibrary"
            library.mkdir(exist_ok=True)
            return completed(command, json.dumps({"last_library": str(library)}))
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("bright_horizons_photo_sync.photos.sys.platform", "darwin")
    monkeypatch.setattr("bright_horizons_photo_sync.photos.subprocess.run", fake_run)
    monkeypatch.setattr(
        "bright_horizons_photo_sync.photos._find_duplicates",
        lambda candidates, library: {candidates[0]: []},
    )
    monkeypatch.setattr(
        "bright_horizons_photo_sync.photos._photos_album_id",
        lambda album: "album-id",
    )
    monkeypatch.setattr(
        "bright_horizons_photo_sync.photos._photos_album_photo_ids", lambda album_id: set()
    )
    monkeypatch.setattr(
        "bright_horizons_photo_sync.photos._photos_import_batch", lambda album_id, paths: []
    )
    try:
        with pytest.raises(PhotosImportError, match="仅返回 0/1"):
            import_photos(store, album="BrightHoizons")
    finally:
        store.close()


def test_batch_import_adds_existing_duplicate_to_album(tmp_path: Path, monkeypatch) -> None:
    store = prepare_export(tmp_path)
    candidate = tmp_path / "exports/photos_import_all/photo.jpg"
    library = tmp_path / "Photos Library.photoslibrary"
    library.mkdir()
    album_ids = iter([set(), {"duplicate-id"}])
    added: list[list[str]] = []

    monkeypatch.setattr(
        "bright_horizons_photo_sync.photos._find_duplicates",
        lambda candidates, library: {candidate: ["duplicate-id"]},
    )
    monkeypatch.setattr(
        "bright_horizons_photo_sync.photos._photos_album_id",
        lambda album: "album-id",
    )
    monkeypatch.setattr(
        "bright_horizons_photo_sync.photos._photos_album_photo_ids",
        lambda album_id: next(album_ids),
    )
    monkeypatch.setattr(
        "bright_horizons_photo_sync.photos._photos_add_to_album",
        lambda album_id, photo_ids: added.append(list(photo_ids)),
    )
    monkeypatch.setattr(
        "bright_horizons_photo_sync.photos._photos_import_batch",
        lambda album_id, paths: pytest.fail("duplicate must not be imported"),
    )
    monkeypatch.setattr(
        "bright_horizons_photo_sync.photos._get_last_photos_library",
        lambda: library,
    )
    try:
        result = _batch_import_into_album(
            store,
            candidates=[candidate],
            library=library,
            album="BrightHoizons",
            export_dir=candidate.parent,
        )
    finally:
        store.close()

    assert added == [["duplicate-id"]]
    assert result["imported"] == 0
    assert result["skipped"] == 1


def test_duplicate_album_repair_falls_back_to_another_match(monkeypatch) -> None:
    attempts: list[list[str]] = []

    def fake_add(album_id, photo_ids):
        attempts.append(list(photo_ids))
        if "stale-id" in photo_ids:
            raise PhotosImportError("stale")

    monkeypatch.setattr(
        "bright_horizons_photo_sync.photos._photos_add_to_album", fake_add
    )
    _photos_add_duplicate_groups("album-id", [["stale-id", "usable-id"]])

    assert attempts == [["stale-id"], ["stale-id"], ["usable-id"]]


def test_mutating_photos_script_disables_outer_retry(monkeypatch) -> None:
    import photoscript.script_loader

    observed: list[bool] = []

    def fake_run(name, *args):
        observed.append(photoscript.script_loader.RUNSCRIPT_CONFIG["retry_enabled"])
        return []

    monkeypatch.setattr(photoscript.script_loader, "run_script", fake_run)
    assert photoscript.script_loader.RUNSCRIPT_CONFIG["retry_enabled"] is True
    assert _photos_run_script("mutation", retry=False) == []
    assert observed == [False]
    assert photoscript.script_loader.RUNSCRIPT_CONFIG["retry_enabled"] is True


def test_import_report_errors_fail_the_operation(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    report.write_text(json.dumps([{"imported": False, "error": True, "skipped": False}]))
    assert _parse_import_report(report) == {"imported": 0, "errors": 1, "skipped": 0}


def test_import_report_requires_album_membership(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            [
                {
                    "imported": False,
                    "error": False,
                    "skipped": True,
                    "albums": [],
                }
            ]
        )
    )
    with pytest.raises(PhotosImportError, match="未加入目标 album"):
        _parse_import_report(report, album="BrightHoizons")


def test_subprocess_environment_drops_private_configuration(monkeypatch) -> None:
    monkeypatch.setenv("BH_SESSION_COOKIE", "secret")
    monkeypatch.setenv("TAVILY_API_KEY", "secret")
    monkeypatch.setenv("PATH", "/usr/bin")
    environment = _subprocess_environment({"XDG_DATA_HOME": "/tmp/state"})
    assert environment["PATH"] == "/usr/bin"
    assert environment["XDG_DATA_HOME"] == "/tmp/state"
    assert "BH_SESSION_COOKIE" not in environment
    assert "TAVILY_API_KEY" not in environment
