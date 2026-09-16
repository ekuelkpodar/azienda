"""Agent registry: definitions, versioning, and the seeded workforce.

Implements ``core.contracts.AgentRegistry`` (resolve-by-capability, get) plus
versioning (publish new version, pause/resume) and lifecycle status.

Tenancy: every record is keyed by tenant_id; lookups always scope to the tenant.

This is the in-memory MVP adapter. The production binding is a SQLAlchemy
repository over ``agents`` / ``agent_versions`` (DATABASE.md §6) — same interface,
swapped behind the ``AgentRegistry`` protocol.
"""

from __future__ import annotations

import builtins
import dataclasses
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, TypedDict

from app.core import contracts


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AgentStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    DEPRECATED = "deprecated"


@dataclass
class AgentDefinitionRecord:
    """Full agent definition. The contracts.AgentDefinition is the wire subset."""

    agent_id: str
    name: str
    version: int
    description: str
    capabilities: tuple[str, ...] = ()
    # Capabilities the agent explicitly does NOT have — honest declarations.
    cannot: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    model_prefs: dict[str, object] = field(default_factory=dict)
    owner: str = "platform"
    status: AgentStatus = AgentStatus.ACTIVE
    permissions: tuple[str, ...] = ()
    autonomy_level: int = 1  # L0..L5 progressive autonomy (ARCHITECTURE.md §2.6)
    budget_ref: str | None = None
    approval_policy: dict[str, object] = field(default_factory=dict)
    published_at: datetime = field(default_factory=_utcnow)

    def to_contract(self) -> contracts.AgentDefinition:
        return contracts.AgentDefinition(
            agent_id=self.agent_id,
            name=self.name,
            version=self.version,
            capabilities=self.capabilities,
            allowed_tools=self.allowed_tools,
            autonomy_level=self.autonomy_level,
        )


# ---------------------------------------------------------------------------
# Seed workforce: the 10 mandated agents.
# Capability declarations are HONEST: every agent lists what it cannot do.
# Autonomy levels are conservative on first seed (finance = L1, read-only = L3).
# ---------------------------------------------------------------------------
class _SeedAgentSpec(TypedDict):
    """Shape of one entry in SEED_AGENTS (the 10 mandated workforce agents)."""
    agent_id: str
    name: str
    description: str
    capabilities: tuple[str, ...]
    cannot: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    permissions: tuple[str, ...]
    autonomy_level: int
    approval_policy: dict[str, Any]


