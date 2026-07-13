"""Provider interface for online metadata lookups.

A provider answers one question the files themselves often cannot: *what
actually is this title?* Filename and audio evidence go only so far -- an
English-dubbed anime with no Japanese audio track and no fansub tag is
indistinguishable from a western cartoon by local evidence alone. That is
exactly the gap that leaves misfiled titles undetectable, so providers exist
to close it.

Each provider decides `is_anime` itself, because that judgement depends on
the provider's own semantics (AniList indexes only anime; TMDB needs
`Animation` AND a Japanese origin country; TVDB has a literal `Anime` genre).
The classifier just weighs the answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(slots=True)
class ProviderSignals:
    """What a provider knows about a title. Every field is best-effort."""

    source: str  # anilist | tmdb | tvdb
    title: str | None = None
    year: int | None = None
    kind: str | None = None  # movie | show

    # None = "this provider has no opinion", which is meaningfully different
    # from False ("this provider says it is NOT anime" -- e.g. TMDB seeing an
    # Animation genre with a US origin country, i.e. a western cartoon).
    is_anime: bool | None = None

    origin_country: str | None = None
    genres: list[str] = field(default_factory=list)

    imdb_id: str | None = None
    tmdb_id: int | None = None
    tvdb_id: int | None = None
    anilist_id: int | None = None


class Provider(Protocol):
    name: str

    def lookup(self, title: str, year: int | None, kind: str | None) -> ProviderSignals | None:
        """Best candidate for this title, or None when nothing matches."""

    def check(self) -> str | None:
        """Verify credentials/reachability. Returns an error message, or None if OK.

        Providers fail soft by design -- a bad API key just means "no metadata"
        and a log line, which is safe but silent. This exists so a
        misconfigured key can be *seen* rather than quietly doing nothing.
        """
