"""Subtitle-coverage: engine, API, Web, and CLI-facing language resolution.

Which shows have a configurable language (Portuguese by default) in every
episode, and for the ones that don't, exactly which episodes are missing it.
Scoped to episodic shows (anime + TV) -- movies are excluded everywhere.
"""

from __future__ import annotations

import tempfile

from fastapi.testclient import TestClient

from media_insights.api import configure, create_app
from media_insights.config import (
    AppConfig,
    DatabaseConfig,
    FingerprintConfig,
    ScheduleConfig,
    SubtitlesConfig,
    WatcherConfig,
)
from media_insights.db import init_engine, reset_for_tests, run_migrations, session_scope
from media_insights.models import Library, MediaFile, MediaItem, Season, Track
from media_insights.subtitle_coverage import compute_coverage, resolve_language


def _setup(*, coverage_language: str = "pt") -> tuple[TestClient, AppConfig]:
    tmpdir = tempfile.mkdtemp(prefix="mi-subs-")
    db_url = f"sqlite:///{tmpdir}/test.db"
    reset_for_tests()
    init_engine(db_url)
    run_migrations(db_url)
    cfg = AppConfig(
        config_dir=tmpdir,
        data_dir=tmpdir,
        log_level="WARNING",
        database=DatabaseConfig(url=db_url),
        fingerprint=FingerprintConfig(),
        watcher=WatcherConfig(enabled=False),
        schedule=ScheduleConfig(enabled=False),
        subtitles=SubtitlesConfig(coverage_language=coverage_language),
        libraries=[],
    )
    configure(cfg, tmpdir + "/config.yaml")
    return TestClient(create_app()), cfg


def _get_or_create_library(session, name: str, kind: str) -> Library:
    library = session.query(Library).filter_by(name=name).one_or_none()
    if library is None:
        library = Library(name=name, path=f"/data/{name.lower()}", kind=kind)
        session.add(library)
        session.flush()
    return library


def _seed_show(
    session,
    *,
    library_name: str = "Anime",
    library_kind: str = "anime",
    title: str = "Frieren",
    episode_langs: tuple[str | None, ...] = ("pt", "pt"),
) -> tuple[int, int]:
    """A show with one episode per entry in `episode_langs`.

    Each entry is the *normalized* subtitle language stored on that episode
    (or None for "no subtitle in the target language"); an English subtitle
    and Japanese audio track are always present too, since Track.language
    always holds an already-normalized code (normalization happens at
    ingestion, in language.py) -- never a raw locale tag like 'pt-BR'.
    """
    library = _get_or_create_library(session, library_name, library_kind)
    item = MediaItem(
        library_id=library.id, kind="show", title=title, year=2023, match_status="matched",
    )
    session.add(item)
    session.flush()

    season = Season(item_id=item.id, number=1)
    session.add(season)
    session.flush()

    for i, target_lang in enumerate(episode_langs, start=1):
        f = MediaFile(
            season_id=season.id,
            path=f"/data/{library_name.lower()}/{title}/S01E{i:02d}.mkv",
            episode_numbers=[i],
        )
        session.add(f)
        session.flush()
        session.add(Track(file_id=f.id, position=0, kind="audio", language="ja", language_raw="jpn"))
        session.add(Track(file_id=f.id, position=1, kind="subtitle", language="en", language_raw="eng"))
        if target_lang:
            session.add(
                Track(file_id=f.id, position=2, kind="subtitle", language=target_lang, language_raw=target_lang)
            )
    session.commit()
    return item.id, library.id


def _seed_movie(session, *, library_name: str = "Anime", library_kind: str = "anime") -> int:
    library = _get_or_create_library(session, library_name, library_kind)
    item = MediaItem(library_id=library.id, kind="movie", title="A Movie", year=2020, match_status="matched")
    session.add(item)
    session.flush()
    season = Season(item_id=item.id, number=None)
    session.add(season)
    session.flush()
    f = MediaFile(season_id=season.id, path=f"/data/{library_name.lower()}/A Movie.mkv")
    session.add(f)
    session.flush()
    session.add(Track(file_id=f.id, position=0, kind="subtitle", language="pt", language_raw="pt"))
    session.commit()
    return item.id


