"""Language code normalization + display names, backed by babelfish (ISO-639).

Single source of truth for turning whatever a container tag or subtitle
sidecar filename spells out ("jpn", "en", "pt-BR", "fre", ...) into a
consistent code, while preserving the exact original token. Both
probe/normalize.py (ffprobe tags) and discovery/subtitles.py (sidecar
filenames) route through here so raw + normalized language stay consistent
regardless of source.

Uses babelfish.Language.fromietf() (handles alpha-2, alpha-3/T, and IETF
locale tags) plus fromalpha3b() as a fallback for legacy ISO-639-2
"bibliographic" codes (fre/ger/dut/cze/rum/may/gre/chi/...) that fromietf()
doesn't resolve, and fromname() as a final fallback for full English names
("Portuguese", "japanese") that neither of the above recognizes. Deliberately
does not use fromguess() -- it isn't a converter shipped in upstream
babelfish packages.
"""

from __future__ import annotations

from dataclasses import dataclass

from babelfish import Language
from babelfish.exceptions import Error as BabelfishError


@dataclass(slots=True)
class LanguageInfo:
    raw: str  # verbatim token, untouched (no case/format changes)
    normalized: str | None  # base ISO-639 code (alpha2 preferred, else alpha3); None if unrecognized


def _resolve(token: str) -> Language | None:
    candidate = token.replace("_", "-")
    try:
        return Language.fromietf(candidate)
    except ValueError:
        pass
    # fromalpha3b's failure mode is babelfish.exceptions.LanguageReverseError,
    # which subclasses AttributeError -- NOT ValueError -- so it needs its
    # own except clause, not a shared one with fromietf above.
    try:
        return Language.fromalpha3b(candidate.lower())
    except (ValueError, BabelfishError):
        pass
    # Last resort: full English names ("Portuguese", "japanese") that neither
    # IETF tags nor alpha-3b codes recognize.
    try:
        return Language.fromname(candidate)
    except (ValueError, BabelfishError, AttributeError):
        return None


def normalize_language(token: str | None) -> LanguageInfo | None:
    """Split a raw language token into (raw, normalized). None if token is empty/blank."""
    if not token or not token.strip():
        return None
    raw = token.strip()
    lang = _resolve(raw)
    normalized = _base_code(lang) if lang else None
    return LanguageInfo(raw=raw, normalized=normalized)


def _base_code(lang: Language) -> str | None:
    """alpha2 if the language has one, else alpha3.

    lang.alpha2 raises babelfish.exceptions.LanguageConvertError -- not just
    return a falsy value -- for languages with no ISO 639-1 code (e.g.
    Klingon, resolved only via fromname()). alpha3 is always present.
    """
    try:
        return lang.alpha2 or lang.alpha3
    except BabelfishError:
        return lang.alpha3


def display_name(normalized_code: str | None) -> str | None:
    """English display name for an already-normalized code, e.g. 'en' -> 'English'."""
    if not normalized_code:
        return None
    lang = _resolve(normalized_code)
    return lang.name if lang else None
