"""Scanner service: walk libraries, match, probe, persist, diff, emit."""

from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from media_insights.classify import Classification, classify
from media_insights.config import AppConfig, LibraryConfig
from media_insights.db import init_engine, run_migrations, session_scope
from media_insights.discovery import (
    FileObservation,
    FoundFile,
    collect_subtitle_sidecars,
    fingerprint,
    iter_video_files,
)
from media_insights.discovery.subtitles import parse_sidecar
from media_insights.discovery.walker import find_nearest_plexmatch
from media_insights.events import bus
from media_insights.matching.matcher import MatchResult, match_observation
from media_insights.matching.parser import parse as parse_title
from media_insights.models import (
    Library,
    MediaFile,
    MediaItem,
    Season,
    Track,
)
from media_insights.probe import ProbeResult
from media_insights.probe import probe as probe_file

log = logging.getLogger(__name__)

# One lock per library name, so a scheduled deep scan, a manual "Rescan"
# click, and a watcher-triggered rescan of the *same* library queue up
# instead of running concurrently. Different libraries still scan in
# parallel; the dict only ever grows to the number of configured libraries.
_scan_locks: dict[str, threading.Lock] = {}
_scan_locks_guard = threading.Lock()


def _lock_for(library_name: str) -> threading.Lock:
    with _scan_locks_guard:
        return _scan_locks.setdefault(library_name, threading.Lock())


class LibraryGoneError(RuntimeError):
    """The library row was deleted (e.g. via ?purge=true) mid-scan."""


def _require_library(session: Session, library_id: int) -> Library:
    library = session.get(Library, library_id)
    if library is None:
        raise LibraryGoneError(f"library {library_id} was deleted mid-scan")
    return library


def get_or_create_library(session: Session, lib: LibraryConfig) -> Library:
    """Find the DB row for a configured library, creating/updating it as needed.

    Public so the API's library-management endpoints can eagerly create a
    row on POST (making a freshly-added library visible immediately, before
    its first scan finishes) instead of waiting for scan_library() to do it.
    """
    row = session.query(Library).filter(Library.name == lib.name).one_or_none()
    if row is None:
        row = Library(name=lib.name, path=lib.path, kind=lib.kind)
        session.add(row)
        session.flush()
    elif row.path != lib.path or row.kind != lib.kind:
        row.path = lib.path
        row.kind = lib.kind
    return row


def _item_record(
    session: Session, library: Library, match: MatchResult
) -> tuple[MediaItem, bool]:
    """Find or create the MediaItem for a match. Returns (item, created)."""
    rows = (
        session.query(MediaItem)
        .filter(MediaItem.library_id == library.id, MediaItem.title == match.title)
        .all()
    )
    item = next((r for r in rows if r.year == match.year), None) or rows[0] if rows else None
    created = item is None
    if item is None:
        item = MediaItem(
            library_id=library.id,
            kind=match.kind,
            title=match.title,
            year=match.year,
            match_status=match.match_status,
            imdb_id=match.imdb_id,
            tmdb_id=match.tmdb_id,
            tvdb_id=match.tvdb_id,
            anidb_id=match.anidb_id,
        )
        session.add(item)
        session.flush()
    else:
        # Persist any newly-supplied IDs even on already-known items.
        item.kind = match.kind or item.kind
        if match.year and not item.year:
            item.year = match.year
        if match.imdb_id and not item.imdb_id:
            item.imdb_id = match.imdb_id
        if match.tmdb_id and not item.tmdb_id:
            item.tmdb_id = match.tmdb_id
        if match.tvdb_id and not item.tvdb_id:
            item.tvdb_id = match.tvdb_id
        if match.anidb_id and not item.anidb_id:
            item.anidb_id = match.anidb_id
        if match.match_status == "matched" and item.match_status in ("unmatched", "unresolved"):
            item.match_status = "matched"
    return item, created


def _season_record(session: Session, item: MediaItem, number: int | None) -> Season:
    row = (
        session.query(Season)
        .filter(Season.item_id == item.id, Season.number == number)
        .one_or_none()
    )
    if row is None:
        row = Season(item_id=item.id, number=number)
        session.add(row)
        session.flush()
    return row


