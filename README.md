# media-insights

Indexer and classifier for media libraries. Probe every file with **ffprobe** (and MediaInfo when available), associate files to movies / TV shows via **.plexmatch** or folder-name parsing, classify each title as **anime / tv / movie** using scored rules, watch the filesystem for changes, and emit **{old, new}** events to webhooks / exec hooks so you can hook your own automations on top.

Designed for an `*-arr`-style Docker deployment: one `/config` volume for state, one `/data` volume (or many) for the libraries you want indexed.

See [CHANGELOG.md](CHANGELOG.md) for release notes.

## Features

- **Manage libraries from the UI or API — no restart needed.** Add, rename, or remove libraries from `/libraries` or `POST/PUT/DELETE /api/libraries`; changes write straight back into `config.yaml` (comments preserved) and the filesystem watcher picks up the new path immediately.
- **Offline-first matching.** `.plexmatch` + folder-name parsing via [guessit] cover most real-world releases with no API keys. Pluggable `Provider` interface for TMDB/TVDB/AniList later.
- **Technical truth, normalized.** Every track (video / audio / subtitle, embedded or external sidecar) is its own row — so queries like *"files with no English subtitle"* or *"anything still x264"* are SQL, not file scanning. Episode numbers and titles are extracted per file.
- **Scored classification.** Anime / TV / movie labels come with confidence and a human-readable list of reasons. Manual overrides always win.
- **Unmatched queue.** Items without external IDs (and items guessit couldn't even name) land in `/unmatched` for one-click identification.
- **Watcher + deep scan.** `watchdog` for inotify (with `PollingObserver` fallback for NFS/SMB) plus a periodic deep scan. Files are fingerprinted (BLAKE2b over first/last 8 MiB + size by default) so re-scans skip unchanged work and detect Arr upgrades.
- **Transactional outbox.** Old and new snapshots are written into the same DB transaction as the file row. A background dispatcher delivers them to webhooks (HMAC-signed) and exec hooks with retries. No loss on crash.
- **REST API + Web UI + CLI.** Same data, three interfaces.

[guessit]: https://github.com/guessit-io/guessit

## Running with Docker (recommended)

Use the published image — no clone required. Create a folder for the config,
drop a `docker-compose.yml` next to it, and start:

```yaml
# docker-compose.yml
services:
  media-insights:
    image: ghcr.io/williamokano/media-insights:latest
    container_name: media-insights
    restart: unless-stopped
    ports:
      - "8765:8765"
    volumes:
      - ./config:/config            # config.yaml + database live here
      - /path/to/movies:/data/movies:ro
      - /path/to/tv:/data/tv:ro
      - /path/to/anime:/data/anime:ro
    environment:
      PUID: "1000"                  # match your host user (id -u / id -g)
      PGID: "1000"
      # MI_WATCHER__OBSERVER: polling   # uncomment on NFS/SMB mounts
```

```bash
mkdir config
curl -o config/config.yaml \
  https://raw.githubusercontent.com/williamokano/media-insights/main/config.example.yaml
$EDITOR config/config.yaml    # point `libraries:` at your /data mounts

docker compose up -d
```

Open <http://localhost:8765> for the Web UI (API docs at `/docs`). On first
start the service scans every configured library; after that the watcher and
the cron deep-scan keep it current.

Volumes:

- `/config` — `config.yaml`, `media_insights.db`, WAL/SHM files. Persist this.
- `/data` — your media library roots. Mount read-only. The container doesn't
  modify media files.

Set `PUID` / `PGID` (default `1000:1000`) to run the service as your host
user — the same convention as the arr stack. The entrypoint remaps the
internal user, chowns `/config`, and drops privileges before starting.

To build the image yourself instead, clone the repo and use the checked-in
`docker-compose.yml` (it has a `build: .` directive):

```bash
git clone https://github.com/williamokano/media-insights
cd media-insights
mkdir config && cp config.example.yaml config/config.yaml
docker compose up -d --build
```

## Running locally with uv

```bash
git clone https://github.com/williamokano/media-insights
cd media-insights

uv sync --extra dev           # creates .venv with runtime + dev deps

mkdir config && cp config.example.yaml config/config.yaml
$EDITOR config/config.yaml    # set config_dir/data_dir + libraries to local paths

uv run media-insights --config config/config.yaml scan    # one-shot index
uv run media-insights --config config/config.yaml serve   # API + Web UI
```

ffmpeg (specifically `ffprobe`) must be in your `$PATH` for the probe layer
to do anything. `pymediainfo` ships `libmediainfo` inside its manylinux
wheels, so no system package is needed for that.

## Configuration

`config.yaml` (or `MI_CONFIG` env / `MI_CONFIG_DIR` env / `MI_*` field overrides).
Full reference:

```yaml
config_dir: /config       # where the database lives
data_dir:   /data
log_level:  INFO

database:
  url: sqlite:///{config_dir}/media_insights.db   # or postgres://...

fingerprint:
  strategy: partial          # mtime | partial | full
  chunk_bytes: 8388608      # 8 MiB head/tail

watcher:
  enabled: true
  recursive: true
  observer: auto             # auto | inotify | polling
  debounce_seconds: 5

schedule:
  enabled: true
  cron: "0 */6 * * *"        # every 6 hours

libraries:
  - name: Movies
    path: /data/movies
    kind: movie              # movie | tv | anime | auto
  - name: TV
    path: /data/tv
    kind: tv
  - name: Anime
    path: /data/anime
    kind: anime
# This list is also editable at runtime -- see "Managing libraries" below.
# Edits made from the UI/API are written back here automatically.

webhooks:
  - name: default
    url: "http://hook:9000/event"
    secret: "change-me"      # HMAC-SHA256 sign the body
    timeout_seconds: 10
    max_attempts: 8

exec_hooks:
  - name: log-changes
    command: "/usr/local/bin/on-change.sh"
    timeout_seconds: 30

server:
  host: 0.0.0.0
  port: 8765

ffmpeg:
  ffprobe: ""                # PATH lookup if empty
  mediainfo_cli: ""          # optional; ffprobe-only if absent
```

Override anything from the environment with `MI_`-prefixed env vars, using
`__` for nesting: `MI_LOG_LEVEL=DEBUG`, `MI_DATABASE__URL=postgresql://...`,
`MI_WATCHER__OBSERVER=polling`, `MI_SERVER__PORT=9000`. List-valued fields
(`libraries`, `webhooks`, `exec_hooks`) can only be set in the YAML file.

## Managing libraries

Editing `libraries:` in `config.yaml` and restarting works, but you don't
have to — the `/libraries` page (and the equivalent REST endpoints) let you
add, rename, or remove libraries while the service keeps running:

- **Add**: validates the path exists inside the container (mount it first),
  writes the entry back into `config.yaml`, starts a background scan, and
  turns on the watcher for that path immediately.
- **Edit**: renaming or repointing a library updates both the database and
  `config.yaml`; if the path changed, the watcher is moved to the new path
  and a rescan is kicked off.
- **Remove** has two distinct actions, since undoing an accidental delete of
  years of indexed metadata isn't something a confirm dialog can fix:
  - *Remove* — stops scanning/watching the library but keeps everything
    already indexed; it stays browsable, just marked `configured: false`.
  - *Remove & delete data* (`?purge=true`) — also deletes the library's
    `MediaItem`/`MediaFile`/`Track` rows. This is permanent.

All three operations use a comment-preserving YAML writer, so hand-written
comments elsewhere in `config.yaml` survive edits made from the UI. If
`config.yaml` doesn't exist yet (or is empty), adding a library through the
UI/API creates it — you don't need to `cp config.example.yaml` first just to
use the library manager.

Libraries defined directly in `config.yaml` before the service starts don't
get a database row until their first scan completes (`GET /api/libraries`
reflects the database, not the config file) — this only affects the very
first scan after adding a library by hand; libraries added through the UI/API
appear immediately.

## CLI

```bash
media-insights scan                              # one-shot scan of every library
media-insights scan --library Movies             # only one library
media-insights scan --force                      # re-probe everything
media-insights serve                             # API + Web UI
media-insights search "Cowboy Bebop"
media-insights unmatched                         # list items needing IDs
media-insights resolve --id 42 --tvdb 71663      # attach external IDs
media-insights resolve --id 42 --classify anime  # manual classification
media-insights rescan /data/anime/foo.mkv        # single-path force rescan
media-insights config                            # dump resolved config as JSON
media-insights version
```

## REST API

| Method | Path | Purpose |
|---|---|---|
| GET    | `/healthz` | liveness |
| GET    | `/api/libraries` | list libraries (`configured: false` = removed from config but data kept) |
| POST   | `/api/libraries` | add a library — body `{"name", "path", "kind"}`; `path` must exist |
| PUT    | `/api/libraries/{id}` | rename / repoint a library |
| DELETE | `/api/libraries/{id}` | stop scanning/watching (data kept); add `?purge=true` to also delete its indexed data |
| GET    | `/api/items` | filter by library / classification / unmatched; paginate |
| GET    | `/api/items/{id}` | full item incl. files + tracks |
| POST   | `/api/items/{id}/identify` | attach `imdb_id` / `tmdb_id` / `tvdb_id` / `anidb_id` / `guid` / `classification` |
| POST   | `/api/items/{id}/classification` | override label (`anime`/`tv`/`movie`) |
| GET    | `/api/unmatched` | items with no external IDs |
| GET    | `/api/search?q=...` | title / path LIKE search |
| GET    | `/api/files/{id}` | file + tracks |
| POST   | `/api/scan` | trigger scan (`?library=Name` to scope) |
| POST   | `/api/rescan` | body `{"path": "..."}` — single-path rescan |

Web UI pages: `/dashboard`, `/libraries`, `/items/{id}`, `/unmatched`,
`/events`, `/search`. OpenAPI docs at `/docs`.

## Webhook payload

Every change is delivered as a JSON POST carrying `old` and `new` snapshots.
Event types:

| Type | old | new |
|---|---|---|
| `file.added` | `null` | full file snapshot (tracks, codecs, fingerprint) |
| `file.changed` | previous snapshot | fresh probe |
| `file.removed` | last-known snapshot | `null` |
| `item.identified` | previous IDs / match status | updated IDs / match status |

With no webhooks or exec hooks configured, events are still written to the
database as an audit log (`delivery_status: skipped`) — nothing is lost, and
nothing is reported as a failure.

```json
{
  "id": 17,
  "type": "file.changed",
  "subject_id": 142,
  "subject_path": "/data/anime/Frieren - 01.mkv",
  "created_at": "2026-07-12T18:42:11+00:00",
  "old": {
    "id": 142, "path": "...", "container": "matroska",
    "video_codec": "h264", "video_width": 1920, "video_height": 1080,
    "audio_summary": "jpn/truehd", "subtitle_summary": "en/ass",
    "tracks": [
      {"position": 0, "kind": "video", "codec": "h264", "language": null},
      {"position": 1, "kind": "audio", "codec": "truehd", "language": "jpn"},
      {"position": 2, "kind": "subtitle", "codec": "ass", "language": "en"}
    ],
    "fingerprint": "ab12...", "size": 8589934592
  },
  "new": { "...": "...same shape, fresh probe" }
}
```

Headers:

- `X-Media-Insights-Event` — the type, e.g. `file.changed`.
- `X-Media-Insights-Event-Id` — monotonically increasing ID.
- `X-Media-Insights-Signature` — `sha256=<hex>` if `webhooks[].secret` is set.

Signature verification (Python):

```python
import hmac, hashlib
expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
assert hmac.compare_digest(expected, request.headers["X-Media-Insights-Signature"])
```

Exec hooks receive the same JSON object on stdin.

## Classification

Every `MediaItem` carries a label, confidence (0–1), and a list of reasons.
The classifier is a scored-rules system:

- Library kind hint from config (strongest signal — e.g. `kind: anime` ⇒
  anime baseline).
- Matched via AniDB ID ⇒ anime.
- Japanese primary audio + non-Japanese subtitles ⇒ strong anime signal.
- Single-file structure ⇒ movie; multi-file ⇒ show.
- Fansub-style release groups (`[SubsPlease]`, `Erai-raws`, ...) ⇒ anime bonus.
- guessit's `anime` flag ⇒ anime bonus.

Per-item manual override (POST `/api/items/{id}/classification`) is sticky and
always wins.

## Subtitles

External `.srt` / `.ass` / `.ssa` / `.sub` / `.idx` / `.vtt` / `.sup` sidecars
are auto-discovered. Trailing dots are parsed:

```
Movie.2020.en.srt           -> language=en
Movie.2020.en.forced.srt    -> language=en, is_forced=true
Movie.2020.eng.sdh.ass      -> language=en, is_sdh=true
Movie.2020.pt-BR.srt        -> language=pt-BR (locale preserved)
```

ISO-639 alpha-3 (`eng`) is normalized to alpha-2 (`en`); region tags (`pt-BR`,
`zh-CN`) are preserved.

## Fingerprinting

Three strategies, picked in `fingerprint.strategy`:

- **`mtime`** — size + mtime digest. Cheapest, can miss same-size rewrites.
- **`partial`** *(default)* — BLAKE2b over first/last `chunk_bytes` + size.
  Near-instant on multi-GB remuxes; Arr upgrades always change the header or
  index so they're detected reliably.
- **`full`** — bit-exact. Slow on large libraries; mostly useful for archival.

A file is re-probed only if its fingerprint changes. Unchanged files update
`last_seen` and generate no events.

## Storage

SQLite by default (WAL, NORMAL sync, foreign keys on). Swap to Postgres by
changing `database.url`:

```yaml
database:
  url: postgresql+psycopg://user:pass@host/media_insights
```

The schema is migrated via Alembic: `alembic upgrade head`. Migrations live
in `alembic/versions/`. On startup the app calls `Base.metadata.create_all`
as a safety net for fresh installs.

## Architecture

```
src/media_insights/
  config.py            pydantic models; YAML + nested MI_* env overrides
  config_store.py      comment-preserving library CRUD writes to config.yaml
  db.py, models.py     SQLAlchemy 2.0 typed ORM, WAL pragmas
  probe/               ffprobe + pymediainfo + normalize
  discovery/           walker, fingerprint, plexmatch, subtitles, grouping
  matching/            parser (guessit), matcher, providers (Protocol)
  classify/            scored rules -> (label, confidence, reasons[])
  scanner/             service (orchestration + diffing), watcher, scheduler
  events/              bus (outbox), webhook (HMAC), exec, dispatcher
  api/                 FastAPI app + lifespan; REST + Web UI
  web/                 Jinja2 templates + static (vanilla JS/CSS)
  cli.py               Typer entry point
```

## Development

```bash
uv sync --extra dev
uv run pytest                    # unit + e2e tests
uv run ruff check .
uv run mypy src
```

Generate a migration after model changes:

```bash
uv run alembic revision --autogenerate -m "describe change"
```

## Release / Docker image

The `release.yml` workflow builds a multi-arch image (`linux/amd64` +
`linux/arm64`) on tag push (`v*`) and publishes to GHCR. Docker Hub is
guarded by `if: secrets.DOCKERHUB_USERNAME != ''`, so it no-ops until you
add those secrets in the repository settings.

```bash
git tag v0.0.1
git push origin v0.0.1
```

## License

MIT.
| GET    | `/api