SEED_AGENTS: tuple[_SeedAgentSpec, ...] = (
    {
        "agent_id": "executive-assistant",
        "name": "Executive Assistant",
        "description": "Inbox triage, scheduling coordination, task management, drafting.",
        "capabilities": ("email.triage", "scheduling.coordination", "task.management",
                         "document.drafting", "meeting.prep"),
        "cannot": ("financial.posting", "bulk.messaging", "contract.signing",
                   "production.config"),
        "allowed_tools": ("tasks.create_task", "tasks.transition_task", "comms.send_email",
                          "knowledge.search", "memory.store", "memory.recall"),
        "permissions": ("tasks:write", "comms:send_single", "knowledge:read", "memory:write"),
        "autonomy_level": 2,
        "approval_policy": {"usd_threshold": 5.0,
                            "always_require": ["comms.send_email"]},
    },
    {
        "agent_id": "sales-agent",
        "name": "Sales",
        "description": "Lead research, outreach drafting, opportunity hygiene, follow-ups.",
        "capabilities": ("lead.research", "outreach.drafting", "opportunity.hygiene",
                         "followup.scheduling", "call.prep"),
        "cannot": ("discount.approval", "contract.signing", "bulk.messaging",
                   "pricing.changes"),
        "allowed_tools": ("crm.search_contacts", "crm.create_contact", "crm.create_opportunity",
                          "crm.log_activity", "knowledge.search", "tasks.create_task"),
        "permissions": ("crm:write", "knowledge:read", "tasks:write"),
        "autonomy_level": 2,
        "approval_policy": {"usd_threshold": 5.0,
                            "always_require": ["comms.send_bulk_sms"]},
    },
    {
        "agent_id": "marketing-agent",
        "name": "Marketing",
        "description": "Content drafting, audience segmentation, campaign reporting.",
        "capabilities": ("content.drafting", "audience.segmentation", "campaign.reporting",
                         "brand.check"),
        "cannot": ("campaign.launch", "ad.spend", "bulk.messaging", "list.purchase"),
        "allowed_tools": ("knowledge.search", "crm.search_contacts", "tasks.create_task",
                          "memory.store"),
        "permissions": ("crm:read", "knowledge:read", "tasks:write", "memory:write"),
        "autonomy_level": 2,
        "approval_policy": {"usd_threshold": 5.0,
                            "always_require": ["comms.send_bulk_sms", "comms.send_email"]},
    },
    {
        "agent_id": "support-agent",
        "name": "Customer Support",
        "description": "Ticket triage, KB-grounded answers, escalation drafting.",
        "capabilities": ("ticket.triage", "kb.answering", "escalation.drafting",
                         "sla.monitoring"),
        "cannot": ("refund.issuing", "account.deletion", "bulk.messaging",
                   "policy.exceptions"),
        "allowed_tools": ("knowledge.search", "crm.search_contacts", "crm.log_activity",
                          "tasks.create_task", "comms.send_email"),
        "permissions": ("knowledge:read", "crm:write", "tasks:write", "comms:send_single"),
        "autonomy_level": 2,
        "approval_policy": {"usd_threshold": 5.0,
                            "always_require": ["comms.send_email"]},
    },
    {
        "agent_id": "operations-agent",
        "name": "Operations",
        "description": "Workflow monitoring, cross-team task coordination, ops reporting.",
        "capabilities": ("workflow.monitoring", "task.coordination", "report.generation",
                         "exception.flagging"),
        "cannot": ("production.config", "financial.posting", "user.provisioning"),
        "allowed_tools": ("tasks.create_task", "tasks.transition_task",
                          "workflows.start_execution", "knowledge.search"),
        "permissions": ("tasks:write", "workflows:execute", "knowledge:read"),
        "autonomy_level": 2,
        "approval_policy": {"usd_threshold": 5.0, "always_require": []},
    },
    {
        "agent_id": "research-agent",
        "name": "Research",
        "description": "Web/document research, synthesis with citations. Read-only.",
        "capabilities": ("web.search", "document.synthesis", "competitor.analysis",
                         "citation.tracking"),
        "cannot": ("acting.on.findings", "external.messaging", "data.modification",
                   "financial.transactions"),
        "allowed_tools": ("knowledge.search", "memory.store", "memory.recall"),
        "permissions": ("knowledge:read", "memory:write"),
        "autonomy_level": 3,  # high autonomy is safe: read-only toolset
        "approval_policy": {"usd_threshold": 5.0, "always_require": []},
    },
    {
        "agent_id": "scheduling-agent",
        "name": "Scheduling",
        "description": "Availability lookup, booking drafting, reminders.",
        "capabilities": ("availability.lookup", "booking.drafting", "reminder.scheduling",
                         "calendar.hygiene"),
        "cannot": ("booking.without.confirmation", "calendar.deletion",
                   "external.messaging"),
        "allowed_tools": ("tasks.create_task", "comms.send_email", "knowledge.search"),
        "permissions": ("tasks:write", "comms:send_single", "knowledge:read"),
        "autonomy_level": 2,
        "approval_policy": {"usd_threshold": 5.0,
                            "always_require": ["comms.send_email"]},
    },
    {
        "agent_id": "finance-assistant",
        "name": "Finance Assistant",
        "description": "Invoice reading, expense categorization, finance report drafting.",
        "capabilities": ("invoice.reading", "expense.categorization", "report.drafting",
                         "variance.flagging"),
        "cannot": ("journal.posting", "payment.issuing", "refund.issuing",
                   "pricing.changes", "bulk.messaging"),
        "allowed_tools": ("knowledge.search", "tasks.create_task", "memory.store"),
        "permissions": ("knowledge:read", "tasks:write", "memory:write"),
        "autonomy_level": 1,  # conservative: money-adjacent, drafts only
        "approval_policy": {"usd_threshold": 0.0,  # every consequential step needs a human
                            "always_require": ["*"]},
    },
    {
        "agent_id": "crm-agent",
        "name": "CRM",
        "description": "Contact lookup, lead scoring, activity logging, data hygiene.",
        "capabilities": ("contact.lookup", "lead.scoring", "activity.logging",
                         "data.hygiene"),
        "cannot": ("bulk.deletion", "bulk.export", "bulk.messaging", "pipeline.deletion"),
        "allowed_tools": ("crm.search_contacts", "crm.create_contact", "crm.log_activity",
                          "tasks.create_task", "knowledge.search"),
        "permissions": ("crm:write", "tasks:write", "knowledge:read"),
        "autonomy_level": 2,
        "approval_policy": {"usd_threshold": 5.0, "always_require": []},
    },
    {
        "agent_id": "analytics-agent",
        "name": "Analytics",
        "description": "Metric computation, anomaly detection, dashboard data. Read-only.",
        "capabilities": ("metric.computation", "anomaly.detection", "dashboard.data",
                         "trend.reporting"),
        "cannot": ("source.data.modification", "external.messaging",
                   "financial.transactions"),
        "allowed_tools": ("knowledge.search", "memory.store", "memory.recall"),
        "permissions": ("knowledge:read", "memory:write"),
        "autonomy_level": 3,  # high autonomy is safe: read-only toolset
        "approval_policy": {"usd_threshold": 5.0, "always_require": []},
    },
)