def _file_snapshot(file: MediaFile) -> dict:
    return {
        "id": file.id,
        "path": file.path,
        "size": file.size,
        "mtime": file.mtime,
        "container": file.container,
        "duration": file.duration,
        "bit_rate": file.bit_rate,
        "video_codec": file.video_codec,
        "video_width": file.video_width,
        "video_height": file.video_height,
        "video_dynamic_range": file.video_dynamic_range,
        "audio_summary": file.audio_summary,
        "subtitle_summary": file.subtitle_summary,
        "episode_numbers": list(file.episode_numbers or []),
        "episode_title": file.episode_title,
        "fingerprint": file.fingerprint,
        "fingerprint_strategy": file.fingerprint_strategy,
        "tracks": [
            {
                "position": t.position,
                "kind": t.kind,
                "codec": t.codec,
                "language": t.language,
                "language_raw": t.language_raw,
                "title": t.title,
                "channels": t.channels,
                "bit_rate": t.bit_rate,
                "is_default": t.is_default,
                "is_forced": t.is_forced,
                "is_sdh": t.is_sdh,
                "is_external": t.is_external,
                "sidecar_path": t.sidecar_path,
            }
            for t in file.tracks
        ],
    }


def _apply_probe(file: MediaFile, probe: ProbeResult, ffprobe_bin: str, fingerprint_strategy: str, fingerprint_chunk: int) -> None:
    """Mutate the file row with probe results, then re-fingerprint."""
    file.container = probe.container or file.container
    file.duration = probe.duration or file.duration
    file.bit_rate = probe.bit_rate or file.bit_rate
    video = probe.primary_video()
    if video is not None:
        file.video_codec = video.codec
        file.video_width = video.width
        file.video_height = video.height
        file.video_dynamic_range = video.dynamic_range

    file.audio_summary = ", ".join(
        f"{t.language or t.language_raw or 'und'}/{t.codec or '?'}" for t in probe.audio_tracks
    ) or None
    # subtitle_summary is computed later, in _refresh_subtitle_summary(), once
    # external sidecars have been added too -- embedded-only here would drop
    # every external .srt from the compact field shown in the UI/list view,
    # even though the Track rows themselves are stored correctly.

    # Drop existing tracks; we'll re-add them
    file.tracks.clear()
    for track in probe.tracks:
        file.tracks.append(
            Track(
                position=track.position,
                kind=track.kind,
                codec=track.codec,
                language=track.language,
                language_raw=track.language_raw,
                title=track.title,
                channels=track.channels,
                bit_rate=track.bit_rate,
                is_default=track.is_default,
                is_forced=track.is_forced,
                is_sdh=track.is_sdh,
            )
        )

    digest, mtime = fingerprint(Path(file.path), fingerprint_strategy, fingerprint_chunk)
    file.fingerprint = digest
    file.fingerprint_strategy = fingerprint_strategy
    file.mtime = mtime
    st = Path(file.path).stat()
    file.size = st.st_size


def _add_sidecars(file: MediaFile, sidecars: Iterable) -> None:
    """Append subtitle sidecar rows to file.tracks."""
    next_pos = (max((t.position for t in file.tracks), default=-1)) + 1
    from media_insights.discovery.subtitles import SidecarInfo  # local import to avoid cycle

    for sc in sidecars:
        if not isinstance(sc, SidecarInfo):
            sc = parse_sidecar(Path(file.path).stem, sc)  # type: ignore[arg-type]
        file.tracks.append(
            Track(
                position=next_pos,
                kind="subtitle",
                codec=_sidecar_codec(sc.path),
                language=sc.language,
                language_raw=sc.language_raw,
                title=sc.path.name,
                is_default=sc.is_default,
                is_forced=sc.is_forced,
                is_sdh=sc.is_sdh,
                is_external=True,
                sidecar_path=str(sc.path),
            )
        )
        next_pos += 1


def _sidecar_codec(path: Path) -> str | None:
    suffix = path.suffix.lower().lstrip(".")
    return suffix or None


