"""Web UI mount helper."""

from __future__ import annotations

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from media_insights.db import get_session
from media_insights.models import ChangeEvent, Library, MediaItem


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

    @app.get("/libraries", response_class=HTMLResponse)
    def libraries(
        request: Request,
        session: Session = Depends(get_session),
    ):
        rows = session.query(Library).order_by(Library.name).all()
        return templates.TemplateResponse(
            request, "libraries.html", {"libraries": rows}
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
