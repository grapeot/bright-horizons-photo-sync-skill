import os
import sys

import pytest

from bright_horizons_photo_sync.photos import list_photos_albums

pytestmark = pytest.mark.photos_live


@pytest.mark.skipif(sys.platform != "darwin", reason="Photos live test 只支持 macOS")
def test_can_read_real_photos_album_names() -> None:
    if os.getenv("BH_ENABLE_PHOTOS_LIVE_TESTS") != "1":
        pytest.skip("设置 BH_ENABLE_PHOTOS_LIVE_TESTS=1 才读取本机 Photos albums")
    albums = list_photos_albums()
    assert albums
    assert all(isinstance(name, str) and name for name in albums)
