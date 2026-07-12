# media-insights

Indexer and classifier for media libraries. Probe every file with **ffprobe** (and MediaInfo when available), associate files to movies / TV shows via **.plexmatch** or folder-name parsing, classify each title as **anime / tv / movie** using scored rules, watch the filesystem for changes, and emit **{old, new}** events to webhooks / exec hooks so you can hook your own automations on top.

Designed for an `*-arr`-style Docker deployment: one `/config` volume for state, one `/data` volume (or many) for the libraries you want indexed.

## Features

- **Offline-first matching.** `.plexmatch` + folder-name parsing via [guessit] cover most real-world releases with no API keys. Pluggable `Provider` interface for TMDB/TVDB/AniList later.
- **Technical truth, normalized.** Every track (video / audio / subtitle, embedded or external sidecar) is its own row — so queries like *"files with no English subtitle"* or *"anything still x264"* are SQL, not file scanning. Episode numbers and titles are extracted per file.
- **Scored classification.** Anime / TV / movie labels come with confidence and a human-readable list of reasons. Manual overrides always win.
- **Unmatched queue.** Items without external IDs (and items guessit couldn't even name) land in `/unmatched` for one-click identification.
- **Watcher + deep scan.** `watchdog` for inotify (with `PollingObserver` fallback for NFS/SMB) plus a periodic deep scan. Files are fingerprinted (BLAKE2b over first/last 8 MiB + size by default) so re-scans skip unchanged work and detect Arr upgrades.
- **Transactional outbox.** Old and new snapshots are written into the same DB transaction as the file row. A background dispatcher delivers them to webhooks (HMAC-signed) and exec hooks with retries. No loss on crash.
- **REST API + Web UI + CLI.** Same data, three interfaces.

[guessit]: https://github.com/guessit-io/guessit

## Quickstart — Docker (recommended)

```bash
git clone https://github.com/<you>/media-insights
cd media-insights

# 1. Create your config (mount this into /config)
mkdir config
cp config.example.yaml config/config.yaml
$EDITOR config/config.yaml   # edit libraries, webhooks, etc.

# 2. Run
docker compose up -d
```

The default `docker-compose.yml` mounts `config/` to `/config` and example
media paths to `/data/{movies,tv,anime}`. Adjust the paths to match your
library layout. Open <http://localhost:8765> for the Web UI.

Volumes:

- `/config` — `config.yaml`, `media_insights.db`, WAL/SHM files. Persist this.
- `/data` — your media library roots. Mount read-only. The container doesn't
  modify media files.

Set `PUID` / `PGID` (default `1000:1000`) to run the service as your host
user — the same convention as the arr stack. The entrypoint remaps the
internal user, chowns `/config`, and drops privileges before starting.

## Quickstart — uv (local dev)

```bash
git clone https://github.com/<you>/media-insights
cd media-insights

uv sync --extra dev           # creates .venv with runtime + dev deps
uv run media-insights --config config/config.yaml scan
uv run media-insights --config config/config.yaml serve
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
| GET    | `/api/libraries` | list libraries |
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
git tag v0.1.0
git push origin v0.1.0
```

## License

MIT.
| GET    | `/api