import subprocess
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from bright_horizons_photo_sync.client import AuthenticationError, IntegrityError
from bright_horizons_photo_sync.media import (
    _download_one,
    build_photos_export,
    fix_formats,
    sync_media,
)
from bright_horizons_photo_sync.store import Store


class FakeResponse:
    def __init__(self, content_type: str):
        self.headers = {"content-type": content_type}


class FakeClient:
    def __init__(self, content_type="image/jpeg", chunks=None):
        self.content_type = content_type
        if chunks is None:
            output = BytesIO()
            Image.new("RGB", (2, 2), (10, 20, 30)).save(output, format="JPEG")
            chunks = [output.getvalue()]
        self.chunks = chunks

    def download_attachment(self, _key):
        return FakeResponse(self.content_type), iter(self.chunks)


class AuthenticationFailureClient:
    def __init__(self):
        self.calls = 0

    def download_attachment(self, _key):
        self.calls += 1
        raise AuthenticationError("expired")


class OneBadAttachmentClient:
    def __init__(self):
        self.calls: list[str] = []

    def download_attachment(self, key):
        self.calls.append(key)
        chunks = [b"bad"] if key == "bad" else [b"\xff\xd8\xffjpeg-data"]
        return FakeResponse("image/jpeg"), iter(chunks)


def seed_attachment(store: Store, key: str = "asset_1") -> None:
    store.publish_events(
        [
            {
                "key": "event_1",
                "event_time": 1,
                "event_date": "2026-01-01",
                "member": "child-1",
                "attachments": [key],
            }
        ]
    )


def test_download_is_verified_and_recorded(tmp_path: Path) -> None:
    store = Store(tmp_path)
    try:
        seed_attachment(store)
        _download_one(FakeClient(), store, tmp_path / "media", "asset_1")
        assert store.pending_attachments() == []
        result = store.verify(deep=True)
        assert result["ok"] is True
        assert not (tmp_path / "children").exists()
        assert list((tmp_path / "media").rglob("*.part")) == []
    finally:
        store.close()


@pytest.mark.parametrize("content_type", ["text/html", "application/json", ""])
def test_rejects_unexpected_content_type(tmp_path: Path, content_type: str) -> None:
    store = Store(tmp_path)
    try:
        seed_attachment(store)
        with pytest.raises(IntegrityError):
            _download_one(
                FakeClient(content_type=content_type),
                store,
                tmp_path / "media",
                "asset_1",
            )
    finally:
        store.close()


def test_rejects_empty_attachment(tmp_path: Path) -> None:
    store = Store(tmp_path)
    try:
        seed_attachment(store)
        with pytest.raises(IntegrityError, match="空文件"):
            _download_one(FakeClient(chunks=[]), store, tmp_path / "media", "asset_1")
        assert list(tmp_path.rglob("*.part")) == []
    finally:
        store.close()


def test_rejects_unsafe_key(tmp_path: Path) -> None:
    store = Store(tmp_path)
    try:
        with pytest.raises(IntegrityError, match="非法"):
            _download_one(FakeClient(), store, tmp_path / "media", "../escape")
    finally:
        store.close()


def test_authentication_failure_stops_download_queue(tmp_path: Path) -> None:
    store = Store(tmp_path)
    try:
        seed_attachment(store)
        client = AuthenticationFailureClient()
        with pytest.raises(AuthenticationError):
            sync_media(client, store)
        assert client.calls == 1
        assert store.status()["attachment_status"] == {"pending": 1}
    finally:
        store.close()


def test_bad_attachment_does_not_starve_remaining_queue(tmp_path: Path) -> None:
    store = Store(tmp_path)
    try:
        store.publish_events(
            [
                {
                    "key": "event",
                    "event_time": 1,
                    "member": "other",
                    "attachments": ["bad", "good"],
                }
            ]
        )
        client = OneBadAttachmentClient()
        with pytest.raises(IntegrityError):
            sync_media(client, store)
        assert client.calls == ["bad", "good"]
        assert store.status()["attachment_status"] == {"complete": 1, "failed": 1}
    finally:
        store.close()


def test_sync_does_not_touch_deprecated_children_view(tmp_path: Path) -> None:
    store = Store(tmp_path)
    try:
        stale = tmp_path / "children/Other/2026/stale.jpg"
        stale.parent.mkdir(parents=True)
        stale.write_bytes(b"stale")
        before = stale.stat()

        seed_attachment(store)
        _download_one(FakeClient(), store, tmp_path / "objects", "asset_1")

        assert stale.read_bytes() == b"stale"
        assert stale.stat().st_ino == before.st_ino
        assert list((tmp_path / "children").rglob("*.jpg")) == [stale]
    finally:
        store.close()


