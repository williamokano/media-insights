"""Subtitle sidecar parsing.

Recognised dot-trailing tokens after the video stem:
  .<lang>.<flags>.<ext>
Where:
  lang:  'en', 'eng', 'pt-BR', 'jpn', ...
  flags: forced, sdh, cc, hi, default
  ext:   srt | ass | ssa | sub | idx | vtt | sup | smi

We avoid `babelfish.Language.fromguess` because its `guess` converter isn't
shipped in upstream packages; a small ISO-639 table is plenty for the cases
that show up in real libraries.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

FLAG_TOKENS = {"forced", "sdh", "cc", "hi", "default"}

# Minimal ISO-639 mapping covering the languages that turn up in actual
# sidecar filenames. Anything not in the table is returned verbatim so the
# caller can still classify the track by language later.
_ALPHA3_TO_ALPHA2 = {
    "eng": "en", "en": "en",
    "jpn": "ja", "ja": "ja", "jpn": "ja",
    "por": "pt", "pt": "pt",
    "fra": "fr", "fre": "fr", "fr": "fr",
    "deu": "de", "ger": "de", "de": "de",
    "spa": "es", "es": "es",
    "ita": "it", "it": "it",
    "rus": "ru", "ru": "ru",
    "zho": "zh", "chi": "zh", "chs": "zh", "cht": "zh", "zh": "zh",
    "kor": "ko", "ko": "ko",
    "ara": "ar", "ar": "ar",
    "nld": "nl", "dut": "nl", "nl": "nl",
    "pol": "pl", "pl": "pl",
    "swe": "sv", "swe": "sv",
    "nor": "no", "nob": "no", "nno": "no", "no": "no",
    "fin": "fi", "fi": "fi",
    "dan": "da", "da": "da",
    "tur": "tr", "tr": "tr",
    "hin": "hi", "hi": "hi",
    "tha": "th", "th": "th",
    "vie": "vi", "vi": "vi",
    "ind": "id", "id": "id",
    "msa": "ms", "may": "ms", "ms": "ms",
    "ces": "cs", "cze": "cs", "cs": "cs",
    "hun": "hu", "hu": "hu",
    "ron": "ro", "rum": "ro", "ro": "ro",
    "ukr": "uk", "uk": "uk",
    "heb": "he", "heb": "he", "he": "he",
    "ell": "el", "gre": "el", "el": "el",
    "tam": "ta", "ta": "ta",
    "tel": "te", "te": "te",
    "jpn": "ja", "jpn": "ja",
    "khm": "km", "khm": "km",
    "cat": "ca", "ca": "ca",
    "lav": "lv", "lv": "lv",
    "lit": "lt", "lt": "lt",
    "slk": "sk", "slo": "sk", "sk": "sk",
    "slv": "sl", "slv": "sl", "sl": "sl",
    "srp": "sr", "srp": "sr", "sr": "sr",
    "hrv": "hr", "hr": "hr",
    "bul": "bg", "bul": "bg", "bg": "bg",
}


@dataclass(slots=True)
class SidecarInfo:
    path: Path
    language: str | None
    is_forced: bool
    is_sdh: bool
    is_default: bool


def _normalise_language(token: str) -> str | None:
    """Convert language tokens like 'eng' or 'pt-BR' to short codes / locales."""
    if not token:
        return None
    canonical = token.replace("_", "-")
    lower = canonical.lower()
    if lower in _ALPHA3_TO_ALPHA2:
        return _ALPHA3_TO_ALPHA2[lower]
    # Region-tagged: 'pt-BR' / 'pt_br' / 'zh-CN' -> keep as-is so callers can
    # group on locale when they care.
    if "-" in canonical and len(canonical) <= 6:
        return canonical
    return canonical


def parse_sidecar(video_stem: str, sidecar: Path) -> SidecarInfo:
    """Extract language + flags from <videoname>.<lang>.<flags>.<ext>."""
    suffix = sidecar.suffix
    stem = sidecar.name[: -len(suffix)] if suffix else sidecar.name

    language: str | None = None
    is_forced = False
    is_sdh = False
    is_default = False

    rest = ""
    if stem == video_stem:
        rest = ""
    elif stem.startswith(video_stem + "."):
        rest = stem[len(video_stem) + 1:]

    if rest:
        tokens = rest.split(".")
        for token in tokens:
            token_lower = token.lower()
            if language is None:
                normalised = _normalise_language(token)
                if normalised:
                    language = normalised
                    continue
            if token_lower in FLAG_TOKENS:
                if token_lower == "forced":
                    is_forced = True
                elif token_lower == "default":
                    is_default = True
                else:
                    is_sdh = True
            else:
                # Unknown token; treat as language if not assigned yet.
                if language is None:
                    language = token

    return SidecarInfo(
        path=sidecar,
        language=language,
        is_forced=is_forced,
        is_sdh=is_sdh,
        is_default=is_default,
    )
