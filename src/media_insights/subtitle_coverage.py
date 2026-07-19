"""Subtitle-language coverage across episodic libraries (anime + TV).

Answers two related questions for a configurable language (Portuguese by
default, see config.SubtitlesConfig): which shows have that language in
*every* episode, and for the ones that don't, exactly which episodes are
missing it and how many.

Movies are deliberately out of scope. "N of M episodes" doesn't mean anything
for a single file, and a movie already gets a pass/fail answer for free from
`missing_subtitle_language` on `GET /api/items`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import exists
from sqlalchemy.orm import Session

from media_insights.language import display_name, normalize_language
from media_insights.models import Library, MediaFile, MediaItem, Season, Track


@dataclass(slots=True)
class EpisodeCoverage:
    file_id: int
    path: str
    season: int | None
    episode_numbers: list[int]
    has_language: bool


@dataclass(slots=True)
class ItemCoverage:
    item_id: int
    title: str
    year: int | None
    library_id: int
    library_name: str
    episodes_total: int
    episodes_with: int
    episodes_missing: int
    complete: bool
    episodes: list[EpisodeCoverage] = field(default_factory=list)


def resolve_language(token: str) -> tuple[str, str] | None:
    """(normalized_code, display_name) for a language token.

    None if the token isn't recognized. 'pt', 'pt-BR', 'por', 'portuguese',
    'Portuguese' all resolve to the same ('pt', 'Portuguese').
    """
    info = normalize_language(token)
    if info is None or info.normalized is None:
        return None
    return info.normalized, (display_name(info.normalized) or info.normalized)


def compute_coverage(
    session: Session, language_code: str, *, library_id: int | None = None
) -> list[ItemCoverage]:
    """Per-show subtitle coverage for an already-normalized `language_code`.

    One query -- MediaItem(kind='show') -> Season -> MediaFile, with a
    correlated EXISTS(subtitle Track where language == language_code) per
    file -- so there's no N+1 regardless of library size. Grouping into
    ItemCoverage happens in Python since the per-episode breakdown, not just
    a count, is the point.
    """
    has_language_track = (
        exists()
        .where(Track.file_id == MediaFile.id)
        .where(Track.kind == "subtitle")
        .where(Track.language == language_code)
    )
    q = (
        session.query(
            MediaItem.id,
            MediaItem.title,
            MediaItem.year,
            Library.id,
            Library.name,
            Season.number,
            MediaFile.id,
            MediaFile.path,
            MediaFile.episode_numbers,
            has_language_track,
        )
        .join(Library, MediaItem.library_id == Library.id)
        .join(Season, Season.item_id == MediaItem.id)
        .join(MediaFile, MediaFile.season_id == Season.id)
        .filter(MediaItem.kind == "show")
    )
    if library_id is not None:
        q = q.filter(MediaItem.library_id == library_id)
    q = q.order_by(Library.name, MediaItem.title, Season.number, MediaFile.path)

    by_item: dict[int, ItemCoverage] = {}
    for (
        item_id, title, year, lib_id, lib_name, season_no,
        file_id, path, episode_numbers, has_language,
    ) in q.all():
        cov = by_item.get(item_id)
        if cov is None:
            cov = ItemCoverage(
                item_id=item_id,
                title=title,
                year=year,
                library_id=lib_id,
                library_name=lib_name,
                episodes_total=0,
                episodes_with=0,
                episodes_missing=0,
                complete=False,
            )
            by_item[item_id] = cov
        cov.episodes.append(
            EpisodeCoverage(
                file_id=file_id,
                path=path,
                season=season_no,
                episode_numbers=list(episode_numbers or []),
                has_language=bool(has_language),
            )
        )
        cov.episodes_total += 1
        if has_language:
            cov.episodes_with += 1
        else:
            cov.episodes_missing += 1

    results = list(by_item.values())
    for cov in results:
        cov.complete = cov.episodes_total > 0 and cov.episodes_missing == 0
    return results
