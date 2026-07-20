from argparse import Namespace
from pathlib import Path

import pytest

from bright_horizons_photo_sync.cli import _resolve_sync_range, build_parser
from bright_horizons_photo_sync.config import ConfigError
from bright_horizons_photo_sync.store import Store


def test_explicit_range_cannot_create_watermark_gap(tmp_path: Path) -> None:
    store = Store(tmp_path)
    try:
        store.start_run("initial", 100, 200)
        store.complete_run("initial", 200, 0, 0)
        args = Namespace(
            since="2026-07-01",
            until="2026-07-02",
            timezone="America/Los_Angeles",
        )
        with pytest.raises(ConfigError, match="缺口"):
            _resolve_sync_range(args, store)
    finally:
        store.close()


def test_query_parser_supports_database_filters() -> None:
    args = build_parser().parse_args(
        ["query", "--status", "complete", "--mime-prefix", "image/", "--limit", "5"]
    )
    assert args.command == "query"
    assert args.status == "complete"
    assert args.mime_prefix == "image/"
    assert args.limit == 5


def test_operational_subcommands_are_available() -> None:
    parser = build_parser()
    assert parser.parse_args(["discover"]).command == "discover"
    assert parser.parse_args(["download"]).command == "download"
    assert parser.parse_args(["fix-formats"]).command == "fix-formats"
    assert parser.parse_args(["photos-export"]).command == "photos-export"
