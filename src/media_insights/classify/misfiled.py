"""Shared source of truth for what counts as "misfiled".

Library kinds and classification labels used to share one flat vocabulary
(movie/tv/anime), so a straight inequality was the whole test. `anime_movie`
breaks that: it's simultaneously anime and movie-format, so it's correctly
filed in *either* a Movies or an Anime library -- not misfiled in either one.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement, and_, or_

from media_insights.models import Library, MediaItem

# Which classification labels a library of a given kind accepts without being
# "misfiled". `auto` is deliberately absent: it asserts nothing about what
# belongs there, so nothing in it can ever be misfiled.
ACCEPTED_LABELS: dict[str, frozenset[str]] = {
    "movie": frozenset({"movie", "anime_movie"}),
    "tv": frozenset({"tv"}),
    "anime": frozenset({"anime", "anime_movie"}),
}


def is_misfiled(library_kind: str, label: str | None) -> bool:
    """True if `label` disagrees with what `library_kind` accepts.

    A `None` label (classification hasn't run yet) is never misfiled --
    there's nothing yet to disagree with.
    """
    if label is None:
        return False
    accepted = ACCEPTED_LABELS.get(library_kind)
    if accepted is None:  # auto, or any unrecognized library kind
        return False
    return label not in accepted


def misfiled_condition() -> ColumnElement[bool]:
    """The SQL equivalent of is_misfiled(), for use in `.filter(...)`.

    Callers must already have `MediaItem` joined to `Library` in the query.
    """
    per_kind = [
        and_(Library.kind == kind, MediaItem.classification_label.notin_(accepted))
        for kind, accepted in ACCEPTED_LABELS.items()
    ]
    return and_(
        MediaItem.classification_label.isnot(None),
        or_(*per_kind),
    )
