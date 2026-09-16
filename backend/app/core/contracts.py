"""Cross-package contracts for Azienda.

These protocols/ABCs are the ONLY legal way for one package to use another
package's capabilities. Import the protocol, receive an implementation via
dependency injection — never import another package's internals.

Builders: breaking a signature here breaks every consumer. Treat this file
as a versioned API (it is). Changes require an ADR-level decision.

Conventions:
- All IDs are UUIDs (as str). All money is Decimal. All datetimes are UTC.
- Every method that touches data takes ``tenant: TenantContext`` first.
- Nothing here performs I/O itself; implementations live in their packages.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Protocol, runtime_checkable


# ------------------------------------------------------------------ tenancy
@dataclass(frozen=True)
class TenantContext:
    """Who is acting, in which tenant. Propagated on every call."""

    tenant_id: str
    user_id: str | None = None          # None for pure agent/system actions
    agent_id: str | None = None
    roles: tuple[str, ...] = ()


# ------------------------------------------------------------------ events
@dataclass(frozen=True)
class DomainEvent:
    topic: str                          # e.g. "task.transitioned"
    tenant_id: str
    aggregate_id: str
    payload: dict[str, Any]
    event_id: str
    occurred_at: datetime
    causation_id: str | None = None
    correlation_id: str | None = None


@runtime_checkable
class EventBus(Protocol):
    """Transport only. The Postgres ledger is truth; the bus is not durable."""

    async def publish(self, event: DomainEvent) -> None: ...
    def subscribe(self, topic: str, handler: Callable[[DomainEvent], Awaitable[None]]) -> None: ...


@runtime_checkable
class TaskQueue(Protocol):
    """Background jobs (ARQ now; interface preserves the swap)."""

    async def enqueue(self, name: str, payload: dict[str, Any], *,
                      delay_seconds: float = 0.0,
                      idempotency_key: str | None = None) -> str: ...


# ------------------------------------------------------------------ policy
class PolicyEffect(str, Enum):  # noqa: UP042
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


@dataclass(frozen=True)
class ActionRequest:
    """Anything consequential an agent or user wants to do."""

    tenant: TenantContext
    action: str                         # e.g. "tool.crm.send_bulk_sms"
    resource: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    risk_context: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None


@dataclass(frozen=True)
class PolicyDecision:
    effect: PolicyEffect
    policy_id: str | None
    reasons: tuple[str, ...] = ()
    approval_id: str | None = None      # set when REQUIRE_APPROVAL
    obligations: dict[str, Any] = field(default_factory=dict)  # e.g. {"redact_pii": True}


@runtime_checkable
class PolicyEngine(Protocol):
    async def evaluate(self, request: ActionRequest) -> PolicyDecision: ...


@dataclass(frozen=True)
class RiskScore:
    score: float                        # 0..100
    factors: tuple[str, ...] = ()
    reversible: bool = True
    financial_impact_usd: Decimal = Decimal("0")


@runtime_checkable
class RiskScorer(Protocol):
    async def score(self, request: ActionRequest) -> RiskScore: ...


class ApprovalStatus(str, Enum):  # noqa: UP042
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"


@dataclass(frozen=True)
class Approval:
    approval_id: str
    tenant_id: str
    action: ActionRequest
    status: ApprovalStatus
    requested_by: str
    decided_by: str | None
    expires_at: datetime
    # Populated from the stored request context; default to "unknown" so
    # producers that don't score risk can still build an Approval.
    risk_score: float = 0.0
    risk_factors: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()


@runtime_checkable
class ApprovalStore(Protocol):
    async def request(self, decision: PolicyDecision, request: ActionRequest,
                      risk_score: float = 0.0,
                      risk_factors: tuple[str, ...] = ()) -> Approval: ...
    async def decide(self, tenant: TenantContext, approval_id: str,
                     approved: bool, note: str = "") -> Approval: ...
    async def get(self, tenant: TenantContext, approval_id: str) -> Approval | None: ...
    async def list_pending(self, tenant: TenantContext, limit: int = 50,
                           offset: int = 0) -> tuple[list[Approval], int]: ...
    async def escalate(self, tenant: TenantContext, approval_id: str,
                       note: str = "") -> Approval: ...
    async def sweep_expired(self, tenant: TenantContext | None = None) -> int: ...


@dataclass(frozen=True)
class AuditEntry:
    tenant_id: str
    actor: str
    action: str
    payload: dict[str, Any]
    prev_hash: str
    hash: str
    occurred_at: datetime
    # 1-based position in the tenant's hash chain. Defaults to 0 for entries
    # built outside the ledger (tests/fakes); the ledger always populates it.
    seq: int = 0


@runtime_checkable
class AuditLedger(Protocol):
    """Append-only, hash-chained. Verify the chain, don't trust it blindly."""

    async def append(self, tenant: TenantContext, actor: str, action: str,
                     payload: dict[str, Any]) -> AuditEntry: ...
    async def verify_chain(self, tenant: TenantContext, from_seq: int = 0) -> bool: ...
    async def list_entries(self, tenant: TenantContext, limit: int = 50,
                           offset: int = 0, action: str | None = None,
                           actor: str | None = None,
                           ) -> tuple[list[AuditEntry], int]: ...
    async def get_by_seq(self, tenant: TenantContext, seq: int) -> AuditEntry | None: ...


