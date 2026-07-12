"""guessit-based name parser."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from guessit import guessit

log = logging.getLogger(__name__)


@dataclass(slots=True)
class ParsedTitle:
    title: str | None
    year: int | None
    kind: str  # "movie" | "show" | "unknown"
    season: int | None
    episodes: list[int]
    container: str | None
    release_group: str | None
    source: str | None
    screen_size: str | None
    video_codec: str | None
    audio_codec: str | None
    audio_channels: str | None
    anime: bool = False
    is_3d: bool = False
    streaming_service: str | None = None
    episode_title: str | None = None

    @property
    def episode_number(self) -> int | None:
        return self.episodes[0] if self.episodes else None


def parse(name: str) -> ParsedTitle:
    """Parse a filename or folder name with guessit."""
    try:
        info = guessit(name)
    except Exception as exc:
        log.debug("guessit failed for %s: %s", name, exc)
        return ParsedTitle(
            title=None, year=None, kind="unknown", season=None,
            episodes=[], container=None, release_group=None, source=None,
            screen_size=None, video_codec=None, audio_codec=None,
            audio_channels=None,
        )

    title = info.get("title")
    if isinstance(title, list):
        title = " ".join(title)
    year = info.get("year")
    if year is not None and not isinstance(year, int):
        try:
            year = int(year)
        except (TypeError, ValueError):
            year = None

    guessit_type = info.get("type")
    if guessit_type == "episode":
        kind = "show"
    elif guessit_type == "movie":
        kind = "movie"
    else:
        kind = "unknown"

    season = info.get("season")
    if season is not None and not isinstance(season, int):
        try:
            season = int(season)
        except (TypeError, ValueError):
            season = None

    episodes: list[int] = []
    ep = info.get("episode")
    if ep is not None:
        if isinstance(ep, list):
            for e in ep:
                try:
                    episodes.append(int(e))
                except (TypeError, ValueError):
                    pass
        else:
            try:
                episodes.append(int(ep))
            except (TypeError, ValueError):
                pass

    container = info.get("container")
    release_group = info.get("release_group")
    source = info.get("source")
    screen_size = info.get("screen_size")
    video_codec = info.get("video_codec")
    audio_codec = info.get("audio_codec")
    audio_channels = info.get("audio_channels")
    streaming_service = info.get("streaming_service")

    return ParsedTitle(
        title=title,
        year=year,
        kind=kind,
        season=season,
        episodes=episodes,
        container=container,
        release_group=release_group,
        source=source,
        screen_size=screen_size,
        video_codec=video_codec,
        audio_codec=audio_codec,
        audio_channels=audio_channels,
        anime=bool(info.get("anime")),
        is_3d=bool(info.get("is_3d")),
        streaming_service=streaming_service,
        episode_title=_as_str(info.get("episode_title")),
    )


def _as_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        return " ".join(str(v) for v in value)
    return str(value)
