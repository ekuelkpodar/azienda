"""Shared HTTP helpers for the crm/tasks/workflows routers.

This module is the COMPOSITION ROOT for these three routers: it is the one
place allowed to wire concrete services together — including adapting
CRMService/TaskService to the workflow ports (workflows/ports.py). Domain
packages never import each other; only this composition layer does.

- TenantContext is resolved from request headers (X-Tenant-Id, X-User-Id).
  This is a DEV/TEST seam: the api-foundation builder's auth (JWT claims)
  replaces it. A request without X-Tenant-Id is a 401.
- Error envelope per API.md §1: {"error": {"code","message","details","trace_id"}}.
- Cursor pagination: opaque base64 offset tokens, page_size default 50 max 200.
- Idempotency-Key on mutating POSTs uses the foundation's
  core.idempotency.IdempotencyStore (idempotency_keys table): replays within
  the TTL return the stored response; a different payload under the same key
  is a 409.
- get_services() builds the service container from env (PermissivePolicyEngine
  + in-process bus by default; REFUSES to run with permissive policy when
  AZIENDA_ENVIRONMENT=production). Tests override this dependency.

TECH DEBT (integration, 2026-09-15): this module is a parallel composition root
alongside app.api.deps. The 8 routers built on it (crm, tasks, workflows,
comms, marketing, support, scheduling, finance) resolve the tenant from the
client-supplied X-Tenant-Id header and compose services locally, while the 7
foundation routers (auth, tenants, admin, approvals, audit, billing, budgets)
use app.api.deps with JWT claims. Remediation (not done: would touch 8 routers
+ ~100 green tests + needs CRM/Tasks/Workflows service providers in deps.py):
1) add get_crm_service/get_task_service/get_workflow_service to app.api.deps;
2) move CRMLeadAdapter/TasksTaskAdapter wiring into deps.py;
3) switch these routers' TenantDep to deps.get_tenant_context;
4) migrate tests from X-Tenant-Id headers to minted JWTs.
Until then: get_tenant REFUSES the header seam in production (fail-closed 401),
and build_services REFUSES PermissivePolicyEngine in production.
"""

from __future__ import annotations

import base64
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import errors as core_errors
from app.core.config import settings as core_settings
from app.core.contracts import (
    ActionRequest,
    Approval,
    ApprovalStatus,
    ApprovalStore,
    DomainEvent,
    EventBus,
    PolicyDecision,
    PolicyEffect,
    PolicyEngine,
    TenantContext,
)
from app.core.events import InProcessEventBus
from app.core.idempotency import IdempotencyStore
from app.crm.service import CRMNotFound, CRMService
from app.tasks.service import TaskService
from app.workflows.ports import AgentTaskDelegate, AgentTaskResult, LeadPort, TaskPort
from app.workflows.runner import WorkflowRunner
from app.workflows.service import WorkflowService


# ------------------------------------------------------------------ tenant
def get_tenant(
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
) -> TenantContext:
    """Resolve the tenant. DEV/TEST seam — replaced by JWT auth (api-foundation).

    Fail-closed: in production this header seam is refused outright (tenant
    spoofing risk); the foundation's JWT-based ``get_tenant_context``
    (app.api.deps) must replace it before these routes serve prod traffic.
    See TECH DEBT note in module docstring.
    """
    if os.environ.get("AZIENDA_ENVIRONMENT", "development") == "production":
        raise HTTPException(
            status_code=401,
            detail="X-Tenant-Id auth seam is disabled in production: "
                   "wire app.api.deps.get_tenant_context (JWT) first",
        )
    if not x_tenant_id:
        raise HTTPException(status_code=401, detail="missing X-Tenant-Id (auth not wired yet)")
    return TenantContext(tenant_id=x_tenant_id, user_id=x_user_id or None)


# ------------------------------------------------------------------ errors
def error_response(code: str, message: str, details: dict | None = None,
                   status_code: int = 400) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=core_errors.error_envelope(code, message, details, uuid.uuid4().hex),
    )


def require_idempotency_key(request: Request) -> str | JSONResponse:
    """Guard for mutating routes: Idempotency-Key is mandatory (API.md §1).

    Returns the key, or a 422 ``missing_idempotency_key`` JSONResponse when the
    header is absent. Handlers return the response unchanged.
    """
    key = request.headers.get("Idempotency-Key")
    if not key:
        return error_response("missing_idempotency_key",
                              "Idempotency-Key header is required for mutating requests",
                              status_code=422)
    return key


