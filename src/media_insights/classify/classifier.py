"""Classify a MediaItem as anime / tv / movie.

Scored rules instead of a single switch -- every verdict comes with a list of
human-readable reasons so users can audit why a title was tagged the way it
was. A manual override on the item always wins.

The library's `kind` is deliberately only a *tiebreaker* (_HINT_WEIGHT below).
It used to dominate every other signal, which made a misfiled title
structurally impossible to detect: an anime sitting in a `kind: tv` library
scored tv=0.90 vs anime=0.70 no matter how obviously anime the file itself
was. Since folders get mixed up in exactly the situation this classifier is
meant to help with (drive migrations, bulk moves), evidence from the file and
from metadata providers must always be able to outvote the folder it happens
to live in. The hint now only decides titles where there is no evidence at
all.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from media_insights.matching.matcher import MatchResult
from media_insights.matching.parser import ParsedTitle
from media_insights.matching.providers.base import ProviderSignals
from media_insights.models import MediaFile, Track

LABELS = ("movie", "tv", "anime", "anime_movie")

# The library's kind is a tiebreaker, not evidence. Kept strictly below the
# weakest real signal (_PARSED_SHOW, 0.15 -- and real signals stack) so that
# any actual evidence outvotes the folder a title happens to sit in. See the
# module docstring for why this matters.
_HINT_WEIGHT = 0.15

# Evidence from an online metadata provider. Deliberately the strongest
# signals available: a provider knows what a title *is*, which local file
# evidence often cannot establish at all (an English-dubbed anime with no
# Japanese audio and no fansub tag looks exactly like a western cartoon).
_PROVIDER_ANIME = 0.7
_PROVIDER_NOT_ANIME = 0.4  # e.g. TMDB: animated, but not Japanese -> a western cartoon
_PROVIDER_KIND = 0.6  # provider says movie vs series

# Evidence from external IDs.
_ANIDB_ID = 0.6
_TVDB_ID = 0.2

# Evidence from the file itself.
_JAPANESE_AUDIO = 0.45
_NON_JAPANESE_SUBS = 0.25  # only added on top of Japanese audio
_NON_JAPANESE_AUDIO = 0.1  # weak live-action lean

# Evidence from the release name.
_FANSUB_GROUP = 0.4
_GUESSIT_ANIME_FLAG = 0.2
_BRACKET_PREFIX = 0.15

# Structural signals.
_PARSED_MOVIE = 0.3
_PARSED_SHOW = 0.15
_SINGLE_FILE = 0.15
_MULTI_FILE = 0.05

# Fansub-style tags commonly seen in anime releases.
_ANIME_GROUP_RE = re.compile(r"^\[(?P<group>[^\]]+)\]")
_ANIME_LIKELY_GROUPS = {
    "SubsPlease", "Erai-raws", "HorribleSubs", "Underwater",
    "Anime Time", "EMBER", "Judas", "Sokrates", "ASW", "Commie",
    "GJM", "Kaleido", "LostYears", "Nii-sama", "Ohys-Raws",
    "Pas",
}


@dataclass(slots=True)
class Classification:
    label: str
    confidence: float
    reasons: list[str]


_JAPANESE = {"ja", "jpn", "japanese"}


def _is_japanese(lang: str | None) -> bool:
    return bool(lang) and lang.lower() in _JAPANESE  # type: ignore[union-attr]


def primary_audio_languages(tracks: Iterable[Track]) -> list[str]:
    """The *primary* audio language of each file -- not every language present.

    This distinction is load-bearing. A western show with a Japanese dub track
    (e.g. Amazon's `Secret Level`: English default plus German/Spanish/Japanese
    dubs) is not anime, but a naive "any track is Japanese" test says it is --
    which is exactly what this used to do despite being named `_primary`. It
    produced a real false positive against a real library.

    Primary = the track the player would actually pick: the default-flagged
    audio track, or failing that the first one by stream position.
    """
    by_file: dict[object, list[Track]] = {}
    for track in tracks:
        if track.kind != "audio" or not track.language:
            continue
        by_file.setdefault(getattr(track, "file_id", None), []).append(track)

    primaries: list[str] = []
    for audio in by_file.values():
        defaults = [t for t in audio if getattr(t, "is_default", False)]
        chosen = defaults[0] if defaults else min(audio, key=lambda t: getattr(t, "position", 0))
        if chosen.language:
            primaries.append(chosen.language)
    return primaries


def _has_japanese_primary(primary_languages: Iterable[str]) -> bool:
    return any(_is_japanese(lang) for lang in primary_languages)


def _has_non_japanese_subs(subtitle_languages: Iterable[str]) -> bool:
    langs = [lang for lang in subtitle_languages if lang]
    return any(not _is_japanese(lang) for lang in langs)


def _apply_provider_signals(
    scores: dict[str, float],
    reasons: dict[str, list[str]],
    provider: ProviderSignals | None,
) -> None:
    """Weigh what an online metadata provider said, if one was consulted.

    This is what makes an English-dubbed anime -- no Japanese audio, no fansub
    tag, nothing locally to go on -- detectable at all. It's also what stops a
    western cartoon being called anime: `is_anime=False` is a real answer, not
    the absence of one.
    """
    if provider is None:
        return

    origin = f" ({provider.origin_country})" if provider.origin_country else ""

    if provider.is_anime is True:
        scores["anime"] += _PROVIDER_ANIME
        reasons["anime"].append(f"{provider.source} identifies this as anime{origin}")
    elif provider.is_anime is False:
        # Not anime, per a provider that would know. Push toward live-action /
        # western-animation instead of merely withholding the anime bonus.
        scores["tv"] += _PROVIDER_NOT_ANIME
        scores["movie"] += _PROVIDER_NOT_ANIME
        reasons["tv"].append(f"{provider.source} says this is not anime{origin}")
        reasons["movie"].append(f"{provider.source} says this is not anime{origin}")

    if provider.kind == "movie":
        scores["movie"] += _PROVIDER_KIND
        reasons["movie"].append(f"{provider.source} lists this as a film")
    elif provider.kind == "show":
        # A series: tv and anime are both series, so this can't discriminate
        # between them -- it only rules out `movie`.
        scores["tv"] += _PROVIDER_KIND
        scores["anime"] += _PROVIDER_KIND
        reasons["tv"].append(f"{provider.source} lists this as a series")
        reasons["anime"].append(f"{provider.source} lists this as a series")


def _leans_anime(scores: dict[str, float]) -> bool:
    """Does the evidence favor anime over live-action -- and actually exist?

    Deliberately orthogonal to which label currently has the top score: a
    single-file title can be a `movie`-format winner while still carrying
    strong anime evidence (Japanese audio, a fansub tag). That combination is
    what makes it an anime movie, not a plain movie mislabeled as anime.
    """
    return scores["anime"] > scores["tv"] and scores["anime"] > 0


def _movie_format(match: MatchResult, provider: ProviderSignals | None) -> bool:
    """Is this a movie by structure, independent of anime-ness?

    A provider's opinion on kind is stronger evidence than file structure (see
    _apply_provider_signals), so it's consulted first when available.
    """
    if provider is not None and provider.kind == "movie":
        return True
    if provider is not None and provider.kind == "show":
        return False
    return match.kind == "movie"


def classify(
    match: MatchResult,
    files: list[MediaFile],
    tracks: list[Track],
    parsed: ParsedTitle | None = None,
    raw_name: str | None = None,
    manual_override: bool = False,
    provider: ProviderSignals | None = None,
) -> Classification:
    """Return the best label + confidence + reasons for the title.

    `parsed`/`raw_name` come from a representative file of the title and feed
    the release-name signals (fansub groups, guessit's anime flag).
    `provider` is what an online metadata source said, when one is enabled --
    the strongest signal available, since it can identify titles whose files
    carry no usable evidence at all.
    """
    # Only the *primary* audio of each file counts as evidence -- a Japanese
    # dub buried among five other dubs says nothing about a show's origin.
    audio_languages: list[str] = primary_audio_languages(tracks)
    sub_languages: list[str] = [t.language for t in tracks if t.kind == "subtitle" and t.language]

    scores: dict[str, float] = {"movie": 0.0, "tv": 0.0, "anime": 0.0}
    reasons: dict[str, list[str]] = {label: [] for label in scores}

    hint = match.library_kind_hint
    hint_label = {"movie": "movie", "tv": "tv", "anime": "anime"}.get(hint)

    _apply_provider_signals(scores, reasons, provider)

    if match.kind == "movie":
        scores["movie"] += _PARSED_MOVIE
        reasons["movie"].append("parsed as movie (SxxExx absent)")
    elif match.kind == "show":
        scores["tv"] += _PARSED_SHOW
        reasons["tv"].append("parsed as show (SxxExx detected)")

    # External-id signal
    if match.anidb_id is not None:
        scores["anime"] += _ANIDB_ID
        reasons["anime"].append("matched via anidb id")
    elif match.tvdb_id is not None and match.anidb_id is None and not match.imdb_id:
        scores["tv"] += _TVDB_ID
        reasons["tv"].append("matched via tvdb id")

    # Language-based signal (audio + subs together)
    if _has_japanese_primary(audio_languages):
        scores["anime"] += _JAPANESE_AUDIO
        reasons["anime"].append("primary audio is Japanese")
        if _has_non_japanese_subs(sub_languages):
            scores["anime"] += _NON_JAPANESE_SUBS
            reasons["anime"].append("non-Japanese subtitle tracks present")
    elif audio_languages:
        scores["tv"] += _NON_JAPANESE_AUDIO
        reasons["tv"].append("audio is non-Japanese (defaulting to live-action)")

    # Structure: single-file = movie bias, multi-file = tv/anime bias
    n_files = len(files)
    if n_files == 1 and match.kind != "show":
        scores["movie"] += _SINGLE_FILE
        reasons["movie"].append("exactly one file")
    elif n_files > 1:
        scores["tv"] += _MULTI_FILE
        scores["anime"] += _MULTI_FILE
        reasons["tv"].append(f"multiple files ({n_files})")
        reasons["anime"].append(f"multiple files ({n_files})")

    apply_parsed_signals(scores, reasons, parsed, raw_name)

    # The folder hint goes in last and small, purely to break ties among
    # titles with no evidence of their own -- never to outvote evidence.
    evidence_before_hint = dict(scores)
    if hint_label is not None:
        scores[hint_label] += _HINT_WEIGHT
        reasons[hint_label].append(f"library kind hint = {hint} (tiebreaker only)")

    return _pick(
        scores, reasons, evidence_before_hint, hint_label, manual_override=manual_override,
        match=match, provider=provider,
    )


def _pick(
    scores: dict[str, float],
    reasons: dict[str, list[str]],
    evidence_before_hint: dict[str, float],
    hint_label: str | None,
    manual_override: bool,
    match: MatchResult,
    provider: ProviderSignals | None,
) -> Classification:
    label = max(scores, key=lambda k: scores[k])

    total = sum(scores.values())
    # A ratio against the alternatives, not a raw score: "0.62 anime" should
    # mean "anime beat the others by this much", which the old clipped raw
    # sum did not convey at all.
    confidence = (scores[label] / total) if total > 0 else 0.0

    if not reasons[label]:
        reasons[label].append("default: insufficient evidence")

    # Make an overruled folder auditable: if the hint pointed elsewhere and
    # the file's own evidence won anyway, say so on the verdict itself.
    if (
        hint_label is not None
        and hint_label != label
        and evidence_before_hint[label] > evidence_before_hint[hint_label]
    ):
        reasons[label].append(
            f"overrode library kind hint = {hint_label} (evidence for {label} was stronger)"
        )

    # anime and movie are orthogonal (style vs. format), so a title can be
    # both -- an anime movie, correctly filed in a Movies library, is neither
    # "misfiled anime" nor "misfiled movie". Reconciled last, after the
    # library-hint audit note above, so that note (if either facet earned
    # one) survives into the merged reasons below.
    if _leans_anime(scores) and _movie_format(match, provider):
        merged_reasons = list(dict.fromkeys(reasons["anime"] + reasons["movie"]))
        anime_movie_total = scores["anime"] + scores["movie"]
        return Classification(
            label="anime_movie",
            confidence=(anime_movie_total / total) if total > 0 else 0.0,
            reasons=merged_reasons,
        )

    return Classification(label=label, confidence=confidence, reasons=reasons[label])


def apply_parsed_signals(
    scores: dict[str, float],
    reasons: dict[str, list[str]],
    parsed: ParsedTitle | None,
    raw_name: str | None = None,
) -> None:
    """Bonus signals from the release name of a representative file."""
    if parsed is not None:
        if parsed.anime:
            scores["anime"] += _GUESSIT_ANIME_FLAG
            reasons["anime"].append("guessit anime flag")
        release_group = parsed.release_group or ""
        if release_group in _ANIME_LIKELY_GROUPS:
            scores["anime"] += _FANSUB_GROUP
            reasons["anime"].append(f"fansub release group: {release_group}")
            return
    # guessit often mistakes the trailing CRC tag for the release group, so
    # the leading [Group] bracket is read off the raw filename instead.
    if raw_name:
        bracket = _ANIME_GROUP_RE.match(raw_name)
        if bracket is None:
            return
        group = bracket.group("group")
        if group in _ANIME_LIKELY_GROUPS:
            scores["anime"] += _FANSUB_GROUP
            reasons["anime"].append(f"fansub release group: {group}")
        else:
            scores["anime"] += _BRACKET_PREFIX
            reasons["anime"].append("bracket-prefixed release name")
