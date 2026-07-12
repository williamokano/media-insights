"""Alembic environment. The engine is initialised from the app's own config
loader so the URL is in one place (config.yaml)."""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from media_insights.config import load_config
from media_insights.db import init_engine
from media_insights.models import Base

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

cfg = load_config()
db_url = cfg.database.url or f"sqlite:///{cfg.config_dir}/media_insights.db"
init_engine(db_url)

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