def domain_error_to_response(e: Exception) -> JSONResponse:
    """Map domain errors to the API.md error envelope."""
    name = type(e).__name__
    mapping = {
        "CRMNotFound": ("not_found", 404), "TaskNotFound": ("not_found", 404),
        "WorkflowNotFound": ("not_found", 404),
        "CRMDuplicate": ("duplicate", 409),
        "CRMValidationError": ("validation_error", 400),
        "TaskValidationError": ("validation_error", 400),
        "WorkflowValidationError": ("validation_error", 400),
        "DagValidationError": ("validation_error", 400),
        "IllegalTransitionError": ("illegal_transition", 409),
        "DependencyCycleError": ("dependency_cycle", 409),
        "PolicyDeniedError": ("policy_denied", 403),
        "WorkflowError": ("workflow_error", 400),
    }
    code, status = mapping.get(name, ("internal_error", 500))
    details = dict(getattr(e, "details", {}) or {})
    if hasattr(e, "errors"):  # DagValidationError
        details = {"errors": e.errors}
    message = "internal error" if status == 500 else str(e)
    return error_response(code, message, details, status)


# ------------------------------------------------------------------ pagination
def encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode()).decode()


def decode_cursor(token: str | None) -> int:
    if not token:
        return 0
    try:
        return max(0, int(base64.urlsafe_b64decode(token.encode()).decode()))
    except Exception:
        raise HTTPException(status_code=400, detail="invalid page_token") from None


def page_params(page_size: int = 50, page_token: str | None = None) -> tuple[int, int]:
    if page_size < 1 or page_size > 200:
        raise HTTPException(status_code=400, detail="page_size must be 1..200")
    return page_size, decode_cursor(page_token)


def page_response(items: list, total: int, offset: int, page_size: int) -> dict:
    next_offset = offset + page_size
    return {
        "items": items,
        "next_page_token": encode_cursor(next_offset) if next_offset < total else None,
    }


# ------------------------------------------------------------------ idempotency (foundation store)
async def idempotent_post(
    request: Request, services: Services, tenant: TenantContext, handler: Any,
) -> JSONResponse:
    """Run a mutating POST with Idempotency-Key support (API.md §1).

    handler() -> (body_dict, status_code). Without a key the handler just runs.
    """
    key = request.headers.get("Idempotency-Key")
    if not key:
        body, status = await handler()
        return JSONResponse(status_code=status, content=body)
    raw = await request.body()
    path = request.url.path
    async with services.session_factory() as session:
        store = IdempotencyStore(session, core_settings)
        try:
            replay = await store.check(tenant.tenant_id, key, request.method, path, raw)
        except core_errors.ConflictError as e:
            return error_response("idempotency_conflict", str(e), status_code=409)
        if replay is not None:
            status_code, resp_body = replay
            return JSONResponse(status_code=status_code, content=resp_body)
        body, status = await handler()
        await store.store(tenant.tenant_id, key, request.method, path, raw, status, body)
        await session.commit()
    return JSONResponse(status_code=status, content=body)


# ------------------------------------------------- port adapters (composition root)
class CRMLeadAdapter(LeadPort):
    """LeadPort backed by CRMService. Lives here (not in workflows/) so the
    workflows package never imports a sibling package's internals."""

    def __init__(self, crm_service: CRMService) -> None:
        self._crm = crm_service

    async def get_lead(self, tenant: TenantContext, lead_id: str) -> dict[str, Any] | None:
        try:
            lead = await self._crm.get_lead(tenant, lead_id)
        except CRMNotFound:
            return None
        contact = org = None
        if lead.contact_id:
            try:
                contact = await self._crm.get_contact(tenant, lead.contact_id)
            except CRMNotFound:
                contact = None
        if lead.org_id:
            try:
                org = await self._crm.get_organization(tenant, lead.org_id)
            except CRMNotFound:
                org = None
        return {
            "id": str(lead.id),
            "status": lead.status,
            "source": lead.source,
            "score": lead.score,
            "owner_id": lead.owner_id,
            "contact_name": (
                f"{contact.first_name} {contact.last_name or ''}".strip() if contact else None
            ),
            "contact_email": contact.email if contact else None,
            "contact_title": contact.title if contact else None,
            "org_name": org.name if org else None,
            "org_industry": org.industry if org else None,
            "org_size_band": org.size_band if org else None,
        }

    async def set_score(self, tenant: TenantContext, lead_id: str, score: int,
                        breakdown: dict[str, Any]) -> None:
        # score is always written via rescore_lead so the breakdown is authoritative
        raise NotImplementedError("use rescore_lead")

    async def rescore_lead(
        self, tenant: TenantContext, lead_id: str
    ) -> tuple[int, dict[str, Any]]:
        return await self._crm.score_lead(tenant, lead_id)

    async def set_status(self, tenant: TenantContext, lead_id: str, status: str) -> None:
        await self._crm.update_lead(tenant, lead_id, {"status": status})

    async def log_activity(self, tenant: TenantContext, subject_type: str, subject_id: str,
                           type: str, body: str | None) -> str:
        activity = await self._crm.log_activity(
            tenant, subject_type, subject_id, type, body)
        return str(activity.id)


