"""Alembic environment (async). One linear history (ADR-001).

Models are imported so autogenerate sees the full metadata. Migrations run as
the owner role (bypassing RLS); the app connects as ``app_role``.
"""
from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# backend/ on sys.path so `app` and `helpers` import.
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(_HERE)
for _p in (_BACKEND, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app.billing.models import *  # noqa: F401,F403,E402
from app.core.config import settings  # noqa: E402
from app.core.models import Base as CoreBase  # noqa: E402
from app.core.models import *  # noqa: F401,F403,E402
from app.governance.models import *  # noqa: F401,F403,E402

# All models share one MetaData via app.core.models.Base subclasses.
target_metadata = CoreBase.metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Env var wins (12-factor); alembic.ini holds the dev default.
config.set_main_option("sqlalchemy.url", os.environ.get(
    "AZIENDA_DATABASE_URL",
    config.get_main_option("sqlalchemy.url") or settings.database_url))


def _sync_url(url: str) -> str:
    # asyncpg -> psycopg2 for the (sync) migration connection is unnecessary;
    # we run migrations on an async engine instead. Kept for offline SQL mode.
    return url


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata,
                      literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.", poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
