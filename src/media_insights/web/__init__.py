"""Web UI mount helper."""

from __future__ import annotations

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from media_insights.classify import misfiled_condition
from media_insights.db import get_session
from media_insights.models import ChangeEvent, Library, MediaItem
from media_insights.query_params import parse_optional_bool, parse_optional_id


def mount_web(app: FastAPI, templates: Jinja2Templates | None) -> None:
    """Mount HTML pages onto the FastAPI app."""
    if templates is None:
        return

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard(
        request: Request,
        session: Session = Depends(get_session),
    ):
        libs = session.query(Library).order_by(Library.name).all()
        items = session.query(MediaItem).all()
        unmatched = [i for i in items if i.match_status in ("unmatched", "unresolved")]
        events = (
            session.query(ChangeEvent)
            .order_by(ChangeEvent.created_at.desc())
            .limit(20)
            .all()
        )
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "libraries": libs,
                "items": items,
                "unmatched": unmatched,
                "events": events,
            },
        )

    @app.get("/titles", response_class=HTMLResponse)
    def titles(
        request: Request,
        library: str | None = None,
        classification: str | None = None,
        unmatched: str | None = None,
        offset: int = 0,
        limit: int = Query(50, le=200),
        session: Session = Depends(get_session),
    ):
        library_id = parse_optional_id(library)
        only_unmatched = parse_optional_bool(unmatched)
        q = session.query(MediaItem)
        if library_id is not None:
            q = q.filter(MediaItem.library_id == library_id)
        if classification:
            q = q.filter(MediaItem.classification_label == classification)
        if only_unmatched:
            q = q.filter(MediaItem.match_status.in_(["unmatched", "unresolved"]))
        total = q.count()
        rows = q.order_by(MediaItem.title).offset(offset).limit(limit).all()
        libs = session.query(Library).order_by(Library.name).all()
        return templates.TemplateResponse(
            request,
            "titles.html",
            {
                "items": rows,
                "libraries": libs,
                "total": total,
                "offset": offset,
                "limit": limit,
                "library": library_id,
                "classification": classification,
                "unmatched": only_unmatched,
            },
        )

    @app.get("/misfiled", response_class=HTMLResponse)
    def misfiled(
        request: Request,
        session: Session = Depends(get_session),
    ):
        """Titles whose classification disagrees with the library they're in.

        See classify/misfiled.py for the compatibility rules (an anime movie
        is correctly filed in a Movies *or* an Anime library).
        """
        rows = (
            session.query(MediaItem, Library)
            .join(Library, MediaItem.library_id == Library.id)
            .filter(misfiled_condition())
            .order_by(Library.name, MediaItem.title)
            .all()
        )
        return templates.TemplateResponse(
            request,
            "misfiled.html",
            {"items": [{"item": item, "library": library} for item, library in rows]},
        )

    @app.get("/libraries", response_class=HTMLResponse)
    def libraries(
        request: Request,
        session: Session = Depends(get_session),
    ):
        # Lazy import: media_insights.api.app imports mount_web from this
        # module, so importing state at module scope would be circular.
        from media_insights.api.app import state

        rows = session.query(Library).order_by(Library.name).all()
        configured_names = {lib.name for lib in (state.config.libraries if state.config else [])}
        return templates.TemplateResponse(
            request, "libraries.html", {"libraries": rows, "configured_names": configured_names}
        )

    @app.get("/items/{item_id}", response_class=HTMLResponse)
    def item_detail(
        item_id: int,
        request: Request,
        session: Session = Depends(get_session),
    ):
        item = session.get(MediaItem, item_id)
        if not item:
            return HTMLResponse("not found", status_code=404)
        return templates.TemplateResponse(request, "item.html", {"item": item})

    @app.get("/unmatched", response_class=HTMLResponse)
    def unmatched(
        request: Request,
        session: Session = Depends(get_session),
    ):
        rows = (
            session.query(MediaItem)
            .filter(MediaItem.match_status.in_(["unmatched", "unresolved"]))
            .order_by(MediaItem.title)
            .all()
        )
        return templates.TemplateResponse(
            request, "unmatched.html", {"items": rows}
        )

    @app.get("/events", response_class=HTMLResponse)
    def events(
        request: Request,
        session: Session = Depends(get_session),
    ):
        rows = (
            session.query(ChangeEvent)
            .order_by(ChangeEvent.created_at.desc())
            .limit(200)
            .all()
        )
        return templates.TemplateResponse(
            request, "events.html", {"events": rows}
        )

    @app.get("/search", response_class=HTMLResponse)
    def search_page(request: Request, q: str = ""):
        return templates.TemplateResponse(request, "search.html", {"q": q})

    @app.get("/subtitle-coverage", response_class=HTMLResponse)
    def subtitle_coverage_page(
        request: Request,
        language: str | None = None,
        library: str | None = None,
        session: Session = Depends(get_session),
    ):
        # Lazy import: same circular-import reason as /libraries above.
        from media_insights.api.app import state
        from media_insights.subtitle_coverage import compute_coverage, resolve_language

        cfg = state.config
        token = language or (cfg.subtitles.coverage_language if cfg else "pt")
        resolved = resolve_language(token)
        library_id = parse_optional_id(library)
        libs = session.query(Library).order_by(Library.name).all()

        items = compute_coverage(session, resolved[0], library_id=library_id) if resolved else []
        return templates.TemplateResponse(
            request,
            "subtitle_coverage.html",
            {
                "language_token": token,
                "language_code": resolved[0] if resolved else None,
                "language_display": resolved[1] if resolved else None,
                "unrecognized": resolved is None,
                "libraries": libs,
                "library": library_id,
                "complete_items": [it for it in items if it.complete],
                "incomplete_items": [it for it in items if not it.complete],
            },
        )