class TasksTaskAdapter(TaskPort):
    """TaskPort backed by TaskService. Lives here for the same reason."""

    def __init__(self, task_service: TaskService) -> None:
        self._tasks = task_service

    async def create_task(self, tenant: TenantContext, *, title: str,
                          description: str | None = None, priority: str = "medium",
                          due_at: Any | None = None,
                          idempotency_key: str | None = None) -> dict[str, Any]:
        due = None
        if due_at:
            due = due_at if isinstance(due_at, datetime) else datetime.fromisoformat(due_at)
        task = await self._tasks.create_task(
            tenant,
            {"title": title, "description": description, "priority": priority,
             "due_at": due},
            idempotency_key=idempotency_key,
        )
        return {"id": str(task.id), "title": task.title, "status": task.status}


# ------------------------------------------------------------------ dev/test doubles
class RecordingEventBus(EventBus):
    """Test double: records published events so tests can assert on them."""

    def __init__(self) -> None:
        self.events: list[DomainEvent] = []

    async def publish(self, event: DomainEvent) -> None:
        self.events.append(event)

    def subscribe(self, topic: str, handler: Any) -> None:
        pass  # not needed for the MVP paths


class PermissivePolicyEngine(PolicyEngine):
    """DEV/TEST ONLY: allows everything. Never use in production."""

    async def evaluate(self, request: ActionRequest) -> PolicyDecision:
        return PolicyDecision(effect=PolicyEffect.ALLOW, policy_id="dev-permissive",
                              reasons=("dev permissive policy",))


class DenyAllPolicyEngine(PolicyEngine):
    """Test helper: denies everything."""

    async def evaluate(self, request: ActionRequest) -> PolicyDecision:
        return PolicyDecision(effect=PolicyEffect.DENY, policy_id="test-deny",
                              reasons=("test deny-all",))


class ScriptedPolicyEngine(PolicyEngine):
    """Test helper: REQUIRE_APPROVAL for actions with a matching prefix, allow rest."""

    def __init__(self, approval_prefixes: tuple[str, ...] = (),
                 deny_prefixes: tuple[str, ...] = ()):
        self.approval_prefixes = approval_prefixes
        self.deny_prefixes = deny_prefixes

    async def evaluate(self, request: ActionRequest) -> PolicyDecision:
        if any(request.action.startswith(p) for p in self.deny_prefixes):
            return PolicyDecision(effect=PolicyEffect.DENY, policy_id="test-scripted",
                                  reasons=("test scripted deny",))
        if any(request.action.startswith(p) for p in self.approval_prefixes):
            return PolicyDecision(effect=PolicyEffect.REQUIRE_APPROVAL,
                                  policy_id="test-scripted",
                                  reasons=("test scripted approval",))
        return PolicyDecision(effect=PolicyEffect.ALLOW, policy_id="test-scripted",
                              reasons=("test scripted allow",))