class AgentRegistry:
    """In-memory AgentRegistry (dev/test adapter). Production: SQLAlchemy repo."""

    def __init__(self) -> None:
        # tenant_id -> agent_id -> builtins.list[AgentDefinitionRecord] (version history)
        self._versions: dict[str, dict[str, builtins.list[AgentDefinitionRecord]]] = {}

    # -- contracts.AgentRegistry ------------------------------------------------
    async def resolve(self, tenant: contracts.TenantContext,
                      capability: str) -> builtins.list[contracts.AgentDefinition]:
        out: builtins.list[contracts.AgentDefinition] = []
        for rec in self._active_records(tenant.tenant_id):
            if capability in rec.capabilities:
                out.append(rec.to_contract())
        return out

    async def get(self, tenant: contracts.TenantContext,
                  agent_id: str) -> contracts.AgentDefinition | None:
        rec = self._latest(tenant.tenant_id, agent_id)
        return rec.to_contract() if rec else None

    # -- versioning / lifecycle --------------------------------------------------
    async def register(self, tenant: contracts.TenantContext,
                       record: AgentDefinitionRecord) -> AgentDefinitionRecord:
        """Register a new agent (version 1) or publish a new version of an existing one."""
        history = self._versions.setdefault(tenant.tenant_id, {}).setdefault(
            record.agent_id, [])
        record = AgentDefinitionRecord(
            **{**record.__dict__, "version": len(history) + 1,
               "published_at": _utcnow()})
        history.append(record)
        return record

    async def create(self, tenant: contracts.TenantContext, *,
                     agent_id: str, name: str, description: str = "",
                     capabilities: tuple[str, ...] = (),
                     cannot: tuple[str, ...] = (),
                     allowed_tools: tuple[str, ...] = (),
                     permissions: tuple[str, ...] = (),
                     autonomy_level: int = 1,
                     owner: str = "platform") -> AgentDefinitionRecord:
        """Create a brand-new agent (version 1, ACTIVE). HTTP layer calls this
        instead of constructing records itself."""
        return await self.register(tenant, AgentDefinitionRecord(
            agent_id=agent_id, name=name, version=1, description=description,
            capabilities=capabilities, cannot=cannot, allowed_tools=allowed_tools,
            owner=owner, status=AgentStatus.ACTIVE, permissions=permissions,
            autonomy_level=autonomy_level))

    async def publish_version(self, tenant: contracts.TenantContext, agent_id: str, *,
                              description: str | None = None,
                              capabilities: tuple[str, ...] | None = None,
                              allowed_tools: tuple[str, ...] | None = None,
                              autonomy_level: int | None = None,
                              status: AgentStatus | str | None = None,
                              ) -> AgentDefinitionRecord | None:
        """Publish a new version of an existing agent. Returns None if unknown."""
        current = self._latest(tenant.tenant_id, agent_id)
        if current is None:
            return None
        return await self.register(tenant, dataclasses.replace(
            current,
            description=description or current.description,
            capabilities=capabilities if capabilities is not None else current.capabilities,
            allowed_tools=(allowed_tools if allowed_tools is not None
                           else current.allowed_tools),
            autonomy_level=(autonomy_level if autonomy_level is not None
                            else current.autonomy_level),
            status=(AgentStatus(status) if isinstance(status, str)
                    else (status or current.status)),
            version=0))  # register() assigns the next version number

    async def get_record(self, tenant: contracts.TenantContext,
                         agent_id: str) -> AgentDefinitionRecord | None:
        return self._latest(tenant.tenant_id, agent_id)

    async def versions(self, tenant: contracts.TenantContext,
                       agent_id: str) -> builtins.list[AgentDefinitionRecord]:
        return list(self._versions.get(tenant.tenant_id, {}).get(agent_id, []))

    async def set_status(self, tenant: contracts.TenantContext, agent_id: str,
                         status: AgentStatus | str) -> AgentDefinitionRecord | None:
        """Lifecycle control: pause/resume/deprecate = new version with new status."""
        current = self._latest(tenant.tenant_id, agent_id)
        if current is None:
            return None
        return await self.register(
            tenant, AgentDefinitionRecord(
                **{**current.__dict__, "status": AgentStatus(status), "version": 0}))

    async def list(self, tenant: contracts.TenantContext,
                   capability: str | None = None,
                   status: AgentStatus | str | None = None) -> builtins.list[AgentDefinitionRecord]:
        # list() shows every latest version (including paused) — routing
        # visibility is decided by _active_records, not here.
        want = AgentStatus(status) if isinstance(status, str) else status
        recs = [h[-1] for h in self._versions.get(tenant.tenant_id, {}).values()
                if h]
        if capability:
            recs = [r for r in recs if capability in r.capabilities]
        if want:
            recs = [r for r in recs if r.status == want]
        return recs

    # -- seeding ------------------------------------------------------------------
    async def seed(self, tenant: contracts.TenantContext,
                   owner: str = "platform") -> builtins.list[AgentDefinitionRecord]:
        """Seed the 10 mandated workforce agents. Idempotent per tenant."""
        seeded: builtins.list[AgentDefinitionRecord] = []
        for spec in SEED_AGENTS:
            if self._latest(tenant.tenant_id, spec["agent_id"]) is not None:
                continue
            rec = AgentDefinitionRecord(
                agent_id=spec["agent_id"],
                name=spec["name"],
                version=1,
                description=spec["description"],
                capabilities=spec["capabilities"],
                cannot=spec["cannot"],
                allowed_tools=spec["allowed_tools"],
                model_prefs={
                    "default": "default", "fallback": "fallback",
                    "reasoning": "reasoning", "low_cost": "low_cost",
                    "fast": "fast", "local": "local",
                },
                owner=owner,
                status=AgentStatus.ACTIVE,
                permissions=spec["permissions"],
                autonomy_level=spec["autonomy_level"],
                budget_ref=f"budget:tenant:{tenant.tenant_id}:agents",
                approval_policy=spec["approval_policy"],
            )
            seeded.append(await self.register(tenant, rec))
        return seeded

    # -- internals ------------------------------------------------------------------
    def _latest(self, tenant_id: str, agent_id: str) -> AgentDefinitionRecord | None:
        history = self._versions.get(tenant_id, {}).get(agent_id, [])
        return history[-1] if history else None

    def _active_records(self, tenant_id: str) -> builtins.list[AgentDefinitionRecord]:
        # Only ACTIVE latest versions route; paused/deprecated agents are
        # invisible to resolve()/list() until resumed.
        return [h[-1] for h in self._versions.get(tenant_id, {}).values()
                if h and h[-1].status == AgentStatus.ACTIVE]


def new_agent_id() -> str:
    return f"agent-{uuid.uuid4().hex[:12]}"
