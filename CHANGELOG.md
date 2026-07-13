# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
(pre-1.0, so minor bumps may still include breaking changes).

## [Unreleased]

## [0.0.6] - 2026-07-13

### Fixed

- `subtitle_summary` (the compact field shown on the item detail page and in
  API responses) was computed once from ffprobe's embedded subtitle streams
  *before* external `.srt`/`.ass` sidecars were attached, and never
  recomputed. External subtitle tracks were correctly detected and stored
  (visible in the full per-file `tracks` list) but silently missing from
  this summary field — making it look like a file had no subtitles when it
  actually had an external one. Now recomputed once, after both embedded
  and external tracks are known.
- `GET /api/items` (the list view) dropped `audio_summary`/`subtitle_summary`
  and basic video info from every file entry, even though these are plain
  columns that cost nothing extra to include — you had to hit
  `/api/items/{id}` to see them. Now included in the list view too.
- The dashboard's "Titles" card linked directly to `/api/items` (raw JSON)
  because no browsable "all titles" HTML page existed. Added `/titles`: a
  proper paginated, filterable (library/classification/unmatched) listing,
  linked from the nav and the dashboard.

### Added

- The events list explains what **skipped** means (no webhook/exec hook
  configured — not a failure; the underlying file change was still recorded)
  directly in the UI, on both the dashboard's compact widget and the full
  `/events` page, instead of showing an unexplained badge.
- New title discovery and classification decisions are now logged at `INFO`
  (`new title: 'X' (year) kind=... matched_via=...` /
  `classified: 'X' as movie (confidence=...%, reasons=[...])`), only when
  they happen or change — not spammed on every re-scan of a stable library.
  Per-file match detail (what guessit/`.plexmatch` parsed out of a given
  filename) logs at `DEBUG`.
- A startup log line now states explicitly that matching is offline-only and
  no TVDB/IMDB/TMDB network calls are ever made, to head off exactly that
  question before it comes up.

## [0.0.5] - 2026-07-12

### Added

- Scans are no longer silent. Every scan now logs a start line and a finish
  summary at `INFO` (`scan started: library=... trigger=... path=... force=...`
  / `scan finished: ... seen=N added=N changed=N unchanged=N removed=N
  errors=N duration=Ns`), tagged with a `trigger` (`cli`, `api`, `scheduled`,
  `watcher`, `library-added`) so it's clear *why* a given scan ran. Added or
  changed files are logged individually at `INFO`; unchanged files log at
  `DEBUG` to avoid drowning out everything else on a re-scan.
- The event dispatcher now logs why events show as `skipped` in the `/events`
  page (no `webhooks`/`exec_hooks` configured), and logs successful
  deliveries too — previously it only ever logged failures.
- `trigger` is also included in the JSON scan summary returned by the CLI
  and `POST /api/scan`.

## [0.0.4] - 2026-07-12

### Fixed

- Fixed a `database is locked` bug that could occur during scans, especially
  on larger libraries or when a scheduled deep scan, a manual "Rescan"
  click, and a watcher-triggered rescan happened to overlap. `scan_library()`
  used to hold one long-lived transaction for an entire library scan;
  SQLite allows exactly one writer, so any other writer that showed up
  mid-scan (the event dispatcher, another scan) had nowhere to go. Each
  file now gets its own short transaction instead, and scans of the same
  library now queue up behind a per-library lock rather than racing each
  other. Also added a 30s `busy_timeout` pragma as a second line of
  defense, and fixed the error handling so a mid-scan failure can no longer
  cascade into `PendingRollbackError` on every statement for the rest of
  that scan.

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

[Unreleased]: https://github.com/williamokano/media-insights/compare/v0.0.6...HEAD
[0.0.6]: https://github.com/williamokano/media-insights/compare/v0.0.5...v0.0.6
[0.0.5]: https://github.com/williamokano/media-insights/compare/v0.0.4...v0.0.5
[0.0.4]: https://github.com/williamokano/media-insights/compare/v0.0.3...v0.0.4
[0.0.3]: https://github.com/williamokano/media-insights/compare/v0.0.2...v0.0.3
[0.0.2]: https://github.com/williamokano/media-insights/compare/v0.0.1...v0.0.2
[0.0.1]: https://github.com/williamokano/media-insights/releases/tag/v0.0.1