class InMemoryApprovalStore(ApprovalStore):
    """Test/dev ApprovalStore implementing the contracts protocol."""

    def __init__(self) -> None:
        self._approvals: dict[str, Approval] = {}

    async def request(self, decision: PolicyDecision, request: ActionRequest,
                      risk_score: float = 0.0,
                      risk_factors: tuple[str, ...] = ()) -> Approval:
        approval = Approval(
            approval_id=f"appr-{uuid.uuid4().hex[:12]}",
            tenant_id=request.tenant.tenant_id,
            action=request,
            status=ApprovalStatus.PENDING,
            requested_by="workflow-engine",
            decided_by=None,
            expires_at=datetime.now(UTC),
        )
        self._approvals[approval.approval_id] = approval
        return approval

    async def decide(self, tenant: TenantContext, approval_id: str,
                     approved: bool, note: str = "") -> Approval:
        current = self._approvals[approval_id]
        updated = Approval(
            approval_id=current.approval_id, tenant_id=tenant.tenant_id,
            action=current.action,
            status=ApprovalStatus.APPROVED if approved else ApprovalStatus.DENIED,
            requested_by=current.requested_by, decided_by=tenant.user_id,
            expires_at=current.expires_at)
        self._approvals[approval_id] = updated
        return updated

    async def get(self, tenant: TenantContext, approval_id: str) -> Approval | None:
        return self._approvals.get(approval_id)


class StubAgentDelegate(AgentTaskDelegate):
    """Test/dev AgentTaskDelegate: returns a canned result, never calls a model.

    Production wiring replaces this with the agents builder's implementation.
    """

    def __init__(self, summary: str = "stub agent run", output: dict | None = None,
                 cost_usd: float = 0.0) -> None:
        self.summary = summary
        self.output = output or {}
        self.cost_usd = cost_usd
        self.calls: list[dict[str, Any]] = []

    async def run(self, tenant: TenantContext, *, capability: str, goal: str,
                  context: dict[str, Any], autonomy_level: int = 1) -> AgentTaskResult:
        self.calls.append({"capability": capability, "goal": goal,
                           "autonomy_level": autonomy_level})
        return AgentTaskResult(ok=True, summary=self.summary, output=dict(self.output),
                               cost_usd=self.cost_usd)


# ------------------------------------------------------------------ service container
@dataclass
class Services:
    session_factory: async_sessionmaker[AsyncSession]
    bus: EventBus
    policy: PolicyEngine
    crm: CRMService
    tasks: TaskService
    workflows: WorkflowService
    runner: WorkflowRunner
    approvals: ApprovalStore | None = None
    agent_delegate: AgentTaskDelegate | None = None


_engine = None


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _engine
    if _engine is None:
        url = os.environ.get("AZIENDA_DATABASE_URL", "sqlite+aiosqlite:///./azienda.db")
        _engine = create_async_engine(url, future=True)
    return async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)


def build_services(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    bus: EventBus | None = None,
    policy: PolicyEngine | None = None,
    approvals: ApprovalStore | None = None,
    agent_delegate: AgentTaskDelegate | None = None,
) -> Services:
    """Compose the service container. Production wiring (real policy engine,
    approval store, agent delegate) is the api-foundation / governance /
    agents builders' job; until then this uses explicit dev/test doubles.
    Refuses permissive policy in production."""
    sf = session_factory or get_session_factory()
    event_bus = bus or InProcessEventBus()  # ADR-007: in-process bus, ledger is truth
    if policy is None:
        if os.environ.get("AZIENDA_ENVIRONMENT", "development") == "production":
            raise RuntimeError(
                "PermissivePolicyEngine refused in production: wire the governance "
                "PolicyEngine before serving traffic.")
        policy = PermissivePolicyEngine()
    crm = CRMService(sf, event_bus, policy)
    tasks = TaskService(sf, event_bus, policy)
    runner = WorkflowRunner(
        sf, event_bus, policy,
        approval_store=approvals, agent_delegate=agent_delegate,
        lead_port=CRMLeadAdapter(crm), task_port=TasksTaskAdapter(tasks),
    )
    workflows = WorkflowService(sf, event_bus, policy, runner)
    return Services(session_factory=sf, bus=event_bus, policy=policy, crm=crm,
                    tasks=tasks, workflows=workflows, runner=runner,
                    approvals=approvals, agent_delegate=agent_delegate)


def get_services(request: Request) -> Services:
    """FastAPI dependency. Tests override this with build_services(...fakes...)."""
    services = getattr(request.app.state, "services", None)
    if services is None:
        services = build_services()
        request.app.state.services = services
    return services


ServicesDep = Depends(get_services)
TenantDep = Depends(get_tenant)
