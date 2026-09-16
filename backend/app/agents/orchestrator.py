"""Orchestrator: the governed AI-native loop as code.

GOAL → CONTEXT → PLAN → POLICY CHECK → TOOL SELECTION → EXECUTION
→ OBSERVATION → FEEDBACK → LEARNING

Task state machine (binding, ARCHITECTURE.md §6):
  PENDING → PLANNING → WAITING_APPROVAL → EXECUTING → BLOCKED
  → COMPLETED | FAILED, plus CANCELLED from any non-terminal state.

Every transition is recorded in the task store AND mirrored as an AGRL event.
State is persisted after every step — runs are resumable after crash/restart.

The policy check is non-bypassable: plan-level evaluation happens before
EXECUTING, and every tool call is re-evaluated inside ToolExecutor.
"""

from __future__ import annotations

import asyncio
import builtins
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from app.agents.planner import Plan, PlanStep
from app.agents.router import RouteRequest
from app.agents.tools import ToolExecutor, new_grant_id
from app.core import contracts


class _AgrlEvents:
    """Local AGRL event names (``core/contracts.py`` is the only cross-package
    seam). These strings intentionally match ``EventTypes`` in
    ``app.memory.agrl.ledger`` so AGRL projections fold both writers
    identically."""

    GOAL_CREATED = "goal.created"
    GOAL_UPDATED = "goal.updated"
    GOAL_REPRIORITIZED = "goal.reprioritized"
    GOAL_SUSPENDED = "goal.suspended"
    GOAL_RESUMED = "goal.resumed"
    GOAL_COMPLETED = "goal.completed"
    CONSTRAINT_ADDED = "constraint.added"
    CONSTRAINT_REMOVED = "constraint.removed"
    RESOURCE_REGISTERED = "resource.registered"
    RESOURCE_ALLOCATED = "resource.allocated"
    RESOURCE_RELEASED = "resource.released"
    PLAN_PROPOSED = "plan.proposed"
    PLAN_APPROVED = "plan.approved"
    OUTCOME_RECORDED = "outcome.recorded"
    LEARNING_APPLIED = "learning.applied"
    TASK_CREATED = "task.created"
    TASK_TRANSITIONED = "task.transitioned"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_DECIDED = "approval.decided"


def _utcnow() -> datetime:
    return datetime.now(UTC)


# Planning-assist model call ceiling. ASSUMED MVP default — production should
# make this a setting (AZIENDA_PLANNING_RESERVE_USD), not a constant.
PLANNING_RESERVE_USD = Decimal("0.25")

_RISK_TIER_SCORES: dict[str, float] = {
    "low": 20.0, "medium": 50.0, "high": 75.0, "critical": 95.0}


class TaskStatus(StrEnum):
    PENDING = "pending"
    PLANNING = "planning"
    WAITING_APPROVAL = "waiting_approval"
    EXECUTING = "executing"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}

TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING: {TaskStatus.PLANNING, TaskStatus.CANCELLED},
    TaskStatus.PLANNING: {TaskStatus.WAITING_APPROVAL, TaskStatus.EXECUTING,
                          TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.BLOCKED},
    TaskStatus.WAITING_APPROVAL: {TaskStatus.EXECUTING, TaskStatus.FAILED,
                                  TaskStatus.CANCELLED, TaskStatus.BLOCKED},
    TaskStatus.EXECUTING: {TaskStatus.WAITING_APPROVAL, TaskStatus.BLOCKED,
                           TaskStatus.COMPLETED, TaskStatus.FAILED,
                           TaskStatus.CANCELLED},
    TaskStatus.BLOCKED: {TaskStatus.EXECUTING, TaskStatus.FAILED,
                         TaskStatus.CANCELLED},
    TaskStatus.COMPLETED: set(),
    TaskStatus.FAILED: set(),
    TaskStatus.CANCELLED: set(),
}


def can_transition(from_status: TaskStatus, to_status: TaskStatus) -> bool:
    return to_status in TRANSITIONS.get(from_status, set())


