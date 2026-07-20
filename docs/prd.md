# PRD: Bright Horizons Photo Sync

## Problem

My Bright Day lets a parent view and save family media, but it does not provide a complete, verifiable bulk backup workflow. Manual downloads are difficult to resume and cannot prove that a large date range avoided API truncation, duplicate files, or partial writes.

This project provides a local-first CLI and agent skill. It discovers all events visible to an authenticated family account, stores their original metadata, downloads each attachment once, verifies local integrity, and prepares media for macOS Photos.

The project is unofficial and is not affiliated with or endorsed by Bright Horizons.

## Users

The primary user is a parent or guardian who controls a Bright Horizons family account and wants a local archive of data that account can access. A coding agent may guide installation and operation, but the user completes login, password, and MFA interactions in a visible browser.

## Product Principles

- **Complete before convenient.** A run succeeds only after the requested date range has been fully covered.
- **Local by default.** Credentials, raw responses, databases, media, reports, and Photos state remain on the user's machine.
- **Visible means in scope.** The backup saves every event returned to the authenticated account. API member metadata does not define ownership or filter downloads.
- **Restartable stages.** Discovery, download, format repair, verification, Photos export, and Photos import can run independently.
- **Derived exports do not replace originals.** Downloaded objects remain unchanged; Photos-compatible media lives in a rebuildable export directory.

## Functional Requirements

### Authentication

- Read the My Bright Day session cookie, Tadpoles UID, and dependent ID from a local environment file.
- Never ask the user to paste credentials into an AI conversation, issue, commit, or command output.
- Treat `401`, `403`, HTML login responses, and malformed authentication responses as hard failures.
- Do not automate password or MFA entry and do not bypass service authorization.

### Discovery and Synchronization

- Accept explicit half-open date ranges `[since, until)` using a configurable IANA timezone.
- After the first successful run, support incremental synchronization with a configurable overlap window.
- Split API requests until the client can prove that no response-limit truncation remains.
- Save each successful event response to a local atomic cache before publishing the run as complete.
- Upsert events by stable event key and preserve their original payloads and API member metadata.
- Deduplicate attachments by attachment ID while preserving many-to-many event relationships.
- Never advance the success watermark after authentication, network, integrity, truncation, or storage failures.

### Media Storage and Verification

- Download through the attachment metadata endpoint and validate the returned HTTPS signed URL.
- Stream each attachment to a temporary file, reject empty or unsupported content, compute SHA-256, fsync, and atomically publish the object.
- Determine media type from file signatures rather than trusting the server MIME value.
- Resume `pending` and `failed` attachments without redownloading valid complete objects.
- Detect missing or size-mismatched complete objects and return them to the pending queue.
- Provide shallow and deep verification; deep verification recalculates every stored SHA-256.

### Query and Export

- Report event, attachment, run, watermark, and attachment-status totals without exposing credentials.
- Query attachments by storage status and MIME prefix.
- Rebuild JSON and CSV exports from SQLite.
- Expose SQLite views for attachment summaries and attachment-event context.

### Photos Export

- Build a flat, reproducible Photos import directory from complete images and videos plus any explicitly retained legacy media.
- Convert images to RGB JPEG and write standard EXIF capture timestamps in the configured timezone.
- Preserve video streams while writing creation time metadata.
- Exclude PDFs, empty files, unsupported files, and duplicate content.
- Run conversion in parallel while publishing each output atomically.
- Serialize Photos export and import operations so they cannot modify shared derived state concurrently.

### Photos Import

- Support macOS only and use the pinned `osxphotos` integration.
- Require the exact name of an existing Photos album; reject missing or ambiguous names.
- Resolve and open the selected Photos library, serialize this CLI's imports with advisory locks, and verify the selected library has not changed during the operation.
- Offer a dry run that reports candidate, imported, skipped, and error counts without mutating Photos.
- Skip likely duplicates and add already-existing duplicate assets to the requested album.
- Store isolated `osxphotos` state and per-file reports under the private data directory so interrupted work can be audited and safely rerun with duplicate detection.
- Never read or write the Photos SQLite database directly.

## Non-Goals

- Running a hosted service, multi-tenant backend, or shared credential store.
- Inferring which person owns an event or attachment.
- Filtering synchronization by child name, member ID, classroom, or local labels.
- Detecting or propagating server-side deletions.
- Uploading, publishing, editing, or deleting the user's source media.
- Automating login, password entry, MFA, or access-control bypass.
- Managing arbitrary Photos libraries, albums, or destructive Photos operations.

## Success Criteria

- Repeating the same completed range does not redownload valid media.
- A full-page API response is split until completeness can be established or the run fails explicitly.
- Interrupted downloads never appear as complete objects.
- `verify --deep` reports no missing files or hash mismatches for a healthy archive.
- Photos export is reproducible and records valid capture times without changing source objects.
- Photos import dry-run makes no Photos changes; a successful import writes a report with zero errors and adds every non-error item to the exact requested album.
- Default tests run offline and never mutate Bright Horizons or Photos.
- The public repository contains no credentials, family data, private paths, raw payloads, or household-specific operational statistics.
