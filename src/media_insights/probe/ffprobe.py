"""ffprobe subprocess wrapper."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

from media_insights.probe.normalize import ProbeResult, parse_ffprobe

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 60


class ProbeError(RuntimeError):
    """Raised when ffprobe can't be run or returns garbage."""


def ffprobe_path(override: str = "") -> str:
    if override:
        return override
    found = shutil.which("ffprobe")
    if not found:
        raise ProbeError("ffprobe not found in PATH; install ffmpeg")
    return found


def probe(path: str | Path, ffprobe_bin: str = "", timeout: float = DEFAULT_TIMEOUT) -> ProbeResult:
    """Run ffprobe -show_streams -show_format -of json and parse."""
    bin_path = ffprobe_path(ffprobe_bin)
    cmd = [
        bin_path,
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        "-show_data",
        str(path),
    ]
    log.debug("running %s", cmd)
    try:
        completed = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise ProbeError(f"ffprobe timed out for {path}") from exc

    if completed.returncode != 0:
        raise ProbeError(f"ffprobe failed for {path}: {completed.stderr.strip()}")
    try:
        data = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ProbeError(f"ffprobe returned invalid JSON for {path}") from exc
    return parse_ffprobe(data)