# ------------------------------------------------------------------ budgets
@dataclass(frozen=True)
class BudgetDecision:
    allowed: bool
    reason: str = ""
    remaining_credits: Decimal = Decimal("0")
    # Populated when allowed=True and credits were actually held. Pass to
    # settle() to release/settle the hold. None means "no hold was created"
    # (e.g. unmetered tenant or denied request).
    reservation_id: str | None = None


@dataclass(frozen=True)
class BudgetView:
    """Read view of a budget for API/CLI consumers (ORM stays in governance)."""
    id: str
    name: str
    scope: str
    scope_ref: str | None
    credit_limit: Decimal
    period: str
    is_active: bool


@dataclass(frozen=True)
class CostLedgerView:
    """One cost-ledger row as seen by API/CLI consumers."""
    id: str
    task_id: str | None
    run_id: str | None
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    credits_drawn: Decimal
    recorded_at: datetime


@dataclass(frozen=True)
class SpendAlertView:
    """One fired budget alert as seen by API/CLI consumers."""
    id: str
    budget_id: str
    threshold: Decimal
    fired_at: datetime
    acknowledged_at: datetime | None


@runtime_checkable
class BudgetEnforcer(Protocol):
    """P0: unbounded agent cost is the #1 business risk. Reserve-before-spend."""

    async def reserve(self, tenant: TenantContext, estimated_credits: Decimal,
                      purpose: str) -> BudgetDecision: ...
    async def settle(self, tenant: TenantContext, reservation_id: str,
                     actual_credits: Decimal) -> None: ...
    async def kill_switch(self, tenant: TenantContext, reason: str) -> None: ...
    async def release_kill_switch(self, tenant: TenantContext) -> None: ...
    async def is_frozen(self, tenant: TenantContext) -> bool: ...
    async def create_budget(self, tenant: TenantContext, name: str,
                            credit_limit: Decimal, period: str = "monthly",
                            scope: str = "tenant",
                            scope_ref: str | None = None) -> BudgetView: ...
    async def list_budgets(self, tenant: TenantContext) -> list[dict[str, Any]]: ...
    async def cost_ledger_entries(self, tenant: TenantContext, limit: int = 50,
                                  offset: int = 0,
                                  ) -> tuple[list[CostLedgerView], int]: ...
    async def list_alerts(self, tenant: TenantContext,
                          limit: int = 100) -> list[SpendAlertView]: ...


@dataclass(frozen=True)
class CostRecord:
    tenant_id: str
    task_id: str | None
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    credits_drawn: Decimal
    recorded_at: datetime


@runtime_checkable
class CostRecorder(Protocol):
    async def record(self, record: CostRecord) -> None: ...


# ------------------------------------------------------------------ credit ledger
@runtime_checkable
class CreditLedger(Protocol):
    """Billing-owned credit pool draw. Implemented by ``billing/``; consumed by
    governance's CostRecorder so table ownership stays clean: governance writes
    ``cost_ledger``, billing writes ``credit_transactions``."""

    async def draw(self, tenant: TenantContext, credits: Decimal, reason: str,
                   task_id: str | None = None) -> None: ...


# ------------------------------------------------------------------ models
@dataclass(frozen=True)
class ModelRequest:
    tenant: TenantContext
    model: str | None                   # None => router picks
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] = field(default_factory=list)
    max_tokens: int | None = None
    temperature: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelResponse:
    content: str | None
    tool_calls: tuple[dict[str, Any], ...] = ()
    model_used: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: Decimal = Decimal("0")
    latency_ms: int = 0


