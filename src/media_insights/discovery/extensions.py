"""Common extensions used across discovery."""

from __future__ import annotations

VIDEO_EXTS = {
    ".mkv", ".mp4", ".m4v", ".mov", ".avi", ".wmv", ".flv", ".webm",
    ".ts", ".m2ts", ".mts", ".mpg", ".mpeg", ".vob", ".ogv", ".rmvb",
}
SUBTITLE_EXTS = {".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt", ".sup", ".smi"}
PLEXMATCH_NAME = ".plexmatch"

# Group prefixes typical of fansub releases, used as a soft anime signal.
FANSUB_PREFIX_RE = __import__("re").compile(r"^\[(?P<group>[^\]]+)\]")
