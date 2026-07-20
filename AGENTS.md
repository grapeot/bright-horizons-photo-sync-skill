# Bright Horizons Photo Sync

## Project Scope

This public repository contains the maintained implementation of the My Bright Day backup CLI and its agent skill. The tool saves every event and attachment visible to an authenticated family account; API `member` fields remain metadata and never act as ownership or download filters.

## Repository Layout

- `src/bright_horizons_photo_sync/`: reusable CLI implementation.
- `tests/`: offline tests plus explicitly enabled live tests.
- `docs/prd.md`: product requirements and success criteria.
- `docs/rfc.md`: architecture, data contracts, and failure semantics.
- `docs/test.md`: validation strategy and release gates.
- `docs/working.md`: public changelog and lessons learned.
- `SKILL.md`: agent-facing operating contract.
- `data/`: local private data, entirely ignored by Git.

## Development Rules

- Use the project virtual environment: `source .venv/bin/activate`.
- Install development dependencies with `uv pip install -e '.[dev]'`. On macOS, add the Photos integration with `uv pip install -e '.[dev,photos]'`. Do not use bare `pip install`.
- Never commit `.env`, cookies, account identifiers, API responses, SQLite databases, media, Photos reports, absolute home paths, or real family metadata.
- Default tests must not access the network or mutate Photos. Bright Horizons and Photos live tests require separate opt-in environment variables.
- Preserve the complete-range contract: authentication, truncation, network, integrity, or storage failures must return nonzero and must not advance the success watermark.
- Keep all API-visible events. Do not add child-specific configuration, ownership inference, or filtering.
- Treat attachment ID as the canonical media identity. `photos-export` may create derived files but must never rewrite source objects.
- `photos-import` must target an exact existing album, use the selected Photos library, and never modify the Photos SQLite database directly.
- Update PRD, RFC, test strategy, README, SKILL, and working log when their contracts change.
- Commit, push, open a PR, merge, or otherwise mutate a remote only when the user explicitly authorizes that action.

## Validation

```bash
source .venv/bin/activate
ruff check .
TEST_HOME="$(mktemp -d)"
HOME="$TEST_HOME" python -m pytest -q
rm -rf "$TEST_HOME"
```

Live tests remain opt-in:

```bash
BH_ENABLE_LIVE_TESTS=1 python -m pytest -q -m live
BH_ENABLE_PHOTOS_LIVE_TESTS=1 python -m pytest -q -m photos_live
```

Before publishing, scan all tracked files for credentials, personal names, account IDs, absolute paths, private API payloads, and operational counts.
