"""Shared fixtures for the backend test suite.

OWNERSHIP: backend/tests/conftest.py is owned by the foundation / governance /
billing builder. The ACP/agent section below was authored by the ACP builder
(2026-09-15) and is preserved here; the foundation section (DB-backed app
fixtures) was added by the owner. Nothing here is presented as the real
governance rail where fakes are used — fakes are honest stand-ins.

Fake governance (stand-ins until the governance package lands) — FakeAuditLedger
implements the REAL hash-chain pattern (sha256(prev_hash || canonical(payload)));
FakePolicyEngine is programmable per test (default ALLOW).
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps as api_deps
from app.api.routers import admin, approvals, audit, auth, billing, budgets, tenants
from app.core import contracts
from app.core.config import Settings
from app.core.db import get_engine, init_engine
from app.core.errors import install_error_handlers
from app.core.models import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      default=str, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Fake governance (stand-ins until the governance package lands)
# ---------------------------------------------------------------------------
class FakePolicyEngine:
    """Programmable policy: default ALLOW; tests add deny/require_approval."""

    def __init__(self) -> None:
        self.deny_actions: set[str] = set()
        self.require_approval_actions: set[str] = set()
        self.evaluations: list[contracts.ActionRequest] = []
        self.decisions: list[dict[str, Any]] = []

    async def evaluate(self, request: contracts.ActionRequest) -> contracts.PolicyDecision:
        self.evaluations.append(request)
        if request.action in self.deny_actions:
            d = contracts.PolicyDecision(
                effect=contracts.PolicyEffect.DENY, policy_id="fake-deny",
                reasons=(f"fake policy denies {request.action}",))
        elif request.action in self.require_approval_actions:
            d = contracts.PolicyDecision(
                effect=contracts.PolicyEffect.REQUIRE_APPROVAL,
                policy_id="fake-approval",
                reasons=(f"fake policy requires approval for {request.action}",))
        else:
            d = contracts.PolicyDecision(effect=contracts.PolicyEffect.ALLOW,
                                         policy_id="fake-allow", reasons=())
        self.decisions.append({"action": request.action,
                               "effect": d.effect.value,
                               "reasons": list(d.reasons),
                               "at": _utcnow().isoformat()})
        return d


class FakeApprovalStore:
    def __init__(self) -> None:
        self._approvals: dict[str, contracts.Approval] = {}

    async def request(self, decision: contracts.PolicyDecision,
                      request: contracts.ActionRequest,
                      risk_score: float = 0.0,
                      risk_factors: tuple[str, ...] = ()) -> contracts.Approval:
        ap = contracts.Approval(
            approval_id=f"appr-{uuid.uuid4().hex[:12]}",
            tenant_id=request.tenant.tenant_id, action=request,
            status=contracts.ApprovalStatus.PENDING,
            requested_by="orchestrator", decided_by=None,
            expires_at=_utcnow() + timedelta(hours=24))
        self._approvals[ap.approval_id] = ap
        return ap

    async def decide(self, tenant: contracts.TenantContext, approval_id: str,
                     approved: bool, note: str = "") -> contracts.Approval:
        ap = self._approvals[approval_id]
        updated = contracts.Approval(
            approval_id=ap.approval_id, tenant_id=ap.tenant_id, action=ap.action,
            status=(contracts.ApprovalStatus.APPROVED if approved
                    else contracts.ApprovalStatus.DENIED),
            requested_by=ap.requested_by, decided_by=tenant.user_id or "tester",
            expires_at=ap.expires_at)
        self._approvals[approval_id] = updated
        return updated

    async def get(self, tenant: contracts.TenantContext,
                  approval_id: str) -> contracts.Approval | None:
        return self._approvals.get(approval_id)

    async def list_pending(self, tenant: contracts.TenantContext, limit: int = 50,
                           offset: int = 0) -> tuple[list[contracts.Approval], int]:
        items = [a for a in self._approvals.values()
                 if a.status == contracts.ApprovalStatus.PENDING]
        return items[offset:offset + limit], len(items)

    async def escalate(self, tenant: contracts.TenantContext, approval_id: str,
                       note: str = "") -> contracts.Approval:
        ap = self._approvals[approval_id]
        # Escalation never extends expiry (fail closed); records the event.
        return ap

    async def sweep_expired(self, tenant: contracts.TenantContext | None = None,
                            ) -> int:
        now = _utcnow()
        count = 0
        for ap in self._approvals.values():
            if ap.status == contracts.ApprovalStatus.PENDING and ap.expires_at <= now:
                self._approvals[ap.approval_id] = contracts.Approval(
                    approval_id=ap.approval_id, tenant_id=ap.tenant_id,
                    action=ap.action, status=contracts.ApprovalStatus.EXPIRED,
                    requested_by=ap.requested_by, decided_by=ap.decided_by,
                    expires_at=ap.expires_at)
                count += 1
        return count


class FakeAuditLedger:
    """Real hash-chain pattern, in-memory."""

    GENESIS = "GENESIS" + "0" * 57

    def __init__(self) -> None:
        self._chains: dict[str, list[contracts.AuditEntry]] = {}

    async def append(self, tenant: contracts.TenantContext, actor: str,
                     action: str, payload: dict[str, Any]) -> contracts.AuditEntry:
        chain = self._chains.setdefault(tenant.tenant_id, [])
        prev = chain[-1].hash if chain else self.GENESIS
        digest = hashlib.sha256(
            (prev + _canonical(payload)).encode()).hexdigest()
        entry = contracts.AuditEntry(
            tenant_id=tenant.tenant_id, actor=actor, action=action,
            payload=dict(payload), prev_hash=prev, hash=digest,
            occurred_at=_utcnow())
        chain.append(entry)
        return entry

    async def verify_chain(self, tenant: contracts.TenantContext,
                           from_seq: int = 0) -> bool:
        chain = self._chains.get(tenant.tenant_id, [])[from_seq:]
        prev = self.GENESIS if from_seq == 0 else None
        for e in chain:
            if prev is None:
                prev = e.prev_hash
            if e.prev_hash != prev:
                return False
            if hashlib.sha256((e.prev_hash + _canonical(e.payload)).encode()
                              ).hexdigest() != e.hash:
                return False
            prev = e.hash
        return True

    def entries(self, tenant_id: str) -> list[contracts.AuditEntry]:
        return list(self._chains.get(tenant_id, []))

    async def list_entries(self, tenant: contracts.TenantContext, limit: int = 50,
                           offset: int = 0, action: str | None = None,
                           actor: str | None = None,
                           ) -> tuple[list[contracts.AuditEntry], int]:
        chain = self._chains.get(tenant.tenant_id, [])
        items = [e for e in chain
                 if (action is None or e.action == action)
                 and (actor is None or e.actor == actor)]
        return items[offset:offset + limit], len(items)

    async def get_by_seq(self, tenant: contracts.TenantContext,
                         seq: int) -> contracts.AuditEntry | None:
        chain = self._chains.get(tenant.tenant_id, [])
        return chain[seq - 1] if 1 <= seq <= len(chain) else None


class FakeBudgetEnforcer:
    def __init__(self, credit_limit: Decimal = Decimal("100")) -> None:
        self.credit_limit = credit_limit
        self.spent = Decimal("0")
        self._frozen = False
        self.reservations: list[dict[str, Any]] = []

    async def reserve(self, tenant: contracts.TenantContext,
                      estimated_credits: Decimal, purpose: str) -> contracts.BudgetDecision:
        if self._frozen:
            return contracts.BudgetDecision(allowed=False, reason="kill switch active",
                                            remaining_credits=Decimal("0"))
        if self.spent + estimated_credits > self.credit_limit:
            return contracts.BudgetDecision(
                allowed=False, reason="budget exhausted",
                remaining_credits=self.credit_limit - self.spent)
        rid = f"rsv-{uuid.uuid4().hex[:8]}"
        self.reservations.append({"id": rid, "estimated": estimated_credits,
                                  "purpose": purpose})
        # remaining AFTER this reservation (mirrors BudgetEnforcerImpl)
        return contracts.BudgetDecision(allowed=True, reservation_id=rid,
                                        remaining_credits=self.credit_limit - self.spent
                                        - estimated_credits)

    async def settle(self, tenant: contracts.TenantContext, reservation_id: str,
                     actual_credits: Decimal) -> None:
        self.spent += actual_credits

    async def kill_switch(self, tenant: contracts.TenantContext, reason: str) -> None:
        self._frozen = True

    async def release_kill_switch(self, tenant: contracts.TenantContext) -> None:
        self._frozen = False

    async def is_frozen(self, tenant: contracts.TenantContext) -> bool:
        return self._frozen

    async def create_budget(self, tenant: contracts.TenantContext, name: str,
                            credit_limit: Decimal, period: str = "monthly",
                            scope: str = "tenant",
                            scope_ref: str | None = None,
                            ) -> contracts.BudgetView:
        return contracts.BudgetView(
            id=f"bgt-{uuid.uuid4().hex[:8]}", name=name, scope=scope,
            scope_ref=scope_ref, credit_limit=credit_limit, period=period,
            is_active=True)

    async def list_budgets(self, tenant: contracts.TenantContext,
                           ) -> list[dict[str, Any]]:
        return [{"id": "bgt-fake", "name": "fake", "scope": "tenant",
                 "scope_ref": None, "credit_limit": str(self.credit_limit),
                 "period": "monthly", "is_active": True,
                 "credits_used": str(self.spent),
                 "remaining": str(self.credit_limit - self.spent)}]

    async def cost_ledger_entries(self, tenant: contracts.TenantContext,
                                  limit: int = 50, offset: int = 0,
                                  ) -> tuple[list[contracts.CostLedgerView], int]:
        return [], 0

    async def list_alerts(self, tenant: contracts.TenantContext,
                          limit: int = 100) -> list[contracts.SpendAlertView]:
        return []


class FakeCostRecorder:
    def __init__(self) -> None:
        self.records: list[contracts.CostRecord] = []

    async def record(self, record: contracts.CostRecord) -> None:
        self.records.append(record)

    async def entries(self, tenant: contracts.TenantContext) -> list[dict[str, Any]]:
        return [{"model": r.model, "cost_usd": str(r.cost_usd),
                 "input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
                 "task_id": r.task_id}
                for r in self.records if r.tenant_id == tenant.tenant_id]


# ---------------------------------------------------------------------------
# Service graph builder
# ---------------------------------------------------------------------------
async def build_graph(tenant: contracts.TenantContext, *,
                      budget_limit: Decimal = Decimal("100")) -> dict[str, Any]:
    """Build a fully wired in-memory service graph for one tenant."""
    from app.agents.models import make_provider
    from app.agents.orchestrator import InMemoryTaskStore, Orchestrator, OrchestratorDeps
    from app.agents.planner import Planner
    from app.agents.registry import AgentRegistry
    from app.agents.router import AgentRouter
    from app.agents.tools import ToolExecutor, ToolRegistry
    from app.api.routers.command_center import AttentionDeps, AttentionService, IntentParser
    from app.knowledge.store import HashEmbeddingProvider, KnowledgeStore
    from app.memory.agrl.ledger import AGRLLedger
    from app.memory.stores import MemoryStore, NamespaceKind

    fakes = {
        "policy": FakePolicyEngine(),
        "approvals": FakeApprovalStore(),
        "audit": FakeAuditLedger(),
        "budgets": FakeBudgetEnforcer(credit_limit=budget_limit),
        "costs": FakeCostRecorder(),
    }
    registry = AgentRegistry()
    tool_registry = ToolRegistry()
    agrl = AGRLLedger()
    memory = MemoryStore()
    knowledge = KnowledgeStore(embedding=HashEmbeddingProvider())
    tasks = InMemoryTaskStore()
    planner = Planner(tool_registry=tool_registry)
    agent_router = AgentRouter(registry=registry)
    executor = ToolExecutor(policy=fakes["policy"], approvals=fakes["approvals"],
                            audit=fakes["audit"], registry=tool_registry,
                            costs=fakes["costs"])
    models = make_provider(costs=fakes["costs"], force_stub=True)

    async def _knowledge_search(t: contracts.TenantContext,
                                a: dict[str, Any]) -> dict[str, Any]:
        hits = await knowledge.search(t, str(a.get("query", "")),
                                      top_k=int(a.get("top_k", 8)))
        return {"hits": [{"text": h.text, "score": h.score,
                          "citations": h.citations} for h in hits],
                "count": len(hits)}

    async def _memory_store(t: contracts.TenantContext,
                            a: dict[str, Any]) -> dict[str, Any]:
        await memory.put(t, str(a["namespace"]), str(a["key"]),
                         dict(a["value"]))
        return {"stored": True, "namespace": a["namespace"], "key": a["key"]}

    async def _memory_recall(t: contracts.TenantContext,
                             a: dict[str, Any]) -> dict[str, Any]:
        found = await memory.get(t, str(a["namespace"]), str(a["key"]))
        return {"found": found is not None, "record": found}

    await registry.seed(tenant)
    await tool_registry.seed_internal_tools(
        tenant, knowledge_search=_knowledge_search,
        memory_store=_memory_store, memory_recall=_memory_recall)
    for name, kind in (("short_term", NamespaceKind.SHORT_TERM),
                       ("episodic", NamespaceKind.EPISODIC),
                       ("semantic", NamespaceKind.SEMANTIC)):
        await memory.create_namespace(tenant, name=name, kind=kind)

    graph: dict[str, Any] = {
        "tenant": tenant,
        **fakes,
        "registry": registry, "planner": planner, "agent_router": agent_router,
        "tool_registry": tool_registry, "executor": executor, "models": models,
        "agrl": agrl, "memory": memory, "knowledge": knowledge, "tasks": tasks,
        "orchestrator": Orchestrator(OrchestratorDeps(
            policy=fakes["policy"], approvals=fakes["approvals"],
            audit=fakes["audit"], budgets=fakes["budgets"], costs=fakes["costs"],
            registry=registry, planner=planner, agent_router=agent_router,
            tool_registry=tool_registry, executor=executor, models=models,
            agrl=agrl, memory=memory, knowledge=knowledge, tasks=tasks,
            retry_backoff_seconds=0)),
        "attention": AttentionService(AttentionDeps(
            approvals=fakes["approvals"], tasks=tasks, budgets=fakes["budgets"],
            audit=fakes["audit"], agrl=agrl,
            policy_decisions=fakes["policy"].decisions)),
        "intent_parser": IntentParser(),
    }
    return graph


# ---------------------------------------------------------------------------
# Fixtures (ACP builder's)
# ---------------------------------------------------------------------------
@pytest.fixture
def tenant() -> contracts.TenantContext:
    return contracts.TenantContext(tenant_id="tenant-acp-1", user_id="user-1",
                                   roles=("admin",))


@pytest.fixture
def tenant_b() -> contracts.TenantContext:
    return contracts.TenantContext(tenant_id="tenant-acp-2", user_id="user-2",
                                   roles=("admin",))


@pytest.fixture
async def svc(tenant: contracts.TenantContext) -> dict[str, Any]:
    """Fully wired + seeded in-memory service graph."""
    return await build_graph(tenant)


# ---------------------------------------------------------------------------
# Foundation fixtures (DB-backed): owned by the foundation builder.
# ---------------------------------------------------------------------------
FOUNDATION_ROUTERS = (auth.router, tenants.router, admin.router,
                      approvals.router, audit.router, billing.router,
                      budgets.router)

_session_holder: dict[str, Any] = {}
_settings_holder: dict[str, Any] = {}


@pytest.fixture(scope="session")
def test_settings() -> Settings:
    fd, path = tempfile.mkstemp(prefix="azienda-test-", suffix=".db")
    os.close(fd)
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    s.database_url = f"sqlite+aiosqlite:///{path}"
    s.jwt_secret = "test-secret-not-for-production-use-only"
    s.rate_limit_per_minute = 1000
    s.rate_limit_burst = 1000
    _settings_holder["settings"] = s
    yield s
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _foundation_schema(test_settings: Settings):
    init_engine(test_settings)
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    await get_engine().dispose()


@pytest_asyncio.fixture()
async def db_session(_foundation_schema) -> AsyncIterator[AsyncSession]:  # noqa: ANN001
    """One rolled-back transaction per test (test isolation)."""
    async with get_engine().connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False,
                               join_transaction_mode="create_savepoint")
        _session_holder["session"] = session
        try:
            yield session
            await session.flush()
        finally:
            await session.close()
            await trans.rollback()
            _session_holder.pop("session", None)


async def _override_get_db() -> AsyncIterator[AsyncSession]:
    yield _session_holder["session"]


def _override_get_settings() -> Settings:
    return _settings_holder["settings"]


@pytest_asyncio.fixture()
async def client(db_session) -> AsyncIterator[AsyncClient]:  # noqa: ANN001
    """HTTP client against a test app mounting ONLY the foundation routers.

    The full ``app.main`` assembly is currently broken by another builder's
    in-progress ``app/api/routers/_common.py`` (imports a not-yet-existing
    ``app.crm.service.make_service``); the integration pass owns that merge.
    """
    app = FastAPI(title="azienda-test")
    install_error_handlers(app)
    for router in FOUNDATION_ROUTERS:
        app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[api_deps.get_settings] = _override_get_settings
    app.dependency_overrides[api_deps.get_db] = _override_get_db
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------- helpers
async def register_tenant(client: AsyncClient, name: str = "Acme Inc",
                          email: str = "owner@example.com",
                          password: str = "Str0ng!Passw0rd") -> dict:
    r = await client.post("/api/v1/auth/register",
                          json={"tenant_name": name, "email": email,
                                "password": password})
    assert r.status_code == 201, r.text
    return r.json()


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
