# RFC: Sync, Storage, and Photos Architecture

## System Overview

```text
Family browser session
        |
        v
MyBrightDayClient ---> atomic API cache
        |
        v
complete-range collector ---> SQLite Store ---> JSON/CSV exports
        |                         |
        v                         v
attachment downloader       canonical objects
                                  |
                                  v
                           Photos-compatible export
                                  |
                                  v
                         exact-album Photos import
```

SQLite is the structured source of truth. Canonical media objects and raw API caches live under the same private data root. JSON, CSV, Photos-compatible files, and Photos import reports are derived artifacts that can be regenerated or audited independently.

## Configuration and Data Boundary

The CLI reads three authentication values from a local `.env`: the complete browser Cookie header, `X-TADPOLES-UID`, and the Referer `dependent_id`. `BH_TIMEZONE` controls date boundaries and EXIF offsets. `BH_DATA_DIR` or `--data-dir` selects the private data root.

The dependent ID establishes the browser session and Referer. It does not define the API's data boundary. The service may return events associated with multiple members or no member. The client stores every visible event and preserves API member metadata without classification, ownership inference, or filtering.

## Private Data Layout

```text
data/
├── state.sqlite3
├── backups/
├── objects/<prefix>/<attachment-id>.<extension>
├── cache/api/events/
├── state/
│   ├── photos_operation.lock
│   └── osxphotos/
├── reports/photos_import/
├── exports/
│   ├── photos_import_all/
│   ├── merged_events.json
│   ├── events_summary.csv
│   └── event_attachments.csv
└── legacy/                         # optional pre-existing archive
```

The entire data root and `.env` are ignored by Git. API caches and exports may contain names, comments, account-visible metadata, and media; they receive the same privacy treatment as the SQLite database.

## Date Ranges and Completeness

User-facing dates form a half-open range `[since, until)` in an IANA timezone. The legacy API accepts an inclusive upper epoch, so requests send `until_epoch - 1`.

The collector starts with bounded windows. A response at or above the requested limit cannot prove completeness, so the collector recursively bisects the epoch range. Retryable server failures also trigger range bisection after HTTP retries are exhausted. Authentication, malformed data, local storage failures, and a saturated one-second range stop the run immediately.

Events from overlapping windows are deduplicated by stable key. Two different payloads for the same key in one run are an integrity failure. The success watermark advances only after every leaf range succeeds and, unless events-only mode was requested, the media stage completes.

## Atomic Event Cache

Every successful event request is stored under `cache/api/events/` with its request range, limit, fetch time, and complete event array. The client writes a unique temporary file, flushes and fsyncs it, then publishes with `os.replace`. A cache failure fails the run rather than silently discarding audit evidence.

## SQLite Schema

Schema version 3 contains:

- `events`: stable key, API member metadata, event time, original payload, payload hash, and observation timestamps.
- `attachments`: stable attachment ID, source metadata, local object path, size, SHA-256, lifecycle status, legacy coverage, and last error.
- `event_attachments`: many-to-many event and attachment relationships.
- `sync_state`: durable watermarks.
- `sync_runs`: run ranges, status, counts, timestamps, and failure details.

`v_attachments` summarizes one attachment per row. `v_attachment_context` exposes one attachment-event relationship per row, including API member metadata. Neither view infers ownership.

Opening a version 1 or version 2 database first checks foreign keys, writes a SQLite backup, and migrates atomically to version 3. Version 3 removes the former local target-child classification while preserving original event payloads and API member IDs.

## Attachment Lifecycle

Attachment status is one of `pending`, `failed`, `complete`, or `legacy`.

1. Discovery inserts a new attachment as `pending`, unless an optional legacy attachment map proves it is already covered.
2. The client requests attachment metadata, validates its HTTPS signed URL, and streams the media to a unique `.part` file.
3. The downloader rejects empty or unsupported content, identifies the real format from magic bytes, calculates SHA-256, flushes and fsyncs, then atomically renames the file.
4. SQLite changes to `complete` only after publication succeeds.
5. Missing or size-mismatched complete objects return to `pending`. Deep verification also compares SHA-256.

