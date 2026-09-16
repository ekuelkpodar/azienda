"""Planner: objective → inspectable plan.

MVP implementation is RULE-BASED (keyword decomposition against the registered
tool catalog) — clearly labeled, deterministic, testable. LLM-based planning
is the documented future: it plugs in behind ``Planner.plan`` without changing
the plan schema, and every LLM-produced plan still passes policy check.

A plan is persisted (in-memory store here; ``tasks.plan`` JSONB in production)
and inspectable via ``explain``.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.core import contracts


def _utcnow() -> datetime:
    return datetime.now(UTC)


# Assumed per-step model/planning overhead constants (ASSUMED — tune with data).
STEP_OVERHEAD_USD = Decimal("0.01")
APPROVAL_COST_THRESHOLD_USD = Decimal("5.00")


@dataclass
class PlanStep:
    step_id: str
    name: str
    description: str
    tool_name: str | None          # None => no capable tool; orchestrator escalates
    arguments: dict[str, Any] = field(default_factory=dict)
    estimated_cost_usd: Decimal = Decimal("0")
    risk_tier: str = "low"
    requires_approval: bool = False
    max_retries: int = 2
    fallback_tool: str | None = None
    approval_reason: str = ""


@dataclass
class Plan:
    plan_id: str
    task_id: str
    agent_id: str
    objective: str
    steps: list[PlanStep] = field(default_factory=list)
    estimated_total_usd: Decimal = Decimal("0")
    risk_estimate: float = 0.0     # 0..100
    risk_factors: tuple[str, ...] = ()
    approval_requests: tuple[str, ...] = ()   # step_ids needing approval
    created_at: datetime = field(default_factory=_utcnow)
    planner_kind: str = "rule-based-mvp"      # honest label

    def explain(self) -> str:
        lines = [f"Plan {self.plan_id} for task {self.task_id} "
                 f"(planner: {self.planner_kind})",
                 f"Objective: {self.objective}",
                 f"Est. total: ${self.estimated_total_usd} | risk: {self.risk_estimate:.0f}/100"]
        for i, s in enumerate(self.steps, 1):
            gate = " [APPROVAL]" if s.requires_approval else ""
            tool = s.tool_name or "NO-TOOL (escalate)"
            lines.append(f"  {i}. {s.name} → {tool}{gate} "
                         f"(~${s.estimated_cost_usd}, {s.risk_tier})")
        return "\n".join(lines)


# Keyword → (tool, argument builder, description) rules. Order matters.
# The builder receives the regex match (or None) and the raw objective.
_RuleBuilder = Callable[[Any, str], dict[str, Any]]
_RULES: tuple[tuple[str, str | None, _RuleBuilder, str], ...] = (
    (r"\b(research|investigate|find out|look up|what is|who is)\b", "knowledge.search",
     lambda m, obj: {"query": obj, "top_k": 5}, "Research the objective"),
    (r"\b(find|search|lookup)\b.*\b(contact|customer|client|lead)s?\b", "crm.search_contacts",
     lambda m, obj: {"query": _noun_phrase(obj)}, "Find the contact/customer"),
    (r"\b(contact|customer|client)s?\b.*\b(find|search|lookup)\b", "crm.search_contacts",
     lambda m, obj: {"query": _noun_phrase(obj)}, "Find the contact/customer"),
    (r"\bemail\b", "comms.send_email",
     lambda m, obj: {"to": "", "subject": _short(obj), "body": ""},
     "Draft/send the email"),
    (r"\b(sms|text message)\b", "comms.send_bulk_sms",
     lambda m, obj: {"to": [], "body": _short(obj)}, "Send the SMS batch"),
    (r"\b(schedule|meeting|calendar|appointment|book)\b", None,
     lambda m, obj: {}, "Scheduling needs confirmation (no scheduling tool bound)"),
    (r"\b(opportunity|deal|pipeline)\b", "crm.create_opportunity",
     lambda m, obj: {"name": _short(obj)}, "Create the opportunity"),
    (r"\b(remember|note|store)\b", "memory.store",
     lambda m, obj: {"namespace": "episodic", "key": _short(obj), "value": {}},
     "Store the memory"),
    (r"\b(task|todo|follow[- ]?up|remind)\b", "tasks.create_task",
     lambda m, obj: {"title": _short(obj)}, "Create the follow-up task"),
)


def _noun_phrase(obj: str) -> str:
    words = re.sub(r"[^\w\s]", "", obj).split()
    stop = {"find", "search", "lookup", "the", "a", "an", "for", "me", "about", "all",
            "contact", "contacts", "customer", "customers", "client", "clients"}
    kept = [w for w in words if w.lower() not in stop]
    return " ".join(kept[:6]) or obj[:60]


def _short(obj: str) -> str:
    return (obj[:80] + "…") if len(obj) > 80 else obj


class Planner:
    """Rule-based MVP planner. Deterministic; every step is inspectable."""

    def __init__(self, *, tool_registry: contracts.ToolRegistry,
                 risk_scorer: contracts.RiskScorer | None = None) -> None:
        self._tools = tool_registry
        self._risk = risk_scorer
        self._plans: dict[str, Plan] = {}  # plan_id -> Plan (dev adapter)

    async def plan(self, tenant: contracts.TenantContext, *,
                   task_id: str, agent: Any, objective: str) -> Plan:
        agent_id = getattr(agent, "agent_id", "unknown")
        allowed = set(getattr(agent, "allowed_tools", ()) or ())
        catalog = {t.name: t for t in await self._tools.list(tenant)}

        steps: list[PlanStep] = []
        matched = False
        for pattern, tool_name, arg_builder, desc in _RULES:
            if not re.search(pattern, objective, re.IGNORECASE):
                continue
            matched = True
            if tool_name is None or tool_name not in allowed or tool_name not in catalog:
                steps.append(PlanStep(
                    step_id=f"s-{len(steps)+1}", name=desc,
                    description=f"No capable/authorized tool for: {objective[:120]}",
                    tool_name=None, arguments={},
                    estimated_cost_usd=Decimal("0"), risk_tier="low",
                    requires_approval=True,
                    approval_reason="no capable tool — human must decide"))
                continue
            spec = catalog[tool_name]
            args = arg_builder(re.search(pattern, objective, re.IGNORECASE), objective)
            cost = self._tool_cost(tool_name)
            needs_approval = (spec.risk_tier in ("high", "critical")
                              or cost >= APPROVAL_COST_THRESHOLD_USD)
            steps.append(PlanStep(
                step_id=f"s-{len(steps)+1}", name=desc,
                description=f"{desc} via {tool_name}",
                tool_name=tool_name, arguments=args,
                estimated_cost_usd=cost + STEP_OVERHEAD_USD,
                risk_tier=spec.risk_tier, requires_approval=needs_approval,
                max_retries=2,
                fallback_tool=("knowledge.search" if tool_name.startswith("crm.")
                               else None),
                approval_reason=(f"risk_tier={spec.risk_tier}" if needs_approval else "")))

        if not matched:
            steps.append(PlanStep(
                step_id="s-1", name="Clarify objective",
                description="Objective matched no planning rule — human must clarify or "
                            "approve a manual step.",
                tool_name=None, arguments={}, estimated_cost_usd=Decimal("0"),
                risk_tier="low", requires_approval=True,
                approval_reason="unplannable objective"))

        total = sum((s.estimated_cost_usd for s in steps), Decimal("0"))
        risk, factors = await self._estimate_risk(tenant, objective, steps)
        plan = Plan(plan_id=f"plan-{uuid.uuid4().hex[:12]}", task_id=task_id,
                    agent_id=agent_id, objective=objective, steps=steps,
                    estimated_total_usd=total, risk_estimate=risk,
                    risk_factors=factors,
                    approval_requests=tuple(s.step_id for s in steps
                                            if s.requires_approval))
        self._plans[plan.plan_id] = plan
        return plan

    async def get(self, plan_id: str) -> Plan | None:
        return self._plans.get(plan_id)

    def validate(self, plan: Plan) -> list[str]:
        """Static checks: steps reference tools, args are objects, costs sane."""
        errors: list[str] = []
        if not plan.steps:
            errors.append("plan has no steps")
        for s in plan.steps:
            if s.tool_name is None and not s.requires_approval:
                errors.append(f"step {s.step_id}: no tool and no approval gate")
            if not isinstance(s.arguments, dict):
                errors.append(f"step {s.step_id}: arguments must be an object")
            if s.estimated_cost_usd < 0:
                errors.append(f"step {s.step_id}: negative cost")
        return errors

    def recovery_for(self, step: PlanStep, error: str) -> PlanStep | None:
        """Failure recovery: fallback tool if declared, else None (= escalate)."""
        if step.fallback_tool:
            return PlanStep(
                step_id=f"{step.step_id}-r", name=f"{step.name} (recovery)",
                description=f"Fallback after failure: {error[:120]}",
                tool_name=step.fallback_tool, arguments=step.arguments,
                estimated_cost_usd=step.estimated_cost_usd,
                risk_tier="low", requires_approval=False, max_retries=1)
        return None

    # -- internals -----------------------------------------------------------
    def _tool_cost(self, tool_name: str) -> Decimal:
        # ASSUMED per-tool cost table (MVP). Production: billing dimensions.
        table = {
            "knowledge.search": Decimal("0.005"), "crm.search_contacts": Decimal("0.001"),
            "crm.create_contact": Decimal("0.002"), "crm.create_opportunity": Decimal("0.002"),
            "crm.log_activity": Decimal("0.001"), "tasks.create_task": Decimal("0.001"),
            "tasks.transition_task": Decimal("0.001"),
            "workflows.start_execution": Decimal("0.01"),
            "comms.send_email": Decimal("0.02"), "comms.send_bulk_sms": Decimal("0.05"),
            "memory.store": Decimal("0.0005"), "memory.recall": Decimal("0.0005"),
        }
        return table.get(tool_name, Decimal("0.01"))

    async def _estimate_risk(self, tenant: contracts.TenantContext, objective: str,
                             steps: list[PlanStep]) -> tuple[float, tuple[str, ...]]:
        if self._risk is not None:
            req = contracts.ActionRequest(
                tenant=tenant, action="plan.proposed", args={"objective": objective},
                risk_context={"steps": [s.tool_name for s in steps]})
            score = await self._risk.score(req)
            return score.score, score.factors
        # Deterministic heuristic floor (pattern: rules set the floor — ACP).
        floor = {"low": 5.0, "medium": 20.0, "high": 45.0, "critical": 70.0}
        factors: list[str] = []
        fallback_score = 0.0
        for s in steps:
            f = floor.get(s.risk_tier, 5.0)
            if f > fallback_score:
                fallback_score = f
                factors = [f"highest step risk tier: {s.risk_tier} ({s.tool_name})"]
        if any(s.tool_name is None for s in steps):
            fallback_score = max(fallback_score, 30.0)
            factors.append("unplannable step requires human")
        return min(fallback_score, 100.0), tuple(factors)
