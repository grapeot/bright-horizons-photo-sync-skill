# Working Log

## Changelog

### 2026-07-20

- Published the local-first event and media backup CLI with a clean public history.
- Removed local child classification and filtering; API member fields remain metadata only.
- Added SQLite v3 migration, complete-range discovery, restartable downloads, format repair, deep verification, and deterministic exports.
- Added Photos-compatible JPEG and video export with timestamps, deduplication, parallel processing, and operation locking.
- Added macOS `photos-import` with exact existing-album validation, selected-library checks, dry-run counts, duplicate handling, isolated state, and per-file reports.
- Restored the public project scaffold: `AGENTS.md`, PRD, RFC, test strategy, and this working log.
- Replaced osxphotos' one-file-at-a-time apply path with duplicate-safe native Photos batches while retaining exact album membership verification and restartability.
- Validated the scaffold with Ruff and an isolated test `HOME`: 58 passed and 2 opt-in tests skipped. The isolation kept mocked Photos tests from contending with a real import lock.
- Scanned public files for credentials, personal names, account IDs, private paths, obsolete child-filter settings, and household-specific counts; no matches remained.

## Lessons Learned

- A successful HTTP response does not prove a wide event range is complete. Responses at the API limit must be split until every leaf range is below the limit.
- The service MIME value is only a hint. File signatures determine the canonical extension.
- Photos accepts decodable files and timestamp metadata under different rules. Exported JPEGs need standard ExifIFD fields and validation, not just a timestamp written somewhere in the file.
- Dragging thousands of Finder URLs into Photos can exhaust sandbox extensions. Use the resumable import command; restart macOS if a one-file import still returns sandbox `ENOMEM`.
- Photoscript retries timed-out Apple events by default. Disable that outer retry for mutations; recover by rerunning the fingerprint scan instead of replaying an ambiguous batch.
- A dependent ID establishes the browser session but does not bound returned events. Save all API-visible events and never infer ownership from member metadata.
- Public documentation must describe reproducible contracts, not household counts, names, dates, local paths, or private operational history.
