from pathlib import Path

import pytest

from bright_horizons_photo_sync.config import Config, ConfigError, resolve_timezone


def set_base_environment(monkeypatch) -> None:
    monkeypatch.setenv("BH_SESSION_COOKIE", "secret")
    monkeypatch.setenv("BH_TADPOLES_UID", "uid")
    monkeypatch.setenv("BH_DEPENDENT_ID", "dependent")


def test_config_requires_only_session_values(monkeypatch, tmp_path: Path) -> None:
    set_base_environment(monkeypatch)
    config = Config.load(data_dir=tmp_path)
    assert config.dependent_id == "dependent"


def test_timezone_can_come_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("BH_TIMEZONE", "Asia/Shanghai")
    assert resolve_timezone() == "Asia/Shanghai"


def test_invalid_timezone_is_rejected() -> None:
    with pytest.raises(ConfigError, match="无效时区"):
        resolve_timezone("not/a-zone")
    with pytest.raises(ConfigError, match="无效时区"):
        resolve_timezone("/absolute/path")
