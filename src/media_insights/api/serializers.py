"""DB -> JSON serializers for the API + Web UI."""

from __future__ import annotations

from typing import Any

from media_insights.config import AppConfig
from media_insights.models import Library, MediaFile, MediaItem, Track


def serialise_library(row: Library, cfg: AppConfig | None = None) -> dict[str, Any]:
    configured = True
    if cfg is not None:
        configured = any(lib.name == row.name for lib in cfg.libraries)
    return {
        "id": row.id,
        "name": row.name,
        "path": row.path,
        "kind": row.kind,
        "items": len(row.items),
        # False means this library was removed from config.yaml but its
        # indexed data was kept (soft delete) -- it's browsable but no
        # longer scanned or watched.
        "configured": configured,
    }


def serialise_item(row: MediaItem, *, include_files: bool = False) -> dict[str, Any]:
    data = {
        "id": row.id,
        "library_id": row.library_id,
        "kind": row.kind,
        "title": row.title,
        "year": row.year,
        "match_status": row.match_status,
        "ids": {
            "imdb": row.imdb_id,
            "tmdb": row.tmdb_id,
            "tvdb": row.tvdb_id,
            "anidb": row.anidb_id,
        },
        "classification": {
            "label": row.classification_label,
            "confidence": row.classification_confidence,
            "reasons": row.classification_reasons or [],
            "override": row.classification_override,
        },
        "seasons": [
            {
                "id": s.id,
                "number": s.number,
                "files": [
                    {
                        "id": f.id,
                        "path": f.path,
                        "container": f.container,
                        # Cheap: plain columns on MediaFile, not a relationship,
                        # so including them here costs no extra query -- unlike
                        # full per-track detail, which still needs include_files.
                        "video_codec": f.video_codec,
                        "video_width": f.video_width,
                        "video_height": f.video_height,
                        "audio_summary": f.audio_summary,
                        "subtitle_summary": f.subtitle_summary,
                    }
                    for f in s.files
                ],
            }
            for s in row.seasons
        ],
    }
    if include_files:
        data["files"] = [serialise_file(f, include_tracks=True) for s in row.seasons for f in s.files]
    return data


def serialise_file(row: MediaFile, *, include_tracks: bool = False) -> dict[str, Any]:
    data = {
        "id": row.id,
        "season_id": row.season_id,
        "path": row.path,
        "container": row.container,
        "size": row.size,
        "mtime": row.mtime,
        "duration": row.duration,
        "bit_rate": row.bit_rate,
        "video": {
            "codec": row.video_codec,
            "width": row.video_width,
            "height": row.video_height,
            "dynamic_range": row.video_dynamic_range,
        },
        "audio_summary": row.audio_summary,
        "subtitle_summary": row.subtitle_summary,
        "episode_numbers": row.episode_numbers or [],
        "episode_title": row.episode_title,
        "fingerprint": row.fingerprint,
        "fingerprint_strategy": row.fingerprint_strategy,
        "last_seen": row.last_seen.isoformat() if row.last_seen else None,
    }
    if include_tracks:
        data["tracks"] = [serialise_track(t) for t in row.tracks]
    return data


def serialise_track(row: Track) -> dict[str, Any]:
    return {
        "position": row.position,
        "kind": row.kind,
        "codec": row.codec,
        "language": row.language,
        "title": row.title,
        "channels": row.channels,
        "bit_rate": row.bit_rate,
        "is_default": row.is_default,
        "is_forced": row.is_forced,
        "is_sdh": row.is_sdh,
        "is_external": row.is_external,
        "sidecar_path": row.sidecar_path,
    }
