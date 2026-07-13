"""Subtitle sidecar parsing.

Recognised dot-trailing tokens after the video stem:
  .<lang>.<flags>.<ext>
Where:
  lang:  'en', 'eng', 'pt-BR', 'jpn', ...
  flags: forced, sdh, cc, hi, default
  ext:   srt | ass | ssa | sub | idx | vtt | sup | smi

Language tokens are normalized via media_insights.language, the same helper
probe/normalize.py uses for embedded ffprobe tags, so raw + normalized
language stay consistent regardless of source.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from media_insights.language import normalize_language

FLAG_TOKENS = {"forced", "sdh", "cc", "hi", "default"}


@dataclass(slots=True)
class SidecarInfo:
    path: Path
    language: str | None       # normalized code, e.g. "en"; None if unrecognized
    language_raw: str | None   # verbatim filename token, e.g. "eng", "pt-BR", "klingon"
    is_forced: bool
    is_sdh: bool
    is_default: bool


def parse_sidecar(video_stem: str, sidecar: Path) -> SidecarInfo:
    """Extract language + flags from <videoname>.<lang>.<flags>.<ext>."""
    suffix = sidecar.suffix
    stem = sidecar.name[: -len(suffix)] if suffix else sidecar.name

    language: str | None = None
    language_raw: str | None = None
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
            if language_raw is None:
                lang_info = normalize_language(token)
                if lang_info:
                    language = lang_info.normalized
                    language_raw = lang_info.raw
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
                if language_raw is None:
                    language_raw = token

    return SidecarInfo(
        path=sidecar,
        language=language,
        language_raw=language_raw,
        is_forced=is_forced,
        is_sdh=is_sdh,
        is_default=is_default,
    )
