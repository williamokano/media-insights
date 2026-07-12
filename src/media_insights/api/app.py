"""FastAPI app: REST + Web UI."""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session

from media_insights import config_store
from media_insights.api.serializers import serialise_file, serialise_item, serialise_library
from media_insights.config import AppConfig, LibraryConfig, resolve_config_path
from media_insights.db import ensure_schema, get_session, init_engine, session_scope
from media_insights.events import Dispatcher
from media_insights.models import Library, MediaFile, MediaItem
from media_insights.scanner import (
    MediaWatcher,
    ScanScheduler,
    get_or_create_library,
    manual_rescan_path,
    scan_all,
    scan_library,
)
from media_insights.web import mount_web

log = logging.getLogger(__name__)


class IdentifyRequest(BaseModel):
    guid: str | None = None
    imdb_id: str | None = None
    tmdb_id: int | None = None
    tvdb_id: int | None = None
    anidb_id: int | None = None
    classification: str | None = None  # "anime" | "tv" | "movie"


class ClassifyOverride(BaseModel):
    label: str


class State:
    config: AppConfig | None = None
    config_path: Path | None = None
    dispatcher: Dispatcher | None = None
    watcher: MediaWatcher | None = None
    scheduler: ScanScheduler | None = None


state = State()


def _db_url(cfg: AppConfig) -> str:
    return cfg.database.url or f"sqlite:///{cfg.config_dir}/media_insights.db"


def configure(cfg: AppConfig, config_path: str | Path | None = None) -> None:
    """Wire config + DB + background services. Called once on startup."""
    state.config = cfg
    state.config_path = resolve_config_path(config_path)
    init_engine(_db_url(cfg))
    ensure_schema()


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = state.config
    if cfg is None:
        raise RuntimeError("configure(cfg) must run before the app starts")

    state.dispatcher = Dispatcher(cfg, poll_seconds=2.0)
    state.dispatcher.start()

    state.watcher = MediaWatcher(
        cfg,
        on_path_changed=lambda p: _debounced_rescan(cfg, p),
    )
    state.watcher.start()

    state.scheduler = ScanScheduler(cfg)
    state.scheduler.start()

    log.info("media-insights API ready: http://%s:%d", cfg.server.host, cfg.server.port)
    try:
        yield
    finally:
        if state.watcher:
            state.watcher.stop()
        if state.scheduler:
            state.scheduler.stop()
        if state.dispatcher:
            state.dispatcher.stop()


def _debounced_rescan(cfg: AppConfig, path) -> None:
    """Watcher callback: rescan changed video files, prune deleted ones."""
    from media_insights.discovery.extensions import VIDEO_EXTS
    from media_insights.scanner import handle_missing_path

    try:
        target = Path(path)
    except Exception:
        return
    if not target.exists():
        # Deleted file (or a directory that took its files with it). A single
        # video row is pruned directly; anything else falls back to a library
        # scan whose prune pass reconciles the rest.
        if target.suffix.lower() in VIDEO_EXTS:
            log.info("watcher -> remove %s", target)
            try:
                handle_missing_path(cfg, str(target))
            except Exception as exc:
                log.warning("watcher removal failed for %s: %s", target, exc)
        else:
            owning = _lib_for_missing(cfg, target)
            if owning:
                log.info("watcher -> deletion under %s, rescanning library", owning.name)
                scan_library(cfg, owning, trigger="watcher")
        return
    if target.is_dir():
        owning = _lib_for(cfg, target)
        if owning:
            log.info("watcher -> scan library %s", owning.name)
            scan_library(cfg, owning, trigger="watcher")
        return
    if target.suffix.lower() not in VIDEO_EXTS:
        return
    log.info("watcher -> rescan %s", target)
    try:
        manual_rescan_path(cfg, str(target), trigger="watcher")
    except Exception as exc:
        log.warning("watcher rescan failed for %s: %s", target, exc)


def _lib_for_missing(cfg: AppConfig, target) -> Any:
    """Like _lib_for, but must not resolve() a path that no longer exists."""
    target_str = str(target)
    for lib in cfg.libraries:
        root = lib.path.rstrip("/")
        if target_str == root or target_str.startswith(root + "/"):
            return lib
    return None


def _lib_for(cfg: AppConfig, target) -> Any:
    target_str = str(target.resolve())
    for lib in cfg.libraries:
        root = lib.path.rstrip("/")
        if target_str == root or target_str.startswith(root + "/"):
            return lib
    return None


def _identity_snapshot(item: MediaItem) -> dict:
    return {
        "match_status": item.match_status,
        "classification_label": item.classification_label,
        "ids": {
            "imdb": item.imdb_id,
            "tmdb": item.tmdb_id,
            "tvdb": item.tvdb_id,
            "anidb": item.anidb_id,
        },
    }