def _refresh_subtitle_summary(file: MediaFile) -> None:
    """Recompute subtitle_summary from every subtitle track (embedded + external).

    Must run after both _apply_probe() and _add_sidecars(), otherwise external
    .srt/.ass sidecars are silently missing from the compact summary field
    even though their Track rows are stored correctly.
    """
    subs = [t for t in file.tracks if t.kind == "subtitle"]
    file.subtitle_summary = ", ".join(
        f"{t.language or t.language_raw or 'und'}/{t.codec or '?'}" for t in subs
    ) or None


def _ensure_db(cfg: AppConfig) -> None:
    if not cfg.database.url:
        cfg.database.url = f"sqlite:///{cfg.config_dir}/media_insights.db"
    init_engine(cfg.database.url)
    run_migrations(cfg.database.url)


def scan_library(
    cfg: AppConfig, lib: LibraryConfig, *, force: bool = False, trigger: str = "manual"
) -> dict[str, Any]:
    """Scan a single library; return a summary dict.

    Each file gets its own short transaction instead of one transaction for
    the whole library. ffprobe on a single file is milliseconds to a couple
    seconds; probing a whole library can take minutes. SQLite allows exactly
    one writer, so holding one open transaction for the entire scan would
    block every other writer (the event dispatcher, a concurrent rescan, the
    watcher) for that whole duration. Splitting per-file also means a
    mid-scan error can't poison the rest of the run: a failed transaction
    only takes down that one file's session, which gets rolled back and
    closed on the way out, and the next file starts a fresh one.

    `trigger` is purely for observability -- it's recorded in the summary
    and logged, so "why did a scan just run" is answerable from the logs
    (cli, api, scheduled, watcher, library-added) instead of a guess.
    """
    _ensure_db(cfg)
    started = dt.datetime.now(dt.UTC)
    perf_start = time.monotonic()
    log.info("scan started: library=%s trigger=%s path=%s force=%s", lib.name, trigger, lib.path, force)
    summary: dict[str, Any] = {
        "library": lib.name,
        "trigger": trigger,
        "files_seen": 0,
        "items_added": 0,
        "files_added": 0,
        "files_changed": 0,
        "files_unchanged": 0,
        "files_removed": 0,
        "errors": 0,
        "started_at": started.isoformat(),
    }

    def _finish() -> dict[str, Any]:
        summary["finished_at"] = dt.datetime.now(dt.UTC).isoformat()
        log.info(
            "scan finished: library=%s trigger=%s seen=%d added=%d changed=%d "
            "unchanged=%d removed=%d errors=%d duration=%.1fs",
            lib.name, trigger, summary["files_seen"], summary["files_added"],
            summary["files_changed"], summary["files_unchanged"], summary["files_removed"],
            summary["errors"], time.monotonic() - perf_start,
        )
        return summary

    with _lock_for(lib.name):
        with session_scope() as session:
            library_id = get_or_create_library(session, lib).id

        for found in iter_video_files(lib.path, recursive=True):
            summary["files_seen"] += 1
            try:
                with session_scope() as session:
                    library = _require_library(session, library_id)
                    outcome = _process_file(
                        session, cfg, library, found, force=force, summary=summary
                    )
            except LibraryGoneError:
                log.info("library %s deleted mid-scan; stopping", lib.name)
                return _finish()
            except Exception as exc:
                log.exception("scan failed for %s: %s", found.path, exc)
                summary["errors"] += 1
                continue
            summary[outcome] = summary.get(outcome, 0) + 1

        try:
            with session_scope() as session:
                library = _require_library(session, library_id)
                summary["files_removed"] = _prune_missing(session, library)
                _reclassify_library(session, library)
        except LibraryGoneError:
            log.info("library %s deleted before prune/reclassify; skipping", lib.name)
    return _finish()


