"""Shared pytest fixtures."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from media_insights.config import AppConfig, LibraryConfig
from media_insights.db import init_engine, reset_for_tests
from media_insights.models import Base


@pytest.fixture(scope="session")
def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


@pytest.fixture
def tmp_library(tmp_path: Path, ffmpeg_available: bool) -> Iterator[LibraryConfig]:
    """Create a fake library with two short mp4 files."""
    skip = os.environ.get("MI_SKIP_FFMPEG_TESTS") == "1"
    if not ffmpeg_available or skip:
        pytest.skip("ffmpeg not available")
    yield from _make_library(tmp_path, name="Movies", kind="movie", pattern="movie")


@pytest.fixture
def tmp_anime(tmp_path: Path, ffmpeg_available: bool) -> Iterator[LibraryConfig]:
    skip = os.environ.get("MI_SKIP_FFMPEG_TESTS") == "1"
    if not ffmpeg_available or skip:
        pytest.skip("ffmpeg not available")
    yield from _make_library(tmp_path, name="Anime", kind="anime", pattern="anime")


@pytest.fixture
def tmp_tv(tmp_path: Path, ffmpeg_available: bool) -> Iterator[LibraryConfig]:
    skip = os.environ.get("MI_SKIP_FFMPEG_TESTS") == "1"
    if not ffmpeg_available or skip:
        pytest.skip("ffmpeg not available")
    yield from _make_library(tmp_path, name="TV", kind="tv", pattern="tv")


def _make_library(tmp_path: Path, *, name: str, kind: str, pattern: str) -> Iterator[LibraryConfig]:
    from tests.fixtures.media_factory import build_library

    lib_path = build_library(tmp_path / name, pattern=pattern)
    yield LibraryConfig(name=name, path=str(lib_path), kind=kind)  # type: ignore[arg-type]


@pytest.fixture
def scratch_config(tmp_path: Path) -> AppConfig:
    """An isolated AppConfig pointing at a per-test SQLite DB."""
    cfg = AppConfig(
        config_dir=str(tmp_path),
        data_dir=str(tmp_path / "data"),
        log_level="WARNING",
        database={"url": f"sqlite:///{tmp_path}/test.db"},
        watcher={"enabled": False},
        schedule={"enabled": False},
        libraries=[],
    )
    reset_for_tests()
    init_engine(cfg.database.url)
    Base.metadata.create_all(init_engine(cfg.database.url).connect())
    return cfg


@pytest.fixture
def sample_ffprobe_streams() -> dict:
    """A canned ffprobe JSON used by normalize/parse unit tests."""
    return {
        "format": {"format_name": "matroska,webm", "duration": "120.0", "bit_rate": "5000000"},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "hevc",
                "width": 1920,
                "height": 1080,
                "avg_frame_rate": "24000/1001",
                "color_transfer": "smpte2084",
                "tags": {"language": "jpn"},
            },
            {
                "codec_type": "audio",
                "codec_name": "truehd",
                "channels": 8,
                "channel_layout": "7.1",
                "bit_rate": "3000000",
                "tags": {"language": "jpn", "title": "Japanese TrueHD Atmos"},
            },
            {
                "codec_type": "audio",
                "codec_name": "aac",
                "channels": 2,
                "channel_layout": "stereo",
                "tags": {"language": "en", "title": "English commentary"},
                "disposition": {"default": 1},
            },
            {
                "codec_type": "subtitle",
                "codec_name": "ass",
                "tags": {"language": "en", "title": "English"},
            },
        ],
    }


@pytest.fixture
def synthetic_video(tmp_path: Path, ffmpeg_available: bool) -> Path:
    """A single short video with two audio tracks."""
    if not ffmpeg_available or os.environ.get("MI_SKIP_FFMPEG_TESTS") == "1":
        pytest.skip("ffmpeg not available")
    out = tmp_path / "sample.mkv"
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=10",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=880:duration=2",
        "-map", "0:v", "-map", "1:a", "-map", "2:a",
        "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=jpn",
        "-c:v", "libx264", "-preset", "ultrafast",
        str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out
