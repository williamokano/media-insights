"""Configuration: YAML on disk + environment overrides via pydantic-settings."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

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


class EnvSettings(BaseSettings):
    """Environment overrides. Anything with MI_ prefix maps onto AppConfig fields."""

    model_config = SettingsConfigDict(env_prefix="MI_", env_file=None, extra="ignore")

    config: str | None = None
    config_dir: str | None = None
    data_dir: str | None = None
    log_level: str | None = None


_PLACEHOLDER_RE = re.compile(r"\{config_dir\}|\{data_dir\}")


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


def load_config(path: str | os.PathLike[str] | None = None) -> AppConfig:
    """Load config: defaults, then YAML file, then env (MI_*)."""
    env = EnvSettings()
    config_path = Path(path or env.config or "/config/config.yaml")
    raw: dict[str, Any] = {}
    if config_path.is_file():
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    # Env overrides take precedence over YAML.
    if env.config_dir is not None:
        raw["config_dir"] = env.config_dir
    if env.data_dir is not None:
        raw["data_dir"] = env.data_dir
    if env.log_level is not None:
        raw["log_level"] = env.log_level

    # Now derive the placeholder source from the resolved values.
    config_dir = str(raw.get("config_dir") or "/config")
    data_dir = str(raw.get("data_dir") or "/data")
    raw = _expand_placeholders(raw, config_dir, data_dir)

    return AppConfig.model_validate(raw)