def test_resolve_language_handles_variants() -> None:
    assert resolve_language("pt") == ("pt", "Portuguese")
    assert resolve_language("pt-BR")[0] == "pt"
    assert resolve_language("por")[0] == "pt"
    assert resolve_language("portuguese")[0] == "pt"
    assert resolve_language("Portuguese")[0] == "pt"
    assert resolve_language("not-a-real-language-xyz") is None


def test_compute_coverage_flags_complete_and_incomplete_and_excludes_movies() -> None:
    _setup()
    with session_scope() as session:
        _seed_show(session, title="Complete Show", episode_langs=("pt", "pt"))
        _seed_show(session, title="Incomplete Show", episode_langs=("pt", None))
        _seed_movie(session)

    with session_scope() as session:
        results = {r.title: r for r in compute_coverage(session, "pt")}

    assert "A Movie" not in results
    assert results["Complete Show"].complete is True
    assert results["Complete Show"].episodes_missing == 0
    assert results["Incomplete Show"].complete is False
    assert results["Incomplete Show"].episodes_with == 1
    assert results["Incomplete Show"].episodes_missing == 1
    missing_paths = [e.path for e in results["Incomplete Show"].episodes if not e.has_language]
    assert missing_paths == ["/data/anime/Incomplete Show/S01E02.mkv"]


def test_api_uses_configured_default_language() -> None:
    client, _ = _setup(coverage_language="pt")
    with session_scope() as session:
        _seed_show(session, title="Frieren", episode_langs=("pt", "pt"))

    body = client.get("/api/subtitle-coverage").json()
    assert body["language"] == "pt"
    assert body["language_display"] == "Portuguese"
    assert body["summary"]["total"] == 1
    assert body["summary"]["complete"] == 1


def test_api_accepts_a_full_language_name_as_a_query_param() -> None:
    """The query param resolves any token language.py understands, even
    though the DB itself only ever stores already-normalized codes."""
    client, _ = _setup()
    with session_scope() as session:
        _seed_show(session, title="Frieren", episode_langs=("pt",))

    body = client.get("/api/subtitle-coverage", params={"language": "portuguese"}).json()
    assert body["language"] == "pt"
    assert body["items"][0]["complete"] is True


def test_api_rejects_unrecognized_language() -> None:
    client, _ = _setup()
    r = client.get("/api/subtitle-coverage", params={"language": "not-a-real-language-xyz"})
    assert r.status_code == 400


def test_api_complete_filter() -> None:
    client, _ = _setup()
    with session_scope() as session:
        _seed_show(session, title="Complete Show", episode_langs=("pt",))
        _seed_show(session, title="Incomplete Show", episode_langs=(None,))

    complete = client.get("/api/subtitle-coverage", params={"complete": "true"}).json()
    assert [it["title"] for it in complete["items"]] == ["Complete Show"]

    incomplete = client.get("/api/subtitle-coverage", params={"complete": "false"}).json()
    assert [it["title"] for it in incomplete["items"]] == ["Incomplete Show"]


def test_api_scopes_to_one_library() -> None:
    client, _ = _setup()
    with session_scope() as session:
        _seed_show(session, library_name="Anime", library_kind="anime", title="Anime Show", episode_langs=("pt",))
        _seed_show(session, library_name="TV", library_kind="tv", title="TV Show", episode_langs=("pt",))
        anime_lib_id = session.query(Library).filter_by(name="Anime").one().id

    body = client.get("/api/subtitle-coverage", params={"library": str(anime_lib_id)}).json()
    assert [it["title"] for it in body["items"]] == ["Anime Show"]


def test_web_page_renders_complete_and_incomplete_sections() -> None:
    client, _ = _setup()
    with session_scope() as session:
        _seed_show(session, title="Frieren", episode_langs=("pt", None))

    r = client.get("/subtitle-coverage")
    assert r.status_code == 200
    assert "<title>Subtitles" in r.text
    assert "Frieren" in r.text
    assert "S01E02.mkv" in r.text  # the missing episode's path


def test_web_page_reports_unrecognized_language() -> None:
    client, _ = _setup()
    r = client.get("/subtitle-coverage", params={"language": "not-a-real-language-xyz"})
    assert r.status_code == 200
    assert "Unrecognized language" in r.text
