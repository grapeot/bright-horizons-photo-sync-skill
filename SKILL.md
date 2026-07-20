---
name: bright-horizons-photo-sync
description: Back up all My Bright Day events and media visible to an authorized Bright Horizons family account, resume interrupted downloads, verify local files, and import them into a macOS Photos album without duplicates.
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
- `photos-import --dry-run` reports the exact new/duplicate split before mutation.
- A completed `photos-import` leaves every exported candidate in the named album and a second run imports zero new assets.

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

Do not drag thousands of individual files from Finder into Photos. The pasteboard grants one sandbox extension per file and can exhaust the kernel extension table, producing `Cannot allocate memory` followed by `Operation not permitted`. Use the duplicate-safe CLI workflow and provide the exact name of an existing album. Albums reference assets in the main Library; they do not provide separate storage.

```bash
# Audit without changing Photos.
bh-photo-sync photos-import --album "EXACT EXISTING ALBUM NAME" --dry-run

# Import missing assets and add existing duplicates to the album.
bh-photo-sync photos-import --album "EXACT EXISTING ALBUM NAME"
```

The apply command reads a temporary copy of the Photos database to fingerprint every candidate, imports only missing assets in native batches, and bulk-adds existing duplicates to the album. It rejects a missing or ambiguous album, serializes this CLI's Photos operations with advisory file locks, verifies that the selected library does not change, and writes a per-file report. It is safe to rerun after interruption. Do not replace it with raw `osxphotos import`: osxphotos imports one asset per Apple event and can take hours. Do not add `--resume`; osxphotos may skip the duplicate-album repair required by this workflow.

If even a one-file scripted import fails with sandbox `ENOMEM`, quitting Photos is insufficient because the system-level extension table is already exhausted. Restart macOS once, then retry the CLI workflow instead of returning to drag-and-drop.

If the user prefers the Photos GUI, import about 1,000 files per batch. After each completed batch, quit Photos completely, reopen it, and continue from the last completed position. Retry a failed batch after restarting Photos; restart macOS only if even a one-file import continues to return sandbox `ENOMEM`.

## Reporting

Report the requested range, event and attachment totals, pending/failed counts, deep verification result, and Photos export path/count when applicable. Never include authentication headers or raw event payloads.

## Repository workflow

This project is maintained only through the public `grapeot/bright-horizons-photo-sync-skill` repository. Do not fetch from, push to, merge from, or open PRs against a private repository.

Keep the main checkout on public `master`. For code or documentation changes, create a separate worktree from `origin/master` on a temporary `feat/*` branch, verify there, push that branch to public `origin`, and merge a public PR into protected `master`. Delete the temporary remote branch after merge. If a local hook permits only `master -> master`, it conflicts with branch protection and must be updated to permit temporary public `feat/*` PR branches while continuing to block direct pushes to `master` and all private-named branches.
