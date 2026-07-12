"""Generate small real media files with ffmpeg for tests."""

from __future__ import annotations

import subprocess
from pathlib import Path


def _ffmpeg(args: list[str]) -> None:
    completed = subprocess.run(args, capture_output=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed: {completed.stderr.decode(errors='ignore')[-500:]}"
        )


def _short_video(path: Path, *, lang_audio: str = "eng", lang_sub: str | None = None) -> None:
    """Create a 2-second H.264 video with a single audio track."""
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=15",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-map", "0:v", "-map", "1:a",
        "-metadata:s:a:0", f"language={lang_audio}",
        "-c:v", "libx264", "-preset", "ultrafast",
        "-c:a", "aac",
        str(path),
    ]
    _ffmpeg(cmd)
    if lang_sub:
        sub = path.with_suffix(".srt")
        sub.write_text(
            "1\n00:00:01,000 --> 00:00:02,000\nhello world\n", encoding="utf-8"
        )


def build_library(root: Path, *, pattern: str = "movie") -> Path:
    """Build a small library. `pattern` selects the layout."""
    if pattern == "movie":
        _short_video(root / "Interstellar (2014)" / "Interstellar.2014.mkv", lang_audio="eng")
    elif pattern == "tv":
        _short_video(
            root / "Breaking Bad" / "Season 01" / "Breaking.Bad.S01E01.mkv",
            lang_audio="eng",
        )
        _short_video(
            root / "Breaking Bad" / "Season 01" / "Breaking.Bad.S01E02.mkv",
            lang_audio="eng",
        )
    elif pattern == "anime":
        # Fansub-style filename, Japanese primary audio, English sub
        _short_video(
            root / "[SubsPlease] Frieren" / "[SubsPlease] Frieren - 01 (1080p) [ABCDEF].mkv",
            lang_audio="jpn",
            lang_sub="en",
        )
        # External sidecar
        sidecar = (
            root / "[SubsPlease] Frieren"
            / "[SubsPlease] Frieren - 01 (1080p) [ABCDEF].en.forced.srt"
        )
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text("1\n00:00:00,500 --> 00:00:02,000\nforced line\n", encoding="utf-8")
    else:
        raise ValueError(f"unknown pattern {pattern!r}")
    return root


def rewrite_with_different_codec(path: Path, *, lang_audio: str = "eng") -> None:
    """Simulate an Arr upgrade by re-encoding with different codec / audio."""
    tmp = path.with_suffix(".reenc.mkv")
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=duration=2:size=1280x720:rate=15",
        "-f", "lavfi", "-i", "sine=frequency=660:duration=2",
        "-map", "0:v", "-map", "1:a",
        "-metadata:s:a:0", f"language={lang_audio}",
        "-c:v", "libx265", "-preset", "ultrafast",
        "-c:a", "ac3",
        str(tmp),
    ]
    _ffmpeg(cmd)
    tmp.replace(path)
