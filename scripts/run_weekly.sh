#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
# sync 的唯一 contract 是保存 API 返回的全部可见事件和未被 legacy 覆盖的附件。
exec "$PROJECT_DIR/.venv/bin/bh-photo-sync" --env-file "$PROJECT_DIR/.env" sync
