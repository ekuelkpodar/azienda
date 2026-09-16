"""Async SQLAlchemy engine + session factory + tenant-scoped sessions.

Tenancy (DATABASE.md §1, ADR-008):
- Primary gate: every query filters ``tenant_id`` (application-enforced via
  ``TenantSession`` helpers and service-layer discipline).
- Defense in depth: Postgres RLS. Each session sets ``app.tenant_id`` (and
  ``app.user_id``); migrations create the RLS policies. On SQLite (tests) the
  ``SET`` is skipped — the app-level gate is what tests assert.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from types import TracebackType
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings
from app.core.models import Base

_tenant_ctx: ContextVar[dict[str, Any] | None] = ContextVar("azienda_tenant", default=None)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def set_request_tenant(tenant_id: str | None, user_id: str | None = None) -> None:
    _tenant_ctx.set({"tenant_id": tenant_id, "user_id": user_id})


def get_request_tenant() -> dict[str, Any] | None:
    return _tenant_ctx.get()


def init_engine(settings: Settings) -> AsyncEngine:
    """Create (or recreate) the global engine. Tests call this with a SQLite URL."""
    global _engine, _session_factory
    url = settings.database_url
    kwargs: dict[str, Any] = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs = {}
    _engine = create_async_engine(url, **kwargs)
    _session_factory = async_sessionmaker(_engine, class_=AsyncSession,
                                          expire_on_commit=False)

    if _engine.dialect.name == "postgresql":
        @event.listens_for(_engine.sync_engine, "connect")
        def _set_rls_vars(dbapi_conn: Any, _conn_record: Any) -> None:
            # RLS variables are set per-transaction in TenantSession; this is a
            # safety default so a leaked connection never sees another tenant.
            cursor = dbapi_conn.cursor()
            try:
                cursor.execute("SET app.tenant_id = '00000000-0000-0000-0000-000000000000'")
            finally:
                cursor.close()

    return _engine


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("Engine not initialized — call init_engine() first")
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("Engine not initialized — call init_engine() first")
    return _session_factory


async def apply_tenant_rls(session: AsyncSession, tenant_id: str,
                           user_id: str | None = None) -> None:
    """Set Postgres session-local RLS variables. No-op on other dialects."""
    bind = session.bind
    dialect = bind.dialect.name if bind is not None else ""
    if dialect != "postgresql":
        return
    # SET LOCAL is transaction-scoped: safe for pooled connections.
    await session.execute(text("SET LOCAL app.tenant_id = :tid"), {"tid": tenant_id})
    await session.execute(text("SET LOCAL app.user_id = :uid"),
                          {"uid": user_id or "00000000-0000-0000-0000-000000000000"})


class TenantSession:
    """Async context manager yielding a tenant-scoped session.

    Usage: ``async with TenantSession(tenant_id, user_id) as db: ...``
    Commits on clean exit, rolls back on error.
    """

    def __init__(self, tenant_id: str, user_id: str | None = None) -> None:
        self.tenant_id = tenant_id
        self.user_id = user_id
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> AsyncSession:
        self._session = get_session_factory()()
        await apply_tenant_rls(self._session, self.tenant_id, self.user_id)
        return self._session

    async def __aexit__(self, exc_type: type[BaseException] | None,
                        exc: BaseException | None,
                        tb: TracebackType | None) -> None:
        assert self._session is not None
        try:
            if exc_type is None:
                await self._session.commit()
            else:
                await self._session.rollback()
        finally:
            await self._session.close()


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Bare session for non-tenant work (migrations, CLI bootstrap)."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def create_all_tables() -> None:
    """Create tables from metadata. Used by tests and dev bootstrap (NOT prod)."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