Signed URLs never enter SQLite or error output. Network errors preserve enough context to diagnose the stage without printing the signed URL.

## Format Repair

The service may label one format as another. New downloads use magic bytes before selecting the extension. `fix-formats` applies the same inspection to complete canonical objects. It atomically renames a mismatched file and updates SQLite; a database failure rolls the file name back. The command does not transcode or rewrite media bytes.

## Photos Export

`photos-export` reads complete image and video objects plus optional legacy media. It deduplicates candidates by source SHA-256 and writes a flat `exports/photos_import_all/` directory.

Images are decoded and saved as high-quality RGB JPEG. ExifTool writes `DateTimeOriginal`, creation and modification timestamps, timezone offsets, dimensions, software version, and the source hash marker. Videos pass through ffmpeg with stream copy while receiving a UTC creation timestamp. PDFs and unsupported or empty legacy files are excluded.

Workers write unique temporary files and publish with `os.replace`. Existing outputs are reused only when their source hash and timestamp metadata match the current export contract. Stale managed outputs are removed. A project-level file lock prevents `photos-export` and `photos-import` from running concurrently.

## Photos Import

`photos-import` runs only on macOS and requires the pinned `osxphotos` version from the `photos` dependency extra. It reads a temporary, read-only copy of Photos SQLite for fingerprint matching and never writes the database directly.

Before import, the command:

1. Rejects symlinks, directories, and unsupported entries in the export directory.
2. Resolves the last-used `.photoslibrary` and opens that exact library.
3. Takes a machine-level import lock and confirms the selected library does not change.
4. Requires one exact, unambiguous existing album name.
5. Starts osxphotos subprocesses with an allowlisted environment so Bright Horizons credentials are not inherited.

Dry-run mode parses the `osxphotos` summary and returns counts without requesting mutation. Import mode fingerprints all candidates, bulk-adds existing matching asset IDs to the album, and sends missing files to Photos in batches of 100. Mutating Apple events disable Photoscript's outer timeout retry; after an ambiguous response or interruption, the user reruns the command and a fresh fingerprint scan determines the remaining work. The command stores a timestamped JSON report and fails unless every candidate resolves to an asset ID in the requested album.

Large Finder drag-and-drop operations can exhaust macOS sandbox extensions. The CLI avoids that pasteboard path. If even a single scripted import returns sandbox `ENOMEM`, the system-level extension table may already be exhausted and macOS must be restarted before retrying.

## Concurrency

Two advisory locks protect Photos operations:

- `data/state/photos_operation.lock` serializes export and import for one data root.
- `~/.cache/bright-horizons-photo-sync/photos_import.lock` prevents concurrent imports across data roots on the same account.

Both locks are non-blocking. A competing operation receives a storage error instead of waiting indefinitely.

## Failure Semantics

- Exit 2: invalid configuration or CLI range.
- Exit 3: expired or invalid authentication.
- Exit 4: network or general synchronization failure.
- Exit 5: data integrity or completeness failure.
- Exit 6: SQLite or filesystem failure.
- Exit 7: Photos import precondition, subprocess, report, or library-consistency failure.

Failures do not advance the synchronization watermark. Attachment failures remain retryable. Photos import writes a report before it can be declared successful.

## Security and Privacy

- Credentials stay in `.env` and are never written into caches, SQLite, reports, or logs.
- Signed URLs are short-lived secrets and are removed from propagated errors.
- Raw API payloads, names, comments, member metadata, media, hashes, and Photos reports remain under the ignored private data root.
- The Photos subprocess receives only `HOME`, locale, `PATH`, temporary-directory variables, and the isolated state location.
- The project never automates passwords or MFA, bypasses authorization, uploads family media, or mutates the Photos database directly.
- Public tests use synthetic identifiers and payloads. Live tests require explicit opt-in.