def _process_file(
    session: Session,
    cfg: AppConfig,
    library: Library,
    found: FoundFile,
    *,
    force: bool,
    summary: dict[str, Any] | None = None,
) -> str:
    obs = FileObservation(found=found)
    match = match_observation(obs, _as_libcfg(library))
    log.debug(
        "matched: %s -> title=%r kind=%s season=%s episodes=%s status=%s via=%s "
        "ids={imdb=%s tmdb=%s tvdb=%s anidb=%s}",
        found.path.name, match.title, match.kind, match.season, match.episode_numbers,
        match.match_status, match.identified_via or "none (unmatched)",
        match.imdb_id, match.tmdb_id, match.tvdb_id, match.anidb_id,
    )
    item, item_created = _item_record(session, library, match)
    if item_created:
        if summary is not None:
            summary["items_added"] += 1
        log.info(
            "new title: %r (%s) kind=%s matched_via=%s ids={imdb=%s tmdb=%s tvdb=%s anidb=%s}",
            match.title, match.year or "?", match.kind, match.identified_via or "none (unmatched)",
            match.imdb_id, match.tmdb_id, match.tvdb_id, match.anidb_id,
        )
    season_number = match.season if match.kind == "show" else None
    season = _season_record(session, item, season_number)

    existing = (
        session.query(MediaFile)
        .filter(MediaFile.season_id == season.id, MediaFile.path == str(found.path))
        .one_or_none()
    )

    if existing is not None and not force:
        digest, mtime = fingerprint(
            found.path,
            cfg.fingerprint.strategy,
            cfg.fingerprint.chunk_bytes,
        )
        if existing.fingerprint == digest and existing.mtime == mtime:
            existing.last_seen = dt.datetime.now(dt.UTC)
            log.debug("file unchanged: %s", found.path)
            return "files_unchanged"

    # Probe and persist
    file_row = existing or MediaFile(season_id=season.id, path=str(found.path))
    if existing is None:
        session.add(file_row)
        session.flush()
    file_row.episode_numbers = match.episode_numbers or []
    file_row.episode_title = match.episode_title
    file_row.last_seen = dt.datetime.now(dt.UTC)

    old_snapshot = _file_snapshot(file_row) if existing is not None else None

    log.debug("probing: %s", found.path)
    probe_result = probe_file(found.path, ffprobe_bin=cfg.ffmpeg.ffprobe)
    _apply_probe(
        file_row,
        probe_result,
        ffprobe_bin=cfg.ffmpeg.ffprobe,
        fingerprint_strategy=cfg.fingerprint.strategy,
        fingerprint_chunk=cfg.fingerprint.chunk_bytes,
    )
    sidecars = collect_subtitle_sidecars(found.path)
    _add_sidecars(file_row, sidecars)
    _refresh_subtitle_summary(file_row)

    new_snapshot = _file_snapshot(file_row)
    if old_snapshot is not None and _has_meaningful_change(old_snapshot, new_snapshot):
        log.info(
            "file changed: %s (codec %s -> %s, audio %s -> %s)",
            file_row.path,
            old_snapshot.get("video_codec"), new_snapshot.get("video_codec"),
            old_snapshot.get("audio_summary"), new_snapshot.get("audio_summary"),
        )
        bus.record_event(
            session,
            type_="file.changed",
            subject_id=file_row.id,
            subject_path=file_row.path,
            old=old_snapshot,
            new=new_snapshot,
        )
        return "files_changed"
    if existing is None:
        log.info("file added: %s", file_row.path)
        bus.record_event(
            session,
            type_="file.added",
            subject_id=file_row.id,
            subject_path=file_row.path,
            old=None,
            new=new_snapshot,
        )
        return "files_added"
    log.debug("file re-probed, no meaningful change: %s", found.path)
    return "files_unchanged"


def _has_meaningful_change(old: dict, new: dict) -> bool:
    """Same fingerprint shouldn't generate an event even if probe runs anew."""
    if old.get("fingerprint") and new.get("fingerprint"):
        if old["fingerprint"] == new["fingerprint"]:
            return False
    keys = ("size", "container", "duration", "video_codec", "video_width", "video_height",
            "video_dynamic_range", "audio_summary", "subtitle_summary")
    for k in keys:
        if old.get(k) != new.get(k):
            return True
    if len(old.get("tracks", [])) != len(new.get("tracks", [])):
        return True
    return False


def _as_libcfg(library: Library) -> LibraryConfig:
    return LibraryConfig(name=library.name, path=library.path, kind=library.kind)  # type: ignore[arg-type]


def _remove_file(session: Session, file: MediaFile) -> None:
    """Emit file.removed with the last-known snapshot, then drop the row.

    The file is detached from the season's collection (delete-orphan cascade
    performs the actual DELETE) so the emptiness checks below see reality.
    """
    bus.record_event(
        session,
        type_="file.removed",
        subject_id=file.id,
        subject_path=file.path,
        old=_file_snapshot(file),
        new=None,
    )
    file.season.files.remove(file)


