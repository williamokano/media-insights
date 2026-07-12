"""Classify a MediaItem as anime / tv / movie.

Scored rules instead of a single switch — every verdict comes with a list of
human-readable reasons so users can audit why a title was tagged the way it
was. Library hint from config is a strong signal but never overrides a manual
override on the item.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from media_insights.matching.matcher import MatchResult
from media_insights.matching.parser import ParsedTitle
from media_insights.models import MediaFile, Track

LABELS = ("movie", "tv", "anime")

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


def _has_japanese_primary(audio_languages: Iterable[str]) -> bool:
    langs = [lang for lang in audio_languages if lang]
    return any(lang.lower() in {"ja", "jpn", "japanese"} for lang in langs)


def _has_non_japanese_subs(subtitle_languages: Iterable[str]) -> bool:
    langs = [lang for lang in subtitle_languages if lang]
    return any(lang.lower() not in {"ja", "jpn", "japanese"} for lang in langs)


def classify(
    match: MatchResult,
    files: list[MediaFile],
    tracks: list[Track],
    parsed: ParsedTitle | None = None,
    raw_name: str | None = None,
    manual_override: bool = False,
) -> Classification:
    """Return the best label + confidence + reasons for the title.

    `parsed`/`raw_name` come from a representative file of the title and feed
    the release-name signals (fansub groups, guessit's anime flag).
    """
    audio_languages: list[str] = [t.language for t in tracks if t.kind == "audio" and t.language]
    sub_languages: list[str] = [t.language for t in tracks if t.kind == "subtitle" and t.language]

    scores: dict[str, float] = {"movie": 0.0, "tv": 0.0, "anime": 0.0}
    reasons: dict[str, list[str]] = {label: [] for label in scores}

    if match.library_kind_hint == "movie":
        scores["movie"] += 0.7
        reasons["movie"].append("library kind hint = movie")
    elif match.library_kind_hint in ("tv", "anime"):
        scores["anime" if match.library_kind_hint == "anime" else "tv"] += 0.7
        reasons[match.library_kind_hint].append(f"library kind hint = {match.library_kind_hint}")

    if match.kind == "movie":
        scores["movie"] += 0.3
        reasons["movie"].append("parsed as movie (SxxExx absent)")
    elif match.kind == "show":
        scores["tv"] += 0.15
        reasons["tv"].append("parsed as show (SxxExx detected)")

    # External-id signal
    if match.anidb_id is not None:
        scores["anime"] += 0.6
        reasons["anime"].append("matched via anidb id")
    elif match.tvdb_id is not None and match.anidb_id is None and not match.imdb_id:
        scores["tv"] += 0.2
        reasons["tv"].append("matched via tvdb id")

    # Language-based signal (audio + subs together)
    if _has_japanese_primary(audio_languages):
        scores["anime"] += 0.4
        reasons["anime"].append("primary audio is Japanese")
        if _has_non_japanese_subs(sub_languages):
            scores["anime"] += 0.25
            reasons["anime"].append("non-Japanese subtitle tracks present")
    elif audio_languages:
        scores["tv"] += 0.1
        reasons["tv"].append("audio is non-Japanese (defaulting to live-action)")

    # Structure: single-file = movie bias, multi-file = tv/anime bias
    n_files = len(files)
    if n_files == 1 and match.kind != "show":
        scores["movie"] += 0.15
        reasons["movie"].append("exactly one file")
    elif n_files > 1:
        scores["tv"] += 0.05
        scores["anime"] += 0.05
        reasons["tv"].append(f"multiple files ({n_files})")
        reasons["anime"].append(f"multiple files ({n_files})")

    apply_parsed_signals(scores, reasons, parsed, raw_name)

    return _pick(scores, reasons, manual_override=manual_override)


def _pick(
    scores: dict[str, float], reasons: dict[str, list[str]], manual_override: bool
) -> Classification:
    label = max(scores, key=lambda k: scores[k])
    confidence = max(0.0, min(1.0, scores[label]))
    if not reasons[label]:
        reasons[label].append("default: insufficient evidence")
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
            scores["anime"] += 0.2
            reasons["anime"].append("guessit anime flag")
        release_group = parsed.release_group or ""
        if release_group in _ANIME_LIKELY_GROUPS:
            scores["anime"] += 0.35
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
            scores["anime"] += 0.35
            reasons["anime"].append(f"fansub release group: {group}")
        else:
            scores["anime"] += 0.15
            reasons["anime"].append("bracket-prefixed release name")
