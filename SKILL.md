---
name: bright-horizons-photo-sync
description: Back up all My Bright Day events and media visible to an authorized Bright Horizons family account, resume interrupted downloads, verify local files, and prepare a macOS Photos import.
---

# Bright Horizons Photo Sync

Use this skill when a user wants a local backup of My Bright Day photos, videos, PDFs, or event metadata. The CLI uses an authenticated browser session for an account the user controls. It must not bypass login, MFA, authorization, or service access controls.

## Success criteria

- The user chooses the local data directory and requested date range.
- Authentication values are written only to a local, Git-ignored `.env`; they never appear in chat, command output, issues, or commits.
- `discover` covers the complete requested half-open range without advancing the watermark after a partial failure.
- `download` reaches zero pending or failed attachments, or reports the exact unresolved failure.
- `verify --deep` reports `ok: True` before the backup is declared complete.
- If requested, `photos-export` creates a reproducible import directory with the correct IANA timezone.

## Boundaries

- Download every event and attachment returned by the authenticated API. A `member` association is metadata, not an ownership or filtering boundary.
- Tell the user that the account may expose class-shared or otherwise unassigned events before starting a broad backfill.
- Do not ask the user to paste cookies, cURL requests, bearer tokens, JWT redirect URLs, names, or dependent IDs into the conversation.
- Do not automate password or MFA entry. The user completes login in a visible browser.
- Do not upload, publish, or delete downloaded media. `data/` and `.env` remain local.
- Treat `401`, `403`, HTML login responses, truncation, and storage failures as hard failures.

## Authentication

The supported V0 flow is interactive and local:

1. Have the user open `https://familyinfocenter.brighthorizons.com/`, log in, and enter My Bright Day from a child card.
2. In browser DevTools Network, trigger a Memories or month load and select a successful `/remote/v1/events` request.
3. Have the user copy the request's complete `Cookie` header, `X-TADPOLES-UID`, and Referer `dependent_id` into their local `.env` based on `.env.example`.
4. Restrict the file with `chmod 600 .env` and verify that Git ignores it.

If browser tooling can inspect network requests, it may help identify the successful request, but it must not print or return secret values. Fall back to the manual DevTools flow when secure local extraction is unavailable.

## Operating guidance

Install core backup dependencies with `uv pip install -e .`. Use `uv pip install -e '.[dev]'` for development and `uv pip install -e '.[photos]'` for macOS Photos import. The `photos` extra pins the tested `osxphotos` integration; ExifTool and ffmpeg are external requirements for `photos-export`. The CLI requires a Unix-like platform with `fcntl`; `photos-import` requires macOS.

Prefer separate, restartable stages:

```bash
bh-photo-sync discover --since YYYY-MM-DD --until YYYY-MM-DD
bh-photo-sync download
bh-photo-sync fix-formats
bh-photo-sync verify --deep
```

Use a short one-day `discover` window with a disposable `--data-dir` to validate authentication without advancing the real archive watermark. For the full run, choose `--since` before the earliest expected enrollment date and `--until` after the latest desired day. Dates use `BH_TIMEZONE` unless overridden with `--timezone`.

For macOS Photos:

```bash
bh-photo-sync photos-export
```

Do not drag thousands of individual files from Finder into Photos. The pasteboard grants one sandbox extension per file and can exhaust the kernel extension table, producing `Cannot allocate memory` followed by `Operation not permitted`. Photos users should provide the exact name of an existing album. Albums reference assets in the main Library; they do not provide separate storage.

```bash
bh-photo-sync photos-import --album "EXACT EXISTING ALBUM NAME" --dry-run
bh-photo-sync photos-import --album "EXACT EXISTING ALBUM NAME"
```

The command rejects a missing or ambiguous album, serializes this CLI's Photos operations with advisory file locks, snapshots the selected Photos library and verifies it remains selected, skips likely duplicates, adds existing duplicate assets to the requested album, keeps state under the private data directory, and writes a per-file report. It cannot lock out the Photos App or unrelated third-party tools, and it never accesses the Photos database directly.

If even a one-file scripted import fails with sandbox `ENOMEM`, quitting Photos is insufficient because the system-level extension table is already exhausted. Restart macOS once, then retry the CLI workflow instead of returning to drag-and-drop.

If the user prefers the Photos GUI, import about 1,000 files per batch. After each completed batch, quit Photos completely, reopen it, and continue from the last completed position. Retry a failed batch after restarting Photos; restart macOS only if even a one-file import continues to return sandbox `ENOMEM`.

## Reporting

Report the requested range, event and attachment totals, pending/failed counts, deep verification result, and Photos export path/count when applicable. Never include authentication headers or raw event payloads.