def _cleanup_empty(session: Session, item: MediaItem) -> None:
    for season in list(item.seasons):
        if not season.files:
            item.seasons.remove(season)
    if not item.seasons:
        session.delete(item)


def _prune_missing(session: Session, library: Library) -> int:
    """Drop rows for files that no longer exist; clean up empty seasons/items."""
    removed = 0
    for item in list(library.items):
        for season in list(item.seasons):
            for file in list(season.files):
                if not Path(file.path).exists():
                    _remove_file(session, file)
                    removed += 1
        _cleanup_empty(session, item)
    if removed:
        session.flush()
        log.info("pruned %d missing file(s) from %s", removed, library.name)
    return removed


def handle_missing_path(cfg: AppConfig, path: str) -> bool:
    """Watcher hook for a deleted file. Returns True if a row was removed."""
    _ensure_db(cfg)
    with session_scope() as session:
        file = session.query(MediaFile).filter(MediaFile.path == path).one_or_none()
        if file is None:
            return False
        item = file.season.item
        _remove_file(session, file)
        _cleanup_empty(session, item)
    return True


def _reclassify_library(session: Session, library: Library) -> None:
    for item in library.items:
        files = [f for season in item.seasons for f in season.files]
        tracks = [t for f in files for t in f.tracks]
        # Release-name signals come from one representative file.
        raw_name = Path(files[0].path).name if files else None
        parsed = parse_title(raw_name) if raw_name else None
        result: Classification = classify(
            MatchResult(
                title=item.title,
                year=item.year,
                kind=item.kind,
                season=None,
                episode_numbers=[],
                match_status=item.match_status,
                imdb_id=item.imdb_id,
                tmdb_id=item.tmdb_id,
                tvdb_id=item.tvdb_id,
                anidb_id=item.anidb_id,
                identified_via=None,
                library_kind_hint=library.kind,  # type: ignore[arg-type]
            ),
            files=files,
            tracks=tracks,
            parsed=parsed,
            raw_name=raw_name,
            manual_override=item.classification_override,
        )
        if not item.classification_override:
            if result.label != item.classification_label:
                log.info(
                    "classified: %r as %s (confidence=%.0f%%, reasons=%s)",
                    item.title, result.label, result.confidence * 100, result.reasons,
                )
            item.classification_label = result.label
            item.classification_confidence = result.confidence
            item.classification_reasons = result.reasons


def scan_all(cfg: AppConfig, *, force: bool = False, trigger: str = "manual") -> list[dict]:
    _ensure_db(cfg)
    log.info("deep scan started: trigger=%s libraries=%d", trigger, len(cfg.libraries))
    results = [scan_library(cfg, lib, force=force, trigger=trigger) for lib in cfg.libraries]
    log.info("deep scan finished: trigger=%s libraries=%d", trigger, len(cfg.libraries))
    return results


def manual_rescan_path(cfg: AppConfig, path: str, *, trigger: str = "manual") -> str:
    """Scan a single path in its owning library. Returns the outcome key."""
    _ensure_db(cfg)
    target = Path(path)
    owning = _find_library_for_path(cfg, target)
    if owning is None:
        raise ValueError(f"{path} is not under any configured library")

    log.info("rescan started: path=%s trigger=%s", target, trigger)
    with _lock_for(owning.name), session_scope() as session:
        library = get_or_create_library(session, owning)
        found = FoundFile(
            path=target,
            parent=target.parent,
            plexmatch_path=find_nearest_plexmatch(target, stop_at=Path(owning.path)),
        )
        outcome = _process_file(session, cfg, library, found, force=True)
        _reclassify_library(session, library)
    log.info("rescan finished: path=%s trigger=%s outcome=%s", target, trigger, outcome)
    return outcome


def _find_library_for_path(cfg: AppConfig, target) -> LibraryConfig | None:
    target_str = str(Path(str(target)).resolve())
    for lib in cfg.libraries:
        try:
            lib_root = str(Path(lib.path).resolve()).rstrip("/")
            if target_str == lib_root or target_str.startswith(lib_root + "/"):
                return lib
        except Exception:
            continue
    return None