class IllegalTransition(Exception):
    pass


@dataclass
class TaskRecord:
    task_id: str
    tenant_id: str
    title: str
    objective: str
    capability: str = ""                 # router capability hint
    status: TaskStatus = TaskStatus.PENDING
    agent_id: str | None = None
    goal_id: str | None = None
    plan_id: str | None = None
    step_results: builtins.list[dict[str, Any]] = field(default_factory=list)
    pending_approval_ids: builtins.list[str] = field(default_factory=list)
    policy_decisions: builtins.list[dict[str, Any]] = field(default_factory=list)
    cost_usd: Decimal = Decimal("0")
    retries: int = 0
    error: str | None = None
    note: str = ""
    created_by: str = "system"
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)
    deadline_at: datetime | None = None


@dataclass
class TaskTransition:
    task_id: str
    from_status: TaskStatus
    to_status: TaskStatus
    actor: str
    note: str = ""
    at: datetime = field(default_factory=_utcnow)


@runtime_checkable
class TaskStore(Protocol):
    async def create(self, task: TaskRecord) -> TaskRecord: ...
    async def get(self, tenant_id: str, task_id: str) -> TaskRecord | None: ...
    async def save(self, task: TaskRecord) -> TaskRecord: ...
    async def list(self, tenant_id: str,
                   status: TaskStatus | None = None) -> builtins.list[TaskRecord]: ...
    async def transitions(self, tenant_id: str,
                          task_id: str) -> builtins.list[TaskTransition]: ...
    async def record_transition(self, tenant_id: str,
                                transition: TaskTransition) -> None: ...


class InMemoryTaskStore:
    """In-memory TaskStore (dev/test adapter). Production: SQLAlchemy repo."""

    def __init__(self) -> None:
        self._tasks: dict[tuple[str, str], TaskRecord] = {}
        self._transitions: dict[tuple[str, str], builtins.list[TaskTransition]] = {}

    async def create(self, task: TaskRecord) -> TaskRecord:
        self._tasks[(task.tenant_id, task.task_id)] = task
        return task

    async def get(self, tenant_id: str, task_id: str) -> TaskRecord | None:
        return self._tasks.get((tenant_id, task_id))

    async def save(self, task: TaskRecord) -> TaskRecord:
        task.updated_at = _utcnow()
        self._tasks[(task.tenant_id, task.task_id)] = task
        return task

    async def list(self, tenant_id: str,
                   status: TaskStatus | None = None) -> builtins.list[TaskRecord]:
        tasks = [t for (tid, _), t in self._tasks.items() if tid == tenant_id]
        if status:
            tasks = [t for t in tasks if t.status == status]
        return sorted(tasks, key=lambda t: t.created_at)

    async def transitions(self, tenant_id: str,
                          task_id: str) -> builtins.list[TaskTransition]:
        return list(self._transitions.get((tenant_id, task_id), []))

    async def record_transition(self, tenant_id: str,
                                transition: TaskTransition) -> None:
        self._transitions.setdefault((tenant_id, task_id_key(transition)),
                                     []).append(transition)


def task_id_key(t: TaskTransition) -> str:
    return t.task_id


@dataclass
class OrchestratorDeps:
    """Everything the orchestrator consumes — all injected protocols."""

    policy: contracts.PolicyEngine
    approvals: contracts.ApprovalStore
    audit: contracts.AuditLedger
    budgets: contracts.BudgetEnforcer
    costs: contracts.CostRecorder
    registry: Any                        # AgentRegistry (duck-typed)
    planner: Any                         # Planner (duck-typed)
    agent_router: Any                    # AgentRouter (duck-typed)
    tool_registry: Any                   # ToolRegistry (duck-typed)
    executor: ToolExecutor
    models: contracts.ModelProvider
    agrl: Any                            # AGRLLedger (duck-typed)
    memory: contracts.MemoryStore
    knowledge: contracts.KnowledgeStore
    tasks: TaskStore
    # Tunables (ASSUMED defaults)
    retry_backoff_seconds: float = 1.0
    task_timeout_seconds: float = 600.0
    approval_cost_threshold_usd: Decimal = Decimal("5.00")


