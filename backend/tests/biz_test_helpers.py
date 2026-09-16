"""Shared fixtures for the crm/tasks/workflows test modules.

This module is NOT conftest.py (which is owned by the foundation builder and
must not be edited). It builds on conftest's DB fixtures (`db_session`,
`tenant`, `tenant_b`) and its programmable FakePolicyEngine, wiring the
crm/tasks/workflows service container and an HTTP app that mounts ONLY the
three owned routers.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.routers import crm as crm_router
from app.api.routers import tasks as tasks_router
from app.api.routers import workflows as workflows_router
from app.api.routers._common import (
    InMemoryApprovalStore,
    RecordingEventBus,
    Services,
    StubAgentDelegate,
    build_services,
)
from app.core.contracts import TenantContext
from app.core.errors import install_error_handlers
from tests.conftest import FakePolicyEngine


@pytest.fixture
def biz_policy() -> FakePolicyEngine:
    return FakePolicyEngine()


@pytest.fixture
def biz_bus() -> RecordingEventBus:
    return RecordingEventBus()


@pytest.fixture
def biz_approvals() -> InMemoryApprovalStore:
    return InMemoryApprovalStore()


@pytest.fixture
def biz_services(db_session: AsyncSession, tenant: TenantContext,
                 biz_policy: FakePolicyEngine, biz_bus: RecordingEventBus,
                 biz_approvals: InMemoryApprovalStore) -> Services:
    """Service container whose sessions join conftest's rolled-back transaction,
    so every test is isolated without touching conftest.py."""
    sf = async_sessionmaker(bind=db_session.bind, class_=AsyncSession,
                            expire_on_commit=False)
    return build_services(session_factory=sf, bus=biz_bus, policy=biz_policy,
                          approvals=biz_approvals,
                          agent_delegate=StubAgentDelegate())


@pytest_asyncio.fixture
async def biz_client(biz_services: Services):
    """HTTP client mounting only the crm/tasks/workflows routers."""
    app = FastAPI(title="biz-test")
    install_error_handlers(app)
    app.include_router(crm_router.router, prefix="/api/v1")
    app.include_router(tasks_router.router, prefix="/api/v1")
    app.include_router(workflows_router.router, prefix="/api/v1")
    app.state.services = biz_services
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as client:
        yield client


def tenant_headers(tenant: TenantContext) -> dict[str, str]:
    return {"X-Tenant-Id": tenant.tenant_id,
            "X-User-Id": tenant.user_id or "test-user"}
