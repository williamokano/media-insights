"""Filesystem walker."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from media_insights.discovery.extensions import (
    PLEXMATCH_NAME,
    SUBTITLE_EXTS,
    VIDEO_EXTS,
)

log = logging.getLogger(__name__)


@dataclass(slots=True)
class FoundFile:
    path: Path
    parent: Path
    plexmatch_path: Path | None = None


def is_hidden(path: Path) -> bool:
    return any(p.startswith(".") and p not in (".", "..") for p in path.parts)


def iter_video_files(
    root: str | Path, recursive: bool = True
) -> Iterator[FoundFile]:
    """Yield every video file under root, attaching the nearest .plexmatch if any."""
    root = Path(root)
    if not root.exists():
        log.warning("library path does not exist: %s", root)
        return

    if root.is_file():
        if root.suffix.lower() in VIDEO_EXTS:
            yield FoundFile(
                path=root, parent=root.parent, plexmatch_path=find_nearest_plexmatch(root)
            )
        return

    plexmatch_cache: dict[Path, Path | None] = {}

    def nearest_plexmatch(path: Path) -> Path | None:
        for parent in (path.parent, *path.parents):
            if not parent or (parent == root.parent and parent != root):
                continue
            if parent in plexmatch_cache:
                if plexmatch_cache[parent] is not None:
                    return plexmatch_cache[parent]
                continue
            candidate = parent / PLEXMATCH_NAME
            if candidate.is_file():
                plexmatch_cache[parent] = candidate
                return candidate
            plexmatch_cache[parent] = None
        return None

    glob = root.rglob if recursive else root.glob
    for entry in glob("*"):
        if not entry.is_file():
            continue
        if is_hidden(entry):
            continue
        if entry.suffix.lower() in VIDEO_EXTS:
            yield FoundFile(
                path=entry,
                parent=entry.parent,
                plexmatch_path=nearest_plexmatch(entry),
            )
        elif entry.suffix.lower() in SUBTITLE_EXTS:
            # Skip here; subtitles are picked up relative to their video file.
            continue


def find_nearest_plexmatch(path: Path, stop_at: Path | None = None) -> Path | None:
    """Walk up from `path` looking for a .plexmatch file.

    `stop_at` bounds the walk to a library root so a stray .plexmatch above
    the library can't hijack the match.
    """
    for parent in (path.parent, *path.parents):
        candidate = parent / PLEXMATCH_NAME
        if candidate.is_file():
            return candidate
        if stop_at is not None and parent == stop_at:
            break
    return None


def collect_subtitle_sidecars(video_path: Path) -> list[Path]:
    """Find subtitle sidecars in the same directory that name-match this video."""
    siblings: list[Path] = []
    stem = video_path.stem
    parent = video_path.parent
    for entry in parent.iterdir():
        if not entry.is_file() or entry.suffix.lower() not in SUBTITLE_EXTS:
            continue
        # The standard pattern is "<videoname>.<lang>(.<flags>).<ext>"
        # .idx pairs with .sub; either counts as a sidecar.
        if entry.stem == stem:
            siblings.append(entry)
            continue
        if entry.stem.startswith(stem + "."):
            siblings.append(entry)
    return siblings
