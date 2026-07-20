from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import Config
from .ranges import TimeRange

EVENTS_URL = "https://mybrightday.brighthorizons.com/remote/v1/events"
ATTACHMENT_URL = "https://mybrightday.brighthorizons.com/remote/v1/obj_attachment"


class SyncError(RuntimeError):
    exit_code = 4


class AuthenticationError(SyncError):
    exit_code = 3


class IntegrityError(SyncError):
    exit_code = 5


class StorageError(SyncError):
    exit_code = 6


@dataclass
class AttachmentResponse:
    content_type: str
    chunks: Iterator[bytes]


class MyBrightDayClient:
    def __init__(self, config: Config, session: requests.Session | None = None) -> None:
        self.config = config
        self.session = session or self._build_session()

    @staticmethod
    def _build_session() -> requests.Session:
        session = requests.Session()
        retry = Retry(
            total=3,
            connect=3,
            read=3,
            status=3,
            backoff_factor=0.8,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            respect_retry_after_header=True,
        )
        session.mount("https://", HTTPAdapter(max_retries=retry))
        return session

    def _headers(self, *, image: bool = False) -> dict[str, str]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:152.0) "
                "Gecko/20100101 Firefox/152.0"
            ),
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Cookie": self.config.session_cookie,
            "Referer": self.config.referer,
            "Sec-Fetch-Site": "same-origin",
        }
        if image:
            headers.update({"Sec-Fetch-Dest": "image", "Sec-Fetch-Mode": "no-cors"})
        else:
            headers.update(
                {
                    "X-TADPOLES-UID": self.config.tadpoles_uid,
                    "X-Requested-With": "XMLHttpRequest",
                    "Sec-Fetch-Dest": "empty",
                    "Sec-Fetch-Mode": "cors",
                }
            )
        return headers

    @staticmethod
    def _raise_for_status(response: requests.Response) -> None:
        if response.status_code in (401, 403):
            raise AuthenticationError(
                "My Bright Day 认证失效；请从 Family Information Center 重新进入并更新 .env"
            )
        try:
            response.raise_for_status()
        except requests.RequestException as exc:
            raise SyncError(f"HTTP 请求失败：{response.status_code}") from exc

    def _get(self, url: str, **kwargs: Any) -> requests.Response:
        try:
            return self.session.get(url, **kwargs)
        except requests.RequestException as exc:
            raise SyncError("HTTP 请求失败；已隐藏可能包含签名的 URL") from exc

    def fetch_events(self, time_range: TimeRange, limit: int = 300) -> list[dict[str, Any]]:
        response = self._get(
            EVENTS_URL,
            params={
                "direction": "range",
                "earliest_event_time": time_range.start,
                "latest_event_time": time_range.api_latest,
                "num_events": limit,
                "client": "dashboard",
            },
            headers=self._headers(),
            timeout=self.config.timeout_seconds,
        )
        self._raise_for_status(response)
        content_type = response.headers.get("content-type", "").lower()
        if "json" not in content_type:
            raise AuthenticationError("事件接口返回的不是 JSON，浏览器 session 可能已经失效")
        try:
            payload = response.json()
        except ValueError as exc:
            raise IntegrityError("事件接口返回无效 JSON") from exc
        events = payload.get("events") if isinstance(payload, dict) else None
        if not isinstance(events, list):
            raise IntegrityError("事件响应缺少 events 数组")
        self._cache_events(time_range, limit, events)
        return events

    def _cache_events(
        self, time_range: TimeRange, limit: int, events: list[dict[str, Any]]
    ) -> None:
        cache_dir = self.config.data_dir / "cache/api/events"
        cache_dir.mkdir(parents=True, exist_ok=True)
        target = cache_dir / f"{time_range.start}_{time_range.end}_{limit}.json"
        temp = cache_dir / f".{target.name}.{uuid.uuid4().hex}.tmp"
        payload = {
            "schema_version": 1,
            "fetched_at": datetime.now(UTC).isoformat(),
            "range": {"start": time_range.start, "end": time_range.end},
            "limit": limit,
            "events": events,
        }
        try:
            with temp.open("x", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, target)
        except OSError as exc:
            raise StorageError(f"API response cache 写入失败：{exc}") from exc
        finally:
            temp.unlink(missing_ok=True)

    def download_attachment(self, key: str) -> tuple[requests.Response, Iterator[bytes]]:
        metadata_response = self._get(
            ATTACHMENT_URL,
            params={"obj": key, "key": key},
            headers=self._headers(image=True),
            timeout=self.config.timeout_seconds,
        )
        self._raise_for_status(metadata_response)
        content_type = metadata_response.headers.get("content-type", "").lower()
        if "json" not in content_type:
            raise IntegrityError("附件 metadata 接口返回的不是 JSON")
        try:
            metadata = metadata_response.json()
        except ValueError as exc:
            raise IntegrityError("附件 metadata 接口返回无效 JSON") from exc
        signed_url = metadata.get("signed_url") if isinstance(metadata, dict) else None
        if not isinstance(signed_url, str) or urlparse(signed_url).scheme != "https":
            raise IntegrityError("附件 metadata 缺少有效 HTTPS signed_url")
        response = self._get(
            signed_url,
            timeout=self.config.timeout_seconds,
            stream=True,
        )
        self._raise_for_status(response)
        return response, self._safe_chunks(response)

    @staticmethod
    def _safe_chunks(response: requests.Response) -> Iterator[bytes]:
        try:
            yield from response.iter_content(chunk_size=1024 * 256)
        except requests.RequestException as exc:
            raise SyncError("媒体流读取失败；已隐藏 signed URL") from exc
