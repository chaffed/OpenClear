# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from openclear.config import get_settings
from openclear.db.base import Base

# openclear.domain's __init__ imports every model module, fully populating Base.metadata
# for autogenerate — see its docstring for why this must be centralized in one place.
import openclear.domain  # noqa: F401

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers=False: fileConfig()'s own default (True) silently sets
    # .disabled = True on every Python logger that already exists at this point and
    # isn't explicitly named in alembic.ini's [loggers] section -- e.g. any
    # openclear.* module logger created by an import that happened to run before this
    # migration did (confirmed directly: openclear.ocr.factory's logger goes from
    # enabled to permanently disabled for the rest of the process). That's a
    # standard, well-documented fileConfig() gotcha, not something alembic.ini's own
    # [loggers] section is meant to enumerate every application logger to avoid.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
