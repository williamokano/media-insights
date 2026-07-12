# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
(pre-1.0, so minor bumps may still include breaking changes).

## [Unreleased]

## [0.0.3] - 2026-07-12

### Fixed

- Adding a library through the Web UI or API no longer requires
  `config.yaml` to already exist and be non-empty. A missing or empty file
  is now created automatically (parent directories included) instead of
  erroring — only the `libraries:` key is ever written, so nothing else in
  the file can be silently clobbered. Genuinely malformed YAML still
  raises a clear error.
- The background scan kicked off after adding a library now catches and
  logs its own exceptions instead of running in a bare, unguarded thread.

## [0.0.2] - 2026-07-12

### Added

- Live library management: add, rename, and remove libraries from the
  `/libraries` Web UI page or `POST` / `PUT` / `DELETE /api/libraries`,
  with no service restart required.
  - Changes are written back into `config.yaml` using a comment-preserving
    YAML round-trip, so hand-written comments elsewhere in the file
    survive edits made from the UI.
  - The filesystem watcher starts or stops watching a library's path
    immediately when it's added or removed.
  - A newly added library is scanned in the background and shows up in
    listings right away, before that first scan finishes.
  - Removing a library defaults to a soft delete (stops scanning/watching,
    keeps already-indexed data browsable); permanently deleting the data
    requires an explicit `?purge=true`.

### Fixed

- `api.app.State.config` had no default value, so `is None` checks
  guarding "not configured yet" paths would have raised `AttributeError`
  instead of running, had `configure()` ever been skipped before a
  request.

## [0.0.1] - 2026-07-12

### Added

- Initial release: scans configured library folders and indexes their
  media files.
- Matching via `.plexmatch` metadata or filename/folder-name parsing
  (guessit), with an unmatched/unresolved queue for manual identification.
- Per-file technical metadata extraction via `ffprobe`, enriched with
  `pymediainfo` when available — codecs, containers, resolution, audio and
  subtitle tracks (including external subtitle sidecars).
- Anime / TV / movie classification using scored heuristics (audio
  language, release-group conventions, library hints, etc.), with reasons
  recorded alongside the verdict.
- Filesystem watcher (with polling fallback for network shares) plus a
  scheduled deep re-scan, with BLAKE2b-based change detection so unchanged
  files are skipped.
- Change events (`file.added`, `file.changed`, `file.removed`,
  `item.identified`) delivered via signed webhooks and/or exec hooks
  through a durable outbox with retry.
- REST API, Web UI, and CLI, all backed by SQLite (or Postgres) via
  SQLAlchemy + Alembic migrations.
- Multi-arch Docker image (amd64/arm64) with `PUID`/`PGID` support,
  published to GHCR on tagged releases.

[Unreleased]: https://github.com/williamokano/media-insights/compare/v0.0.3...HEAD
[0.0.3]: https://github.com/williamokano/media-insights/compare/v0.0.2...v0.0.3
[0.0.2]: https://github.com/williamokano/media-insights/compare/v0.0.1...v0.0.2
[0.0.1]: https://github.com/williamokano/media-insights/releases/tag/v0.0.1