def _require_config() -> AppConfig:
    cfg = state.config
    if cfg is None:
        raise HTTPException(503, "not configured")
    return cfg


def _require_config_path() -> Path:
    if state.config_path is None:
        raise HTTPException(503, "not configured")
    return state.config_path


def _require_existing_dir(path: str) -> None:
    if not Path(path).is_dir():
        raise HTTPException(
            400,
            f"path does not exist or is not a directory: {path} "
            "(mount it into the container, then retry)",
        )


def _background_scan(cfg: AppConfig, lib: LibraryConfig) -> None:
    """Fire-and-forget initial scan so a newly added library fills in without
    the caller having to wait on a potentially large directory tree."""

    def run() -> None:
        try:
            scan_library(cfg, lib, force=False, trigger="library-added")
        except Exception:
            log.exception("background scan of %s failed", lib.name)

    threading.Thread(target=run, daemon=True).start()


def create_app() -> FastAPI:
    from media_insights import __version__

    app = FastAPI(title="media-insights", version=__version__, lifespan=lifespan)
    static_dir = _static_dir()
    templates_dir = _templates_dir()
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # ---- API ----
    @app.get("/api/libraries")
    def list_libraries(session: Session = Depends(get_session)) -> dict:
        rows = session.query(Library).order_by(Library.name).all()
        return {"libraries": [serialise_library(r, state.config) for r in rows]}

    @app.post("/api/libraries", status_code=201)
    def create_library(body: LibraryConfig) -> dict:
        cfg = _require_config()
        config_path = _require_config_path()
        _require_existing_dir(body.path)
        try:
            config_store.add_library(cfg, config_path, body)
        except config_store.LibraryExistsError as exc:
            raise HTTPException(409, str(exc)) from exc
        except config_store.ConfigFileError as exc:
            raise HTTPException(500, str(exc)) from exc
        with session_scope() as session:
            row = get_or_create_library(session, body)
            result = serialise_library(row, cfg)
        if state.watcher:
            state.watcher.install_library(body.path)
        _background_scan(cfg, body)
        return result

    @app.put("/api/libraries/{library_id}")
    def update_library(library_id: int, body: LibraryConfig, session: Session = Depends(get_session)) -> dict:
        cfg = _require_config()
        config_path = _require_config_path()
        _require_existing_dir(body.path)
        row = session.get(Library, library_id)
        if row is None:
            raise HTTPException(404, "library not found")
        old_name, old_path = row.name, row.path
        try:
            config_store.update_library(cfg, config_path, old_name, body)
        except config_store.LibraryNotFoundError:
            # DB row exists but config.yaml no longer has this name (e.g. it
            # was hand-edited); add it fresh rather than fail the request.
            try:
                config_store.add_library(cfg, config_path, body)
            except config_store.LibraryExistsError as exc:
                raise HTTPException(409, str(exc)) from exc
        except config_store.LibraryExistsError as exc:
            raise HTTPException(409, str(exc)) from exc
        except config_store.ConfigFileError as exc:
            raise HTTPException(500, str(exc)) from exc
        row.name = body.name
        row.path = body.path
        row.kind = body.kind
        result = serialise_library(row, cfg)
        session.commit()
        path_changed = old_path != body.path
        if state.watcher and path_changed:
            state.watcher.uninstall_library(old_path)
            state.watcher.install_library(body.path)
        if path_changed:
            _background_scan(cfg, body)
        return result

    @app.delete("/api/libraries/{library_id}", status_code=204)
    def delete_library(
        library_id: int, purge: bool = False, session: Session = Depends(get_session)
    ) -> Response:
        cfg = _require_config()
        config_path = _require_config_path()
        row = session.get(Library, library_id)
        if row is None:
            raise HTTPException(404, "library not found")
        name, path = row.name, row.path
        if purge:
            session.delete(row)
            session.commit()
        try:
            config_store.remove_library(cfg, config_path, name)
        except config_store.LibraryNotFoundError:
            pass  # already absent from config; nothing to persist
        except config_store.ConfigFileError as exc:
            raise HTTPException(500, str(exc)) from exc
        if state.watcher:
            state.watcher.uninstall_library(path)
        return Response(status_code=204)

    @app.get("/api/items")
    def list_items(
        library: int | None = None,
        classification: str | None = None,
        unmatched: bool = False,
        limit: int = Query(50, le=500),
        offset: int = 0,
        session: Session = Depends(get_session),
    ) -> dict:
        q = session.query(MediaItem)
        if library is not None:
            q = q.filter(MediaItem.library_id == library)
        if classification:
            q = q.filter(MediaItem.classification_label == classification)
        if unmatched:
            q = q.filter(MediaItem.match_status == "unmatched")
        rows = q.order_by(MediaItem.title).offset(offset).limit(limit).all()
        return {"items": [serialise_item(r) for r in rows]}

    @app.get("/api/items/{item_id}")
    def get_item(item_id: int, session: Session = Depends(get_session)) -> dict:
        item = session.get(MediaItem, item_id)
        if not item:
            raise HTTPException(404, "item not found")
        return serialise_item(item, include_files=True)

    @app.post("/api/items/{item_id}/identify")
    def identify_item(item_id: int, body: IdentifyRequest, session: Session = Depends(get_session)) -> dict:
        from media_insights.events import bus

        item = session.get(MediaItem, item_id)
        if not item:
            raise HTTPException(404, "item not found")
        old = _identity_snapshot(item)
        if body.imdb_id is not None:
            item.imdb_id = body.imdb_id
        if body.tmdb_id is not None:
            item.tmdb_id = body.tmdb_id
        if body.tvdb_id is not None:
            item.tvdb_id = body.tvdb_id
        if body.anidb_id is not None:
            item.anidb_id = body.anidb_id
        if body.guid:
            from media_insights.discovery.plexmatch import GUID_PREFIXES
            if body.guid.startswith(GUID_PREFIXES):
                item.match_status = "matched"
        if body.classification in ("anime", "tv", "movie"):
            item.classification_label = body.classification
            item.classification_override = True
        if body.imdb_id or body.tmdb_id or body.tvdb_id or body.anidb_id or body.guid:
            item.match_status = "matched"
        new = _identity_snapshot(item)
        if new != old:
            bus.record_event(
                session,
                type_="item.identified",
                subject_id=item.id,
                subject_path=None,
                old=old,
                new=new,
            )
        session.commit()
        return serialise_item(item)

    @app.post("/api/items/{item_id}/classification")
    def override_classification(item_id: int, body: ClassifyOverride, session: Session = Depends(get_session)) -> dict:
        item = session.get(MediaItem, item_id)
        if not item:
            raise HTTPException(404, "item not found")
        if body.label not in ("anime", "tv", "movie"):
            raise HTTPException(400, "label must be anime|tv|movie")
        item.classification_label = body.label
        item.classification_override = True
        item.classification_confidence = 1.0
        session.commit()
        return serialise_item(item)

    @app.get("/api/unmatched")
    def list_unmatched(session: Session = Depends(get_session)) -> dict:
        rows = (
            session.query(MediaItem)
            .filter(MediaItem.match_status.in_(["unmatched", "unresolved"]))
            .order_by(MediaItem.title)
            .all()
        )
        return {"items": [serialise_item(r) for r in rows]}

    @app.get("/api/search")
    def search(q: str, session: Session = Depends(get_session)) -> dict:
        like = f"%{q}%"
        items = session.query(MediaItem).filter(MediaItem.title.ilike(like)).limit(100).all()
        files = (
            session.query(MediaFile)
            .filter(MediaFile.path.ilike(like))
            .limit(100)
            .all()
        )
        return {
            "items": [serialise_item(r) for r in items],
            "files": [serialise_file(r) for r in files],
        }

    @app.get("/api/files/{file_id}")
    def get_file(file_id: int, session: Session = Depends(get_session)) -> dict:
        f = session.get(MediaFile, file_id)
        if not f:
            raise HTTPException(404, "file not found")
        return serialise_file(f, include_tracks=True)

    @app.post("/api/scan")
    def trigger_scan(library: str | None = None) -> dict:
        cfg = _require_config()
        if library:
            for lib in cfg.libraries:
                if lib.name == library:
                    return scan_library(cfg, lib, force=True, trigger="api")
            raise HTTPException(404, f"no such library: {library}")
        return {"libraries": scan_all(cfg, force=True, trigger="api")}

    @app.post("/api/rescan")
    def rescan_path(body: dict) -> dict:
        cfg = state.config
        if cfg is None or not body.get("path"):
            raise HTTPException(400, "path is required")
        try:
            outcome = manual_rescan_path(cfg, body["path"], trigger="api")
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"outcome": outcome}

    # ---- Web UI ----
    templates = Jinja2Templates(directory=str(templates_dir)) if templates_dir.is_dir() else None
    mount_web(app, templates)

    @app.get("/", response_class=HTMLResponse)
    def root() -> RedirectResponse:
        return RedirectResponse(url="/dashboard", status_code=303)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    return app


def _static_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "web" / "static"


def _templates_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "web" / "templates"


__all__ = ["configure", "create_app", "state"]
