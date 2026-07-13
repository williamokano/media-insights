"""Web UI + list-view regression coverage.

Three real bugs found from a live report:
1. GET /api/items (the list view) dropped audio_summary/subtitle_summary
   from every file entry, even though those are plain columns and cost
   nothing extra to include -- you had to hit /api/items/{id} to see them.
2. The dashboard's "Titles" card linked straight to /api/items (raw JSON)
   because no browsable "all titles" HTML page existed at all.
3. The events list showed a "skipped" badge with zero explanation, which
   reads as "your files were ignored" when it actually just means "no
   webhook/exec_hook is configured to deliver this to."
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from media_insights.api import configure, create_app
from media_insights.events import Dispatcher
from media_insights.scanner import scan_library
from tests.test_e2e_scan import _config_for


def _client_for(lib, *, drain_events: bool = False) -> TestClient:
    cfg = _config_for(lib)
    scan_library(cfg, lib, force=True)
    if drain_events:
        # Simulate the background dispatcher having run once, without
        # depending on the lifespan's real poll-interval background thread.
        Dispatcher(cfg).drain_once()
    configure(cfg, cfg.config_dir + "/config.yaml")
    return TestClient(create_app())


def test_api_items_list_includes_subtitle_and_audio_summary(tmp_anime) -> None:
    client = _client_for(tmp_anime)
    body = client.get("/api/items").json()
    file = body["items"][0]["seasons"][0]["files"][0]
    assert "audio_summary" in file
    assert "subtitle_summary" in file
    assert file["subtitle_summary"] is not None
    assert "en" in file["subtitle_summary"]  # external sidecar included


def test_titles_page_renders_and_lists_scanned_item(tmp_library) -> None:
    client = _client_for(tmp_library)
    r = client.get("/titles")
    assert r.status_code == 200
    assert "Interstellar" in r.text
    assert "<title>Titles" in r.text


def test_titles_page_unmatched_filter(tmp_library) -> None:
    client = _client_for(tmp_library)
    # Interstellar was parsed by guessit alone (no .plexmatch, no external
    # ID attached), so it's "unresolved" -- genuinely part of the unmatched
    # queue -- and must show up here.
    r = client.get("/titles?unmatched=true")
    assert r.status_code == 200
    assert "Interstellar" in r.text

    # A classification filter that doesn't match anything shows the empty state.
    r = client.get("/titles?classification=anime")
    assert "No titles match these filters" in r.text
    assert "Interstellar" not in r.text


def test_dashboard_titles_card_links_to_titles_page_not_api(tmp_library) -> None:
    client = _client_for(tmp_library)
    r = client.get("/dashboard")
    assert 'href="/titles"' in r.text
    assert 'href="/api/items"' not in r.text


def test_dashboard_explains_skipped_events(tmp_library) -> None:
    # No webhooks configured -> the dispatcher marks events "skipped".
    client = _client_for(tmp_library, drain_events=True)
    r = client.get("/dashboard")
    assert "no webhook or exec hook is configured" in r.text.lower()


def test_events_page_explains_skipped_status(tmp_library) -> None:
    client = _client_for(tmp_library, drain_events=True)
    r = client.get("/events")
    assert "skipped" in r.text.lower()
    assert "not an error" in r.text.lower()


def test_nav_has_titles_link(tmp_library) -> None:
    client = _client_for(tmp_library)
    r = client.get("/dashboard")
    assert '<a href="/titles">Titles</a>' in r.text


def test_titles_page_handles_empty_library_filter(tmp_library) -> None:
    """The 'All libraries' <select> option submits library= (empty string),
    not an omitted parameter -- must render the page, not a 422 int-parsing
    error, and must behave the same as not filtering by library at all."""
    client = _client_for(tmp_library)
    r = client.get("/titles?library=&classification=&unmatched=true")
    assert r.status_code == 200
    assert "<title>Titles" in r.text
    assert "Interstellar" in r.text


def test_titles_pager_links_render_not_422(tmp_library) -> None:
    """The pager carries every filter through to the next page, including
    unmatched= (empty when the box is unchecked). An empty bool must mean
    "not filtering", not a 422 -- this is the exact URL the pager builds."""
    client = _client_for(tmp_library)
    r = client.get("/titles?offset=50&limit=50&library=&classification=&unmatched=")
    assert r.status_code == 200
    assert "<title>Titles" in r.text


def test_titles_empty_unmatched_does_not_filter(tmp_library) -> None:
    """unmatched= (empty) must behave like the filter is off, i.e. show
    everything -- not like unmatched=true."""
    client = _client_for(tmp_library)
    r = client.get("/titles?unmatched=")
    assert r.status_code == 200
    assert "Interstellar" in r.text
