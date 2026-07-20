from __future__ import annotations

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .client import StorageError


@contextmanager
def photos_operation_lock(data_dir: Path) -> Iterator[None]:
    lock_dir = data_dir / "state"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / "photos_operation.lock"
    with lock_path.open("a+") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise StorageError("另一个 photos-export 或 photos-import 正在运行") from exc
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


@contextmanager
def photos_import_lock() -> Iterator[None]:
    lock_dir = Path.home() / ".cache/bright-horizons-photo-sync"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / "photos_import.lock"
    with lock_path.open("a+") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise StorageError("另一个 Photos import 正在运行") from exc
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
