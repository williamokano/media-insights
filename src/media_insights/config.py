"""Configuration: YAML on disk + MI_* environment overrides.

Env override syntax mirrors the YAML structure with `__` as the nesting
separator: `MI_LOG_LEVEL=DEBUG`, `MI_DATABASE__URL=postgresql://...`,
`MI_WATCHER__OBSERVER=polling`. List-valued fields (libraries, webhooks,
exec_hooks) can only be set in the YAML file.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

# Library-kind hint seed for the matcher and classifier.
LibraryKind = Literal["movie", "tv", "anime", "auto"]
FingerprintStrategy = Literal["mtime", "partial", "full"]
ObserverKind = Literal["auto", "inotify", "polling"]


class LibraryConfig(BaseModel):
    name: str
    path: str
    kind: LibraryKind = "auto"


class FingerprintConfig(BaseModel):
    strategy: FingerprintStrategy = "partial"
    chunk_bytes: int = 8 * 1024 * 1024


class WatcherConfig(BaseModel):
    enabled: bool = True
    recursive: bool = True
    observer: ObserverKind = "auto"
    debounce_seconds: float = 5.0


class ScheduleConfig(BaseModel):
    enabled: bool = True
    cron: str = "0 */6 * * *"


class WebhookConfig(BaseModel):
    name: str = "default"
    url: str = ""
    secret: str = ""
    timeout_seconds: float = 10.0
    max_attempts: int = 8


class ExecHookConfig(BaseModel):
    name: str = "log-changes"
    command: str = ""
    timeout_seconds: float = 30.0


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8765


class FfmpegConfig(BaseModel):
    ffprobe: str = ""
    mediainfo_cli: str = ""


class DatabaseConfig(BaseModel):
    # Default is filled in by init_db() when empty.
    url: str = ""


class AniListConfig(BaseModel):
    # No API key: AniList's public GraphQL endpoint is open. It's also the
    # single best anime/not-anime oracle, so it's on by default whenever
    # providers are enabled at all.
    enabled: bool = True


class TmdbConfig(BaseModel):
    enabled: bool = False
    # Also settable as MI_PROVIDERS__TMDB__API_KEY so keys can stay out of
    # config.yaml entirely.
    api_key: str = ""


class TvdbConfig(BaseModel):
    enabled: bool = False
    api_key: str = ""
    pin: str = ""  # only needed for user-subscription keys


class ProvidersConfig(BaseModel):
    """Online metadata lookups. Off by default: this tool works fully offline,
    and enabling it is the user's explicit choice to make network calls."""

    enabled: bool = False
    timeout_seconds: float = 10.0
    # A title's provider data is re-fetched only after this long. Metadata
    # barely changes, and AniList allows just 30 requests/minute.
    cache_ttl_days: int = 30
    anilist: AniListConfig = Field(default_factory=AniListConfig)
    tmdb: TmdbConfig = Field(default_factory=TmdbConfig)
    tvdb: TvdbConfig = Field(default_factory=TvdbConfig)


class AppConfig(BaseModel):
    config_dir: str = "/config"
    data_dir: str = "/data"
    log_level: str = "INFO"
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    fingerprint: FingerprintConfig = Field(default_factory=FingerprintConfig)
    watcher: WatcherConfig = Field(default_factory=WatcherConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    libraries: list[LibraryConfig] = Field(default_factory=list)
    webhooks: list[WebhookConfig] = Field(default_factory=list)
    exec_hooks: list[ExecHookConfig] = Field(default_factory=list)
    server: ServerConfig = Field(default_factory=ServerConfig)
    ffmpeg: FfmpegConfig = Field(default_factory=FfmpegConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)


_ENV_PREFIX = "MI_"
# Env keys that aren't AppConfig fields.
_ENV_SPECIAL = {"MI_CONFIG"}
# Only allow overrides onto known top-level sections/fields, so unrelated
# MI_-prefixed variables (e.g. from other tools) can't corrupt the config.
_ENV_ALLOWED_ROOTS = frozenset(AppConfig.model_fields) - {"libraries", "webhooks", "exec_hooks"}

_PLACEHOLDER_RE = re.compile(r"\{config_dir\}|\{data_dir\}")


def _env_overrides() -> dict[str, Any]:
    """Collect MI_* env vars into a nested dict: MI_DATABASE__URL -> database.url."""
    out: dict[str, Any] = {}
    for key, value in os.environ.items():
        if not key.startswith(_ENV_PREFIX) or key in _ENV_SPECIAL:
            continue
        parts = key[len(_ENV_PREFIX):].lower().split("__")
        if parts[0] not in _ENV_ALLOWED_ROOTS:
            continue
        node = out
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return out


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def _expand_placeholders(raw: dict[str, Any], config_dir: str, data_dir: str) -> dict[str, Any]:
    """Resolve {config_dir}/{data_dir} placeholders inside string leaves."""
    def walk(node: Any) -> Any:
        if isinstance(node, str):
            return _PLACEHOLDER_RE.sub(
                lambda m: config_dir if m.group(0) == "{config_dir}" else data_dir, node
            )
        if isinstance(node, list):
            return [walk(x) for x in node]
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        return node

    return walk(raw)


def resolve_config_path(path: str | os.PathLike[str] | None = None) -> Path:
    """Resolve the config.yaml path: explicit arg > MI_CONFIG env > default.

    Shared with config_store so the file that gets edited from the API/UI is
    always the same one load_config() would read.
    """
    return Path(path or os.environ.get("MI_CONFIG") or "/config/config.yaml")


def load_config(path: str | os.PathLike[str] | None = None) -> AppConfig:
    """Load config: defaults, then YAML file, then env (MI_*)."""
    config_path = resolve_config_path(path)
    raw: dict[str, Any] = {}
    if config_path.is_file():
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    # Env overrides take precedence over YAML.
    _deep_merge(raw, _env_overrides())

    # Now derive the placeholder source from the resolved values.
    config_dir = str(raw.get("config_dir") or "/config")
    data_dir = str(raw.get("data_dir") or "/data")
    raw = _expand_placeholders(raw, config_dir, data_dir)

    return AppConfig.model_validate(raw)
