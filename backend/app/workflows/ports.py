"""Ports: the interfaces the workflow engine needs from other packages.

These Protocols are the workflow engine's declared dependencies. Implementations
are injected at composition time (see service.py ``WorkflowService`` and the api
routers). Nothing in this package imports another package's internals.

INTERFACE NEEDS for other builders (tracked here so nothing is silently stubbed):

1. AGENT steps — needs the agents/ACP builder to provide an implementation of
   ``AgentTaskDelegate`` (or to add an ``Orchestrator`` protocol to
   core/contracts.py, at which point this protocol should delegate to it).
   Until then, AGENT nodes fail closed with a clear "not configured" error.

2. APPROVAL steps — implemented against the existing ``ApprovalStore`` protocol
   from core/contracts.py (governance builder owns the implementation).

3. TOOL steps — implemented against the existing ``ToolExecutor`` protocol
   from core/contracts.py (agents builder owns the implementation).

4. CRM / Task writes — ``LeadPort`` / ``TaskPort`` below are implemented by
   adapters over this deliverable's own CRMService/TaskService (see adapters.py).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from app.core.contracts import TenantContext


@dataclass(frozen=True)
class AgentTaskResult:
    ok: bool
    summary: str = ""
    output: dict[str, Any] | None = None
    cost_usd: float = 0.0
    error: str | None = None


@runtime_checkable
class AgentTaskDelegate(Protocol):
    """INTERFACE NEED: delegate one goal-directed step to the agent workforce.

    The agents/ACP builder implements this (backed by the orchestrator). The
    runner treats a missing delegate as a misconfiguration and fails the node
    closed — it never pretends an agent ran.
    """

    async def run(
        self,
        tenant: TenantContext,
        *,
        capability: str,
        goal: str,
        context: dict[str, Any],
        autonomy_level: int = 1,
    ) -> AgentTaskResult: ...


@runtime_checkable
class LeadPort(Protocol):
    """Minimal CRM surface the workflow engine is allowed to touch."""

    async def get_lead(self, tenant: TenantContext, lead_id: str) -> dict[str, Any] | None: ...

    async def set_score(
        self, tenant: TenantContext, lead_id: str, score: int, breakdown: dict[str, Any]
    ) -> None: ...

    async def set_status(self, tenant: TenantContext, lead_id: str, status: str) -> None: ...

    async def rescore_lead(
        self, tenant: TenantContext, lead_id: str
    ) -> tuple[int, dict[str, Any]]: ...

    async def log_activity(
        self,
        tenant: TenantContext,
        subject_type: str,
        subject_id: str,
        type: str,
        body: str | None,
    ) -> str: ...


@runtime_checkable
class TaskPort(Protocol):
    """Minimal task surface the workflow engine is allowed to touch."""

    async def create_task(
        self,
        tenant: TenantContext,
        *,
        title: str,
        description: str | None = None,
        priority: str = "medium",
        due_at: Any | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]: ...


@runtime_checkable
class NotificationPort(Protocol):
    """Outbound notifications. The comms package owns real delivery; the default
    implementation records the notification as an execution event + domain event."""

    async def notify(
        self, tenant: TenantContext, *, message: str, context: dict[str, Any]
    ) -> None: ...


def coerce_uuid(value: Any) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