def test_photos_export_converts_canonical_image_with_event_date(tmp_path: Path) -> None:
    store = Store(tmp_path)
    try:
        store.publish_events(
            [
                {
                    "key": "other-event",
                    "event_time": 1,
                    "member": "other",
                    "attachments": ["new_asset"],
                }
            ]
        )
        _download_one(FakeClient(), store, tmp_path / "objects", "new_asset")

        result = build_photos_export(store, workers=1)
        source = tmp_path / "objects/ne/new_asset.jpg"
        view = tmp_path / "exports/photos_import_all/new_asset.jpg"
        assert result["media"] == 1
        assert result["copied"] == 1
        assert not view.samefile(source)
        with Image.open(view) as compatible:
            assert compatible.format == "JPEG"
            exif_ifd = compatible.getexif().get_ifd(34665)
            assert exif_ifd[36867] == "1969:12:31 16:00:01"
            assert exif_ifd[36881] == "-08:00"
        validation = subprocess.run(
            ["exiftool", "-validate", "-T", str(view)],
            check=True,
            capture_output=True,
            text=True,
        )
        assert validation.stdout.strip() == "OK"

        replacement = source.with_suffix(".replacement")
        Image.new("RGB", (3, 3), (30, 20, 10)).save(replacement, format="JPEG")
        replacement.replace(source)
        store.mark_attachment_complete(
            "new_asset",
            "objects/ne/new_asset.jpg",
            source.stat().st_size,
            "replacement-digest",
            "image/jpeg",
        )
        rebuilt = build_photos_export(store, workers=1)
        assert rebuilt["copied"] == 1
        assert not view.samefile(source)
    finally:
        store.close()


def test_photos_export_converts_png_to_dated_jpeg(tmp_path: Path) -> None:
    store = Store(tmp_path)
    try:
        store.publish_events(
            [
                {
                    "key": "other-event",
                    "event_time": 1,
                    "member": "other",
                    "attachments": ["png_asset"],
                }
            ]
        )
        source = tmp_path / "objects/pn/png_asset.png"
        source.parent.mkdir(parents=True)
        Image.new("RGBA", (2, 2), (10, 20, 30, 255)).save(source, exif=b"test-exif")
        store.mark_attachment_complete(
            "png_asset",
            "objects/pn/png_asset.png",
            source.stat().st_size,
            "digest",
            "image/png",
        )

        result = build_photos_export(store, workers=1)
        target = tmp_path / "exports/photos_import_all/png_asset.jpg"
        with Image.open(source) as original, Image.open(target) as compatible:
            assert result["copied"] == 1
            assert compatible.format == "JPEG"
            assert compatible.mode == "RGB"
            exif_ifd = compatible.getexif().get_ifd(34665)
            assert exif_ifd[36867] == "1969:12:31 16:00:01"
            assert exif_ifd[36881] == "-08:00"
            assert target.stat().st_mtime == 1
            assert compatible.size == original.size
    finally:
        store.close()


def test_photos_export_uses_configured_timezone(tmp_path: Path) -> None:
    store = Store(tmp_path)
    try:
        seed_attachment(store)
        _download_one(FakeClient(), store, tmp_path / "objects", "asset_1")
        result = build_photos_export(store, workers=1, timezone="Asia/Shanghai")
        with Image.open(tmp_path / "exports/photos_import_all/asset_1.jpg") as exported:
            exif_ifd = exported.getexif().get_ifd(34665)
            assert exif_ifd[36867] == "1970:01:01 08:00:01"
            assert exif_ifd[36881] == "+08:00"
        assert result["timezone"] == "Asia/Shanghai"
    finally:
        store.close()


def test_photos_export_includes_legacy_jpeg_with_original_date(tmp_path: Path) -> None:
    store = Store(tmp_path)
    try:
        legacy = tmp_path / "legacy/photos_by_date/20240401_01.jpg"
        legacy.parent.mkdir(parents=True)
        exif = Image.Exif()
        exif[36867] = "2024:04:01 10:24:59"
        Image.new("RGB", (2, 2), (20, 30, 40)).save(legacy, exif=exif)

        result = build_photos_export(store, workers=1)
        target = tmp_path / "exports/photos_import_all/legacy_20240401_01.jpg"
        with Image.open(target) as compatible:
            assert result["media"] == 1
            assert compatible.getexif().get_ifd(34665)[36867] == "2024:04:01 10:24:59"
    finally:
        store.close()


def test_file_signature_overrides_incorrect_jpeg_header(tmp_path: Path) -> None:
    store = Store(tmp_path)
    try:
        seed_attachment(store)
        png = b"\x89PNG\r\n\x1a\n" + b"png-data"
        _download_one(
            FakeClient(content_type="image/jpeg", chunks=[png]),
            store,
            tmp_path / "objects",
            "asset_1",
        )
        row = store.complete_attachments()[0]
        assert row["mime_type"] == "image/png"
        assert row["local_path"].endswith(".png")
    finally:
        store.close()


def test_fix_formats_renames_existing_mislabeled_object(tmp_path: Path) -> None:
    store = Store(tmp_path)
    try:
        seed_attachment(store)
        source = tmp_path / "objects/as/asset_1.jpg"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"\x89PNG\r\n\x1a\nold-data")
        store.mark_attachment_complete(
            "asset_1", "objects/as/asset_1.jpg", source.stat().st_size, "digest", "image/jpeg"
        )

        result = fix_formats(store)
        row = store.complete_attachments()[0]
        assert result == {"scanned": 1, "renamed": 1, "metadata_updated": 1}
        assert not source.exists()
        assert (tmp_path / "objects/as/asset_1.png").is_file()
        assert row["local_path"] == "objects/as/asset_1.png"
        assert row["mime_type"] == "image/png"
    finally:
        store.close()