@dataclass
class RunOutcome:
    task_id: str
    status: TaskStatus
    steps_executed: int
    cost_usd: Decimal
    policy_decisions: builtins.list[dict[str, Any]]
    error: str | None = None


class Orchestrator:
    """Runs the governed task pipeline. All governance deps are injected."""

    def __init__(self, deps: OrchestratorDeps) -> None:
        self._d = deps

    # ------------------------------------------------------------------ API
    async def submit(self, tenant: contracts.TenantContext, *,
                     title: str, objective: str, capability: str = "",
                     goal_id: str | None = None,
                     created_by: str | None = None) -> TaskRecord:
        task = TaskRecord(
            task_id=f"task-{uuid.uuid4().hex[:12]}", tenant_id=tenant.tenant_id,
            title=title, objective=objective, capability=capability,
            goal_id=goal_id, created_by=created_by or tenant.user_id or "system",
            deadline_at=_utcnow() + timedelta(seconds=self._d.task_timeout_seconds))
        await self._d.tasks.create(task)
        await self._d.agrl.append(tenant, _AgrlEvents.TASK_CREATED, task.task_id,
                                  {"title": title, "objective": objective,
                                   "capability": capability, "goal_id": goal_id},
                                  task.created_by)
        return task

    async def run_task(self, tenant: contracts.TenantContext,
                       task_id: str) -> RunOutcome:
        """Full pipeline run. Idempotent-safe: refuses to run terminal tasks."""
        task = await self._require_task(tenant, task_id)
        if task.status in TERMINAL:
            raise IllegalTransition(f"task {task_id} is terminal ({task.status})")
        if task.status not in (TaskStatus.PENDING, TaskStatus.PLANNING):
            # resume semantics for non-terminal states
            return await self.resume(tenant, task_id)

        d = self._d
        # ---- GOAL: link persistent goal -------------------------------------
        await self._transition(tenant, task, TaskStatus.PLANNING, "agent",
                               "pipeline started")
        if task.goal_id is None:
            task.goal_id = await d.agrl.create_goal(
                tenant, title=task.title,
                description=f"Agent task: {task.objective[:200]}",
                actor=task.created_by)
            await d.tasks.save(task)

        # ---- CONTEXT: memory + knowledge (best effort, read-only) -----------
        context_notes: builtins.list[str] = []
        try:
            hits = await d.knowledge.search(tenant, task.objective, top_k=3)
            context_notes = [h.text[:200] for h in hits]
        except Exception:
            context_notes = []
        # One model call per run for planning assistance (metered).
        # ---- RESERVE-BEFORE-SPEND (planning): the planning-assist model call
        # is bounded by PLANNING_RESERVE_USD and reserved BEFORE it happens.
        planning_reservation = await d.budgets.reserve(
            tenant, PLANNING_RESERVE_USD, purpose=f"task:{task.task_id}:planning")
        if not planning_reservation.allowed:
            await self._transition(tenant, task, TaskStatus.BLOCKED, "orchestrator",
                                   f"budget: {planning_reservation.reason}")
            await d.tasks.save(task)
            return RunOutcome(task_id=task.task_id, status=task.status,
                              steps_executed=0, cost_usd=task.cost_usd,
                              policy_decisions=task.policy_decisions,
                              error=planning_reservation.reason)

        # ---- MODEL ASSIST (governed + metered) ------------------------------------
        model_resp = await d.models.complete(contracts.ModelRequest(
            tenant=tenant, model=None,
            messages=[{"role": "system",
                       "content": "You assist a business agent workforce planner."},
                      {"role": "user",
                       "content": f"Objective: {task.objective}\n"
                                  f"Context: {' | '.join(context_notes)[:500]}"}],
            metadata={"task_id": task.task_id, "purpose": "planning-assist"}))
        task.cost_usd += model_resp.cost_usd
        await d.budgets.settle(
            tenant, getattr(planning_reservation, "reservation_id", ""),
            model_resp.cost_usd)

        # ---- TOOL SELECTION (agent): route -----------------------------------
        capability = task.capability or self._infer_capability(task.objective)
        agent = await d.agent_router.route(tenant, RouteRequest(capability=capability))
        task.agent_id = agent.agent_id

        # ---- PLAN --------------------------------------------------------------
        agent_rec = await d.registry.get_record(tenant, agent.agent_id)
        plan: Plan = await d.planner.plan(tenant, task_id=task.task_id,
                                          agent=agent_rec, objective=task.objective)
        plan_errors = d.planner.validate(plan)
        if plan_errors:
            return await self._fail(tenant, task, "plan_invalid: " + "; ".join(plan_errors))
        task.plan_id = plan.plan_id
        await d.tasks.save(task)
        await d.agrl.append(tenant, _AgrlEvents.PLAN_PROPOSED, task.task_id,
                            {"plan_id": plan.plan_id, "agent_id": agent.agent_id,
                             "steps": [{"step_id": s.step_id, "tool": s.tool_name,
                                        "cost": str(s.estimated_cost_usd),
                                        "approval": s.requires_approval}
                                       for s in plan.steps],
                             "estimated_total_usd": str(plan.estimated_total_usd),
                             "risk": plan.risk_estimate}, "orchestrator")

        # ---- POLICY CHECK (plan-level, before execution) ------------------------
        gate = await self._policy_gate(tenant, task, plan)
        if gate == "denied":
            return await self._fail(tenant, task, task.error or "policy denied plan")
        if gate == "approval":
            await self._transition(tenant, task, TaskStatus.WAITING_APPROVAL,
                                   "orchestrator", "plan steps require approval")
            await d.tasks.save(task)
            return RunOutcome(task_id=task.task_id, status=task.status,
                              steps_executed=0, cost_usd=task.cost_usd,
                              policy_decisions=task.policy_decisions)

        # ---- BUDGET RESERVE (execution) --------------------------------------------
        # Planning spend is already reserved+settled above; this covers the plan.
        reservation = await d.budgets.reserve(
            tenant, plan.estimated_total_usd,
            purpose=f"task:{task.task_id}")
        if not reservation.allowed:
            await self._transition(tenant, task, TaskStatus.BLOCKED, "orchestrator",
                                   f"budget: {reservation.reason}")
            await d.tasks.save(task)
            return RunOutcome(task_id=task.task_id, status=task.status,
                              steps_executed=0, cost_usd=task.cost_usd,
                              policy_decisions=task.policy_decisions,
                              error=reservation.reason)

        # ---- EXECUTING -------------------------------------------------------------
        await self._transition(tenant, task, TaskStatus.EXECUTING, "orchestrator",
                               f"plan {plan.plan_id} approved by policy")
        outcome = await self._execute_plan(tenant, task, plan)

        # ---- OBSERVATION → FEEDBACK → LEARNING --------------------------------------
        await d.budgets.settle(tenant, getattr(reservation, "reservation_id", ""),
                               task.cost_usd)
        await d.agrl.append(
            tenant, _AgrlEvents.OUTCOME_RECORDED, task.task_id,
            {"status": task.status.value, "goal_id": task.goal_id,
             "plan_id": plan.plan_id, "agent_id": task.agent_id,
             "steps_executed": outcome.steps_executed,
             "retries": task.retries,
             "human_interventions": len(task.pending_approval_ids),
             "cost_usd": str(task.cost_usd),
             "policy_decisions": task.policy_decisions}, "orchestrator")
        # LEARNING (async in production; inline here, never blocks the outcome)
        await d.agrl.append(
            tenant, _AgrlEvents.LEARNING_APPLIED, task.task_id,
            {"lesson": "run recorded",
             "cost_per_outcome_usd": str(task.cost_usd),
             "policy_denies": sum(1 for p in task.policy_decisions
                                  if p.get("effect") == "deny")}, "orchestrator")
        return outcome

    async def resume(self, tenant: contracts.TenantContext,
                     task_id: str) -> RunOutcome:
        """Resume a WAITING_APPROVAL or BLOCKED task."""
        task = await self._require_task(tenant, task_id)
        d = self._d
        if task.status == TaskStatus.WAITING_APPROVAL:
            return await self._resume_after_approval(tenant, task)
        if task.status == TaskStatus.BLOCKED:
            await self._transition(tenant, task, TaskStatus.EXECUTING, "operator",
                                   "resumed from blocked")
            plan = await d.planner.get(task.plan_id) if task.plan_id else None
            if plan is None:
                return await self._fail(tenant, task, "plan not found on resume")
            # re-reserve budget for remaining steps
            remaining = [s for s in plan.steps
                         if s.step_id not in {r["step_id"] for r in task.step_results
                                               if r.get("ok")}]
            est = sum((s.estimated_cost_usd for s in remaining), Decimal("0"))
            reservation = await d.budgets.reserve(tenant, est, purpose=f"task:{task_id}:resume")
            if not reservation.allowed:
                await self._transition(tenant, task, TaskStatus.BLOCKED, "orchestrator",
                                       f"budget: {reservation.reason}")
                return RunOutcome(task_id=task_id, status=task.status, steps_executed=0,
                                  cost_usd=task.cost_usd,
                                  policy_decisions=task.policy_decisions,
                                  error=reservation.reason)
            outcome = await self._execute_plan(tenant, task, plan, resume=True)
            await d.budgets.settle(tenant, getattr(reservation, "reservation_id", ""),
                                   task.cost_usd)
            return outcome
        raise IllegalTransition(f"cannot resume task in {task.status}")

    async def cancel(self, tenant: contracts.TenantContext, task_id: str,
                     reason: str = "") -> TaskRecord:
        task = await self._require_task(tenant, task_id)
        if task.status in TERMINAL:
            raise IllegalTransition(f"task {task_id} is terminal")
        await self._transition(tenant, task, TaskStatus.CANCELLED,
                               tenant.user_id or "operator", reason or "cancelled")
        await self._d.tasks.save(task)
        return task

    async def escalate(self, tenant: contracts.TenantContext, task_id: str,
                       note: str) -> TaskRecord:
        task = await self._require_task(tenant, task_id)
        if task.status in TERMINAL:
            raise IllegalTransition(f"task {task_id} is terminal")
        await self._transition(tenant, task, TaskStatus.BLOCKED,
                               tenant.user_id or "operator", f"escalated: {note}")
        task.note = note
        await self._d.tasks.save(task)
        return task

    # ---------------------------------------------------------------- internals
    async def _require_task(self, tenant: contracts.TenantContext,
                            task_id: str) -> TaskRecord:
        task = await self._d.tasks.get(tenant.tenant_id, task_id)
        if task is None:
            raise KeyError(f"unknown task {task_id}")
        return task

    async def _transition(self, tenant: contracts.TenantContext, task: TaskRecord,
                          to: TaskStatus, actor: str, note: str = "") -> None:
        if not can_transition(task.status, to):
            raise IllegalTransition(f"{task.status.value} -> {to.value} not allowed")
        from_status = task.status
        task.status = to
        await self._d.tasks.save(task)
        await self._d.tasks.record_transition(
            tenant.tenant_id,
            TaskTransition(task_id=task.task_id, from_status=from_status,
                           to_status=to, actor=actor, note=note))
        await self._d.agrl.append(tenant, _AgrlEvents.TASK_TRANSITIONED, task.task_id,
                                  {"from": from_status.value, "to": to.value,
                                   "note": note}, actor)
        await self._d.audit.append(tenant, actor=actor, action="task.transitioned",
                                   payload={"task_id": task.task_id,
                                            "from": from_status.value,
                                            "to": to.value, "note": note})

    async def _fail(self, tenant: contracts.TenantContext, task: TaskRecord,
                    error: str) -> RunOutcome:
        task.error = error
        await self._transition(tenant, task, TaskStatus.FAILED, "orchestrator", error)
        await self._d.tasks.save(task)
        await self._d.agrl.append(tenant, _AgrlEvents.OUTCOME_RECORDED, task.task_id,
                                  {"status": "failed", "goal_id": task.goal_id,
                                   "error": error, "cost_usd": str(task.cost_usd)},
                                  "orchestrator")
        return RunOutcome(task_id=task.task_id, status=task.status,
                          steps_executed=len(task.step_results),
                          cost_usd=task.cost_usd,
                          policy_decisions=task.policy_decisions, error=error)

    def _infer_capability(self, objective: str) -> str:
        o = objective.lower()
        if any(k in o for k in ("lead", "prospect", "outreach", "deal")):
            return "lead.research"
        if any(k in o for k in ("support", "ticket", "complaint")):
            return "ticket.triage"
        if any(k in o for k in ("research", "analyze", "analysis", "report")):
            return "document.synthesis"
        if any(k in o for k in ("email", "inbox")):
            return "email.triage"
        if any(k in o for k in ("schedule", "meeting", "calendar")):
            return "scheduling.coordination"
        if any(k in o for k in ("invoice", "expense", "finance")):
            return "invoice.reading"
        if any(k in o for k in ("contact", "customer", "crm")):
            return "contact.lookup"
        return "task.management"

    async def _policy_gate(self, tenant: contracts.TenantContext,
                           task: TaskRecord, plan: Plan) -> str:
        """Plan-level policy check. Returns 'allow' | 'denied' | 'approval'.

        DENY on any step => whole plan denied (fail closed). REQUIRE_APPROVAL
        on any step => approval requests created, task waits.
        """
        d = self._d
        verdict = "allow"
        for step in plan.steps:
            if step.tool_name is None:
                # No capable tool — needs a human decision.
                no_tool_request = contracts.ActionRequest(
                    tenant=tenant, action=f"plan.no_tool:{step.step_id}",
                    risk_context={"step_id": step.step_id,
                                  "plan_id": plan.plan_id})
                no_tool_decision = contracts.PolicyDecision(
                    effect=contracts.PolicyEffect.REQUIRE_APPROVAL,
                    policy_id="planner-no-tool",
                    reasons=(f"no capable tool for step '{step.name}'",),
                    obligations={})
                approval = await d.approvals.request(
                    no_tool_decision, no_tool_request)
                task.pending_approval_ids.append(approval.approval_id)
                task.policy_decisions.append(
                    {"step": step.step_id, "effect": "require_approval",
                     "reasons": ["no capable tool"]})
                await d.agrl.append(tenant, _AgrlEvents.APPROVAL_REQUESTED,
                                    task.task_id,
                                    {"approval_id": approval.approval_id,
                                     "step_id": step.step_id,
                                     "reason": "no capable tool"}, "orchestrator")
                verdict = "approval"
                continue
            action_request = contracts.ActionRequest(
                tenant=tenant, action=f"tool.{step.tool_name}",
                args=step.arguments,
                risk_context={"risk_tier": step.risk_tier,
                              "plan_id": plan.plan_id,
                              "estimated_cost_usd": str(step.estimated_cost_usd)})
            decision = await d.policy.evaluate(action_request)
            task.policy_decisions.append(
                {"step": step.step_id, "action": f"tool.{step.tool_name}",
                 "effect": decision.effect.value,
                 "reasons": list(decision.reasons)})
            await d.audit.append(
                tenant, actor="orchestrator", action="plan.policy_decision",
                payload={"task_id": task.task_id, "step": step.step_id,
                         "effect": decision.effect.value,
                         "reasons": list(decision.reasons)})
            if decision.effect == contracts.PolicyEffect.DENY:
                task.error = ("policy_denied step "
                              f"{step.step_id}: {'; '.join(decision.reasons)}")
                return "denied"
            if decision.effect == contracts.PolicyEffect.REQUIRE_APPROVAL:
                approval = await d.approvals.request(
                    decision, action_request,
                    risk_score=_RISK_TIER_SCORES.get(step.risk_tier, 50.0),
                    risk_factors=(step.risk_tier,))
                task.pending_approval_ids.append(approval.approval_id)
                step.approval_reason = "; ".join(decision.reasons)
                await d.agrl.append(tenant, _AgrlEvents.APPROVAL_REQUESTED,
                                    task.task_id,
                                    {"approval_id": approval.approval_id,
                                     "step_id": step.step_id,
                                     "reasons": list(decision.reasons)},
                                    "orchestrator")
                verdict = "approval"
        await d.tasks.save(task)
        return verdict

    async def _resume_after_approval(self, tenant: contracts.TenantContext,
                                     task: TaskRecord) -> RunOutcome:
        d = self._d
        # Collect decisions for all pending approvals.
        for approval_id in list(task.pending_approval_ids):
            approval = await d.approvals.get(tenant, approval_id)
            if approval is None:
                continue
            await d.agrl.append(tenant, _AgrlEvents.APPROVAL_DECIDED, task.task_id,
                                {"approval_id": approval_id,
                                 "status": approval.status.value,
                                 "decided_by": approval.decided_by}, "orchestrator")
            if approval.status == contracts.ApprovalStatus.DENIED:
                return await self._fail(tenant, task,
                                        f"approval {approval_id} denied by "
                                        f"{approval.decided_by}")
            if approval.status == contracts.ApprovalStatus.EXPIRED:
                # Fail closed: approval timeout == DENY.
                return await self._fail(tenant, task,
                                        f"approval {approval_id} expired (fail closed)")
            if approval.status != contracts.ApprovalStatus.APPROVED:
                # Still pending — stay waiting.
                return RunOutcome(task_id=task.task_id, status=task.status,
                                  steps_executed=len(task.step_results),
                                  cost_usd=task.cost_usd,
                                  policy_decisions=task.policy_decisions)
        task.pending_approval_ids = []
        plan = await d.planner.get(task.plan_id) if task.plan_id else None
        if plan is None:
            return await self._fail(tenant, task, "plan not found on resume")
        await d.agrl.append(tenant, _AgrlEvents.PLAN_APPROVED, task.task_id,
                            {"plan_id": plan.plan_id}, "orchestrator")
        reservation = await d.budgets.reserve(tenant, plan.estimated_total_usd,
                                              purpose=f"task:{task.task_id}")
        if not reservation.allowed:
            await self._transition(tenant, task, TaskStatus.BLOCKED, "orchestrator",
                                   f"budget: {reservation.reason}")
            return RunOutcome(task_id=task.task_id, status=task.status,
                              steps_executed=0, cost_usd=task.cost_usd,
                              policy_decisions=task.policy_decisions,
                              error=reservation.reason)
        await self._transition(tenant, task, TaskStatus.EXECUTING, "orchestrator",
                               "approvals granted")
        outcome = await self._execute_plan(tenant, task, plan)
        await d.budgets.settle(tenant, getattr(reservation, "reservation_id", ""),
                               task.cost_usd)
        await d.agrl.append(tenant, _AgrlEvents.OUTCOME_RECORDED, task.task_id,
                            {"status": task.status.value, "goal_id": task.goal_id,
                             "steps_executed": outcome.steps_executed,
                             "cost_usd": str(task.cost_usd),
                             "policy_decisions": task.policy_decisions},
                            "orchestrator")
        return outcome

    async def _execute_plan(self, tenant: contracts.TenantContext,
                            task: TaskRecord, plan: Plan,
                            resume: bool = False) -> RunOutcome:
        d = self._d
        done = {r["step_id"] for r in task.step_results if r.get("ok")} \
            if resume else set()
        steps_executed = 0
        for step in plan.steps:
            if step.step_id in done:
                continue
            if task.deadline_at and _utcnow() > task.deadline_at:
                return await self._fail(tenant, task, "task_timeout")
            if step.tool_name is None:
                # Human-gated step with no tool — operator must resolve.
                await self._transition(tenant, task, TaskStatus.BLOCKED,
                                       "orchestrator",
                                       f"step {step.step_id} has no tool; awaiting human")
                return RunOutcome(task_id=task.task_id, status=task.status,
                                  steps_executed=steps_executed,
                                  cost_usd=task.cost_usd,
                                  policy_decisions=task.policy_decisions,
                                  error="no tool for step")
            result = await self._run_step(tenant, task, step)
            steps_executed += 1
            task.step_results.append({
                "step_id": step.step_id, "tool": step.tool_name,
                "ok": result.ok, "error": result.error,
                "output": result.output if result.ok else None,
                "cost_usd": str(result.cost_usd)})
            task.cost_usd += result.cost_usd
            await d.tasks.save(task)  # durable checkpoint per step
            if not result.ok:
                if result.error == "approval_required":
                    approval_id = (result.output or {}).get("approval_id")
                    if approval_id:
                        task.pending_approval_ids.append(approval_id)
                    await self._transition(tenant, task,
                                           TaskStatus.WAITING_APPROVAL,
                                           "orchestrator",
                                           f"step {step.step_id} needs approval")
                    await d.tasks.save(task)
                    return RunOutcome(task_id=task.task_id, status=task.status,
                                      steps_executed=steps_executed,
                                      cost_usd=task.cost_usd,
                                      policy_decisions=task.policy_decisions)
                # Retry with bounded backoff, then recovery, then BLOCKED/FAILED.
                recovered = await self._recover(tenant, task, plan, step,
                                                result.error or "unknown")
                if recovered is None:
                    await self._transition(tenant, task, TaskStatus.BLOCKED,
                                           "orchestrator",
                                           f"step {step.step_id} failed: {result.error}")
                    await d.tasks.save(task)
                    return RunOutcome(task_id=task.task_id, status=task.status,
                                      steps_executed=steps_executed,
                                      cost_usd=task.cost_usd,
                                      policy_decisions=task.policy_decisions,
                                      error=result.error)
        await self._transition(tenant, task, TaskStatus.COMPLETED, "orchestrator",
                               f"{steps_executed} steps executed")
        await d.tasks.save(task)
        return RunOutcome(task_id=task.task_id, status=task.status,
                          steps_executed=steps_executed, cost_usd=task.cost_usd,
                          policy_decisions=task.policy_decisions)

    async def _run_step(self, tenant: contracts.TenantContext,
                        task: TaskRecord, step: PlanStep) -> contracts.ToolResult:
        d = self._d
        last: contracts.ToolResult = contracts.ToolResult(ok=False,
                                                         error="not attempted")
        for attempt in range(step.max_retries + 1):
            call = contracts.ToolCall(
                tool_name=step.tool_name or "", arguments=step.arguments,
                grant_id=new_grant_id(),  # per-action scoped grant
                idempotency_key=f"{task.task_id}:{step.step_id}:{attempt}")
            last = await d.executor.execute(tenant, call)
            if last.ok or last.error == "approval_required":
                return last
            task.retries += 1
            if attempt < step.max_retries and self._d.retry_backoff_seconds > 0:
                await asyncio.sleep(self._d.retry_backoff_seconds * (2 ** attempt))
        return last

    async def _recover(self, tenant: contracts.TenantContext, task: TaskRecord,
                       plan: Plan, step: PlanStep,
                       error: str) -> PlanStep | None:
        recovery = self._d.planner.recovery_for(step, error)
        if recovery is None:
            return None
        await self._d.audit.append(
            tenant, actor="orchestrator", action="step.recovery",
            payload={"task_id": task.task_id, "step": step.step_id,
                     "fallback_tool": recovery.tool_name, "error": error[:200]})
        result = await self._run_step(tenant, task, recovery)
        task.step_results.append({
            "step_id": recovery.step_id, "tool": recovery.tool_name,
            "ok": result.ok, "error": result.error,
            "output": result.output if result.ok else None,
            "cost_usd": str(result.cost_usd)})
        task.cost_usd += result.cost_usd
        await self._d.tasks.save(task)
        return recovery if result.ok else None
