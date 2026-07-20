import os
from datetime import date, timedelta
from pathlib import Path

import pytest

from bright_horizons_photo_sync.client import MyBrightDayClient
from bright_horizons_photo_sync.config import Config, resolve_timezone
from bright_horizons_photo_sync.ranges import parse_date_range, today_until
from bright_horizons_photo_sync.store import extract_attachments

pytestmark = pytest.mark.live


@pytest.mark.skipif(
    os.environ.get("BH_ENABLE_LIVE_TESTS") != "1",
    reason="设置 BH_ENABLE_LIVE_TESTS=1 才运行真实 API 测试",
)
def test_real_event_schema(tmp_path: Path) -> None:
    config = Config.load(data_dir=tmp_path)
    client = MyBrightDayClient(config)
    timezone = resolve_timezone()
    until = os.environ.get("BH_LIVE_UNTIL") or today_until(timezone)
    since = (
        os.environ.get("BH_LIVE_SINCE")
        or (date.fromisoformat(until) - timedelta(days=30)).isoformat()
    )
    events = client.fetch_events(parse_date_range(since, until, timezone))
    assert isinstance(events, list)
    assert all(isinstance(event, dict) for event in events)
    attachment = next(
        (attachment for event in events for attachment in extract_attachments(event)), None
    )
    if attachment is None:
        return
    response, chunks = client.download_attachment(str(attachment["key"]))
    try:
        assert response.headers.get("content-type", "").startswith(
            ("image/", "video/", "application/pdf")
        )
        assert len(next(chunk for chunk in chunks if chunk)) > 0
    finally:
        response.close()
