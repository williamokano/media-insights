"""Walker tests."""

from __future__ import annotations

from pathlib import Path

from media_insights.discovery.walker import (
    collect_subtitle_sidecars,
    iter_video_files,
)


def test_iter_video_files_finds_movies(tmp_path: Path) -> None:
    (tmp_path / "Movie (2020)").mkdir()
    (tmp_path / "Movie (2020)" / "movie.mkv").write_bytes(b"x")
    (tmp_path / "Movie (2020)" / "subs.srt").write_text("not a video", encoding="utf-8")

    results = list(iter_video_files(tmp_path, recursive=True))
    assert len(results) == 1
    assert results[0].path.name == "movie.mkv"


def test_walker_attaches_plexmatch(tmp_path: Path) -> None:
    folder = tmp_path / "Show" / "Season 01"
    folder.mkdir(parents=True)
    (folder / ".plexmatch").write_text("Title: Show\n", encoding="utf-8")
    (folder / "Show.S01E01.mkv").write_bytes(b"x")
    (folder / "Show.S01E02.mkv").write_bytes(b"x")

    results = list(iter_video_files(tmp_path, recursive=True))
    assert len(results) == 2
    assert all(r.plexmatch_path and r.plexmatch_path.name == ".plexmatch" for r in results)


def test_collect_subtitle_sidecars(tmp_path: Path) -> None:
    folder = tmp_path / "Movie (2020)"
    folder.mkdir()
    video = folder / "Movie.2020.mkv"
    video.write_bytes(b"x")
    (folder / "Movie.2020.en.srt").write_text("x", encoding="utf-8")
    (folder / "Movie.2020.en.forced.srt").write_text("x", encoding="utf-8")
    (folder / "Movie.2020.pt-BR.srt").write_text("x", encoding="utf-8")
    # Same exact stem: this counts as a sidecar (the simplest case).
    (folder / "Movie.2020.ass").write_text("x", encoding="utf-8")
    # Completely unrelated name -> not a sidecar.
    (folder / "Unrelated.ass").write_text("x", encoding="utf-8")

    sc = collect_subtitle_sidecars(video)
    names = sorted(p.name for p in sc)
    assert "Movie.2020.en.srt" in names
    assert "Movie.2020.en.forced.srt" in names
    assert "Movie.2020.pt-BR.srt" in names
    assert "Movie.2020.ass" in names
    assert "Unrelated.ass" not in names