@runtime_checkable
class ModelProvider(Protocol):
    """LiteLLM-backed now; the interface is what callers depend on."""

    async def complete(self, request: ModelRequest) -> ModelResponse: ...


# ------------------------------------------------------------------ tools
@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    risk_tier: str                      # "low" | "medium" | "high" | "critical"
    idempotent: bool = False


@dataclass(frozen=True)
class ToolCall:
    tool_name: str
    arguments: dict[str, Any]
    grant_id: str                       # scoped per-action grant — no ambient authority
    idempotency_key: str | None = None


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    output: Any = None
    error: str | None = None
    cost_usd: Decimal = Decimal("0")


@runtime_checkable
class ToolRegistry(Protocol):
    async def get(self, tenant: TenantContext, name: str) -> ToolSpec | None: ...
    async def list(self, tenant: TenantContext,
                   capability: str | None = None) -> list[ToolSpec]: ...


@runtime_checkable
class ToolExecutor(Protocol):
    """Every call is policy-gated by the implementation BEFORE executing."""

    async def execute(self, tenant: TenantContext, call: ToolCall) -> ToolResult: ...


# ------------------------------------------------------------------ workflows
class ExecutionStatus(str, Enum):  # noqa: UP042
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@runtime_checkable
class WorkflowBackend(Protocol):
    """LangGraph implementation now; Temporal is the documented future binding."""

    async def start(self, tenant: TenantContext, definition_id: str, version: int,
                    input: dict[str, Any],
                    idempotency_key: str | None = None) -> str: ...
    async def signal(self, tenant: TenantContext, execution_id: str,
                     signal: str, payload: dict[str, Any]) -> None: ...
    async def cancel(self, tenant: TenantContext, execution_id: str, reason: str) -> None: ...
    async def get_state(self, tenant: TenantContext, execution_id: str) -> dict[str, Any]: ...


# ------------------------------------------------------------------ knowledge
@dataclass(frozen=True)
class KnowledgeHit:
    chunk_id: str
    document_id: str
    text: str
    score: float
    citations: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class KnowledgeStore(Protocol):
    async def search(self, tenant: TenantContext, query: str, top_k: int = 8,
                     filters: dict[str, Any] | None = None) -> list[KnowledgeHit]: ...
    async def ingest(self, tenant: TenantContext, source_id: str) -> str: ...


# ------------------------------------------------------------------ memory
@runtime_checkable
class MemoryStore(Protocol):
    async def put(self, tenant: TenantContext, namespace: str, key: str,
                  value: dict[str, Any], ttl_seconds: int | None = None) -> None: ...
    async def get(self, tenant: TenantContext, namespace: str,
                  key: str) -> dict[str, Any] | None: ...
    async def forget(self, tenant: TenantContext, namespace: str, key: str) -> None: ...


# ------------------------------------------------------------------ AGRL
@dataclass(frozen=True)
class AGRLEvent:
    """One immutable fact in the Adaptive Goal & Resource Ledger."""

    tenant_id: str
    event_type: str                     # e.g. "goal.created", "resource.allocated"
    aggregate_id: str                   # the goal/resource/plan this belongs to
    payload: dict[str, Any]
    seq: int
    prev_hash: str
    hash: str
    actor: str
    occurred_at: datetime


@runtime_checkable
class AGRLLedger(Protocol):
    """Single event ledger for goals + resources. Projections are derived, never edited."""

    async def append(self, tenant: TenantContext, event_type: str, aggregate_id: str,
                     payload: dict[str, Any], actor: str) -> AGRLEvent: ...
    async def read(self, tenant: TenantContext, aggregate_id: str,
                   from_seq: int = 0) -> list[AGRLEvent]: ...
    async def get_projection(self, tenant: TenantContext, projection: str,
                             aggregate_id: str) -> dict[str, Any]: ...


# ------------------------------------------------------------------ secrets
@runtime_checkable
class SecretBroker(Protocol):
    """Resolve a secret reference to a value. Values must never be logged or returned to clients."""

    async def resolve(self, tenant: TenantContext, ref: str) -> str: ...


# ------------------------------------------------------------------ registry
@dataclass(frozen=True)
class AgentDefinition:
    agent_id: str
    name: str
    version: int
    capabilities: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    autonomy_level: int = 1             # L0..L5 progressive autonomy


@runtime_checkable
class AgentRegistry(Protocol):
    async def resolve(self, tenant: TenantContext, capability: str) -> list[AgentDefinition]: ...
    async def get(self, tenant: TenantContext, agent_id: str) -> AgentDefinition | None: ...
