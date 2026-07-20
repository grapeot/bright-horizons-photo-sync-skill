from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv


class ConfigError(ValueError):
    pass


def default_data_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "data"


def load_environment(env_file: Path | None = None) -> None:
    path = env_file or Path(__file__).resolve().parents[2] / ".env"
    load_dotenv(path, override=False)


def resolve_data_dir(override: Path | None = None) -> Path:
    raw = override or os.environ.get("BH_DATA_DIR")
    return Path(raw).expanduser().resolve() if raw else default_data_dir()


def resolve_timezone(override: str | None = None) -> str:
    timezone = (override or os.environ.get("BH_TIMEZONE") or "America/Los_Angeles").strip()
    try:
        ZoneInfo(timezone)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ConfigError(f"无效时区：{timezone}") from exc
    return timezone


@dataclass(frozen=True)
class Config:
    session_cookie: str
    tadpoles_uid: str
    dependent_id: str
    data_dir: Path
    timeout_seconds: float = 45.0

    @classmethod
    def load(
        cls,
        *,
        env_file: Path | None = None,
        data_dir: Path | None = None,
    ) -> Config:
        load_environment(env_file)
        values = {
            "BH_SESSION_COOKIE": os.environ.get("BH_SESSION_COOKIE", "").strip(),
            "BH_TADPOLES_UID": os.environ.get("BH_TADPOLES_UID", "").strip(),
            "BH_DEPENDENT_ID": os.environ.get("BH_DEPENDENT_ID", "").strip(),
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise ConfigError(f"缺少配置：{', '.join(missing)}；请更新本地 .env")
        return cls(
            session_cookie=values["BH_SESSION_COOKIE"],
            tadpoles_uid=values["BH_TADPOLES_UID"],
            dependent_id=values["BH_DEPENDENT_ID"],
            data_dir=resolve_data_dir(data_dir),
        )

    @property
    def referer(self) -> str:
        return (
            "https://mybrightday.brighthorizons.com/dashboard/parents.html"
            f"?dependent_id={self.dependent_id}"
        )
