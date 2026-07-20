from pathlib import Path

import pytest
import requests

from bright_horizons_photo_sync.client import AuthenticationError, MyBrightDayClient, SyncError
from bright_horizons_photo_sync.config import Config
from bright_horizons_photo_sync.ranges import TimeRange


class FakeResponse:
    def __init__(self, status=200, content_type="application/json", payload=None):
        self.status_code = status
        self.headers = {"content-type": content_type}
        self._payload = payload if payload is not None else {"events": []}

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload

    def iter_content(self, chunk_size):
        assert chunk_size > 0
        return iter([b"media"])


class FakeSession:
    def __init__(self, response):
        self.responses = response if isinstance(response, list) else [response]
        self.last_kwargs = None
        self.calls = []

    def get(self, *args, **kwargs):
        self.last_kwargs = kwargs
        self.calls.append((args, kwargs))
        return self.responses.pop(0)


def config(tmp_path: Path) -> Config:
    return Config("secret", "uid", "dependent", tmp_path)


def test_event_request_uses_inclusive_api_upper_bound(tmp_path: Path) -> None:
    session = FakeSession(FakeResponse())
    client = MyBrightDayClient(config(tmp_path), session=session)
    client.fetch_events(TimeRange(100, 200))
    assert session.last_kwargs["params"]["earliest_event_time"] == 100
    assert session.last_kwargs["params"]["latest_event_time"] == 199
    cache_files = list((tmp_path / "cache/api/events").glob("*.json"))
    assert len(cache_files) == 1
    assert cache_files[0].name == "100_200_300.json"


@pytest.mark.parametrize("status", [401, 403])
def test_authentication_failure_is_explicit(tmp_path: Path, status: int) -> None:
    client = MyBrightDayClient(config(tmp_path), session=FakeSession(FakeResponse(status=status)))
    with pytest.raises(AuthenticationError):
        client.fetch_events(TimeRange(100, 200))


def test_html_response_is_treated_as_expired_session(tmp_path: Path) -> None:
    response = FakeResponse(content_type="text/html")
    client = MyBrightDayClient(config(tmp_path), session=FakeSession(response))
    with pytest.raises(AuthenticationError):
        client.fetch_events(TimeRange(100, 200))


def test_attachment_follows_signed_url(tmp_path: Path) -> None:
    metadata = FakeResponse(
        payload={
            "signed_url": "https://storage.example.com/object?signature=secret",
            "mime_type": "image/jpeg",
            "expires_in": 60,
        }
    )
    media = FakeResponse(content_type="image/jpeg")
    session = FakeSession([metadata, media])
    client = MyBrightDayClient(config(tmp_path), session=session)

    response, chunks = client.download_attachment("asset")

    assert response is media
    assert b"".join(chunks) == b"media"
    assert session.calls[1][0][0].startswith("https://storage.example.com/object")
    assert "headers" not in session.calls[1][1]


def test_signed_url_network_error_is_redacted(tmp_path: Path) -> None:
    class BrokenSession(FakeSession):
        def get(self, *args, **kwargs):
            if args[0].startswith("https://storage.example.com"):
                raise requests.ConnectionError(f"failed {args[0]}")
            return super().get(*args, **kwargs)

    metadata = FakeResponse(
        payload={"signed_url": "https://storage.example.com/object?signature=secret"}
    )
    client = MyBrightDayClient(config(tmp_path), session=BrokenSession(metadata))

    with pytest.raises(SyncError) as captured:
        client.download_attachment("asset")
    assert "signature=secret" not in str(captured.value)
