"""Alembic environment.

Two ways this runs:
  - CLI (`alembic upgrade head` from a shell): alembic.ini's sqlalchemy.url
    is empty, so we fall back to the app's own config loader, same as any
    other entry point.
  - Programmatically, from media_insights.db.run_migrations() at app
    startup: the caller already resolved the URL and set it directly on the
    Config object via set_main_option(), so we use that instead of
    re-reading config.yaml from disk a second time.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from media_insights.config import load_config
from media_insights.models import Base

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

def _resolve_db_url() -> str:
    preset = config.get_main_option("sqlalchemy.url")
    if preset:
        return preset
    cfg = load_config()
    return cfg.database.url or f"sqlite:///{cfg.config_dir}/media_insights.db"


db_url: str = _resolve_db_url()

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=db_url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    cfg_section = config.get_section(config.config_ini_section) or {}
    cfg_section["sqlalchemy.url"] = db_url
    connectable = engine_from_config(cfg_section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


# Run at import time so Alembic sees a live engine regardless of mode.
if context.is_offline_mode():
    pass  # offline mode doesn't need an engine
else:
    run_migrations_online()
