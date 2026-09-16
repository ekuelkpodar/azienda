"""Router: command_center. Contract: /API.md §command_center.

Structure: domain services (``AttentionService``, ``IntentParser``) live here
because the Command Center's aggregation/parsing logic is owned by this
router's builder — the agents package's enumerated modules are
registry/orchestrator/planner/router/tools/models/mcp/eval only. HTTP
handlers below are thin: they resolve the tenant, call a service, shape the
response. Parsed intents are never executed here; execution always goes
through the normal governed path.

``IntentParser`` is RULE/KEYWORD-BASED (labeled ``rule-based-mvp``) — LLM
planning is the documented future.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core import contracts

router = APIRouter(prefix="/command-center", tags=["command_center"])

_services: dict[str, Any] | None = None


def configure_services(**services: Any) -> None:
    """Inject the domain service graph. Called once by the app factory."""
    global _services
    _services = dict(services)


def _svc(name: str) -> Any:
    if _services is None or name not in _services:
        raise HTTPException(status_code=500, detail={
            "error": {"code": "services_not_configured",
                      "message": f"service '{name}' is not configured",
                      "details": {}, "trace_id": ""}})
    return _services[name]


def _tenant() -> Any:
    raise HTTPException(status_code=501, detail={
        "error": {"code": "auth_not_wired",
                  "message": "tenant resolution not wired",
                  "details": {}, "trace_id": ""}})


def _utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Attention aggregation
# ---------------------------------------------------------------------------
@dataclass
class AttentionDeps:
    approvals: contracts.ApprovalStore
    tasks: Any                       # TaskStore (duck-typed)
    budgets: contracts.BudgetEnforcer
    audit: contracts.AuditLedger
    agrl: Any                        # AGRLLedger (duck-typed)
    policy_decisions: list[dict[str, Any]] | None = None  # recent, for anomalies


def _item(kind: str, severity: str, title: str, detail: str,
          links: dict[str, str], occurred_at: str) -> dict[str, Any]:
    return {"kind": kind, "severity": severity, "title": title,
            "detail": detail, "links": links, "occurred_at": occurred_at}


class AttentionService:
    """Aggregates 'what needs my attention' across governance + execution.

    Every item is evidence-linked (ids that resolve to the underlying record).
    """

    def __init__(self, deps: AttentionDeps) -> None:
        self._d = deps

    async def attention(self, tenant: contracts.TenantContext,
                        limit_per_section: int = 10) -> dict[str, Any]:
        sections: dict[str, list[dict[str, Any]]] = {
            "pending_approvals": [], "failed_tasks": [], "blocked_tasks": [],
            "overdue_tasks": [], "budget_alerts": [], "anomalies": [],
            "recent_outcomes": [],
        }
        now = _utcnow()

        approvals = await self.pending_approvals(tenant)
        for ap in approvals[:limit_per_section]:
            action = ap.action.action if hasattr(ap.action, "action") else str(ap.action)
            sections["pending_approvals"].append(_item(
                "approval", "high", f"Approval needed: {action}",
                f"Requested by {ap.requested_by}.",
                {"approval_id": ap.approval_id},
                ap.expires_at.isoformat() if hasattr(ap.expires_at, "isoformat")
                else ""))

        tasks = await self._d.tasks.list(tenant.tenant_id)
        for t in tasks:
            status = t.status.value if hasattr(t.status, "value") else str(t.status)
            if status == "failed":
                sections["failed_tasks"].append(_item(
                    "failed_task", "high", f"Task failed: {t.title}",
                    (t.error or "")[:200], {"task_id": t.task_id},
                    t.updated_at.isoformat()))
            elif status == "blocked":
                sections["blocked_tasks"].append(_item(
                    "blocked_task", "high", f"Task blocked: {t.title}",
                    (t.note or t.error or "")[:200], {"task_id": t.task_id},
                    t.updated_at.isoformat()))
            elif status not in ("completed", "failed", "cancelled") and t.deadline_at \
                    and t.deadline_at < now:
                sections["overdue_tasks"].append(_item(
                    "overdue_task", "medium", f"Task overdue: {t.title}",
                    f"deadline was {t.deadline_at.isoformat()}",
                    {"task_id": t.task_id}, t.updated_at.isoformat()))

        if await self._budget_frozen(tenant):
            sections["budget_alerts"].append(_item(
                "budget_alert", "critical", "Agent spend is FROZEN (kill switch)",
                "New agent work cannot start until the freeze is released.",
                {}, now.isoformat()))

        decisions = self._d.policy_decisions or []
        if decisions:
            denies = sum(1 for d_ in decisions if d_.get("effect") == "deny")
            rate = denies / len(decisions)
            if rate > 0.3 and len(decisions) >= 5:
                sections["anomalies"].append(_item(
                    "anomaly", "medium",
                    f"Policy deny rate spike: {rate:.0%} ({denies}/{len(decisions)})",
                    "Unusually many actions denied — review policy or agent behavior.",
                    {}, now.isoformat()))

        summary = await self._d.agrl.get_projection(tenant, "outcome_summary", "*")
        for o in summary.get("latest", [])[-limit_per_section:]:
            sections["recent_outcomes"].append(_item(
                "outcome", "low", f"Outcome: {o.get('status')} ({o.get('aggregate')})",
                "", {"task_id": str(o.get("aggregate", ""))}, str(o.get("at", ""))))

        total = sum(len(v) for v in sections.values())
        return {"sections": {k: v[:limit_per_section] for k, v in sections.items()},
                "total_items": total,
                "generated_at": now.isoformat(),
                "sources": ["approvals", "tasks", "budgets", "policy", "agrl"]}

    async def pending_approvals(self, tenant: contracts.TenantContext) -> list[Any]:
        """Public: pending approvals for this tenant (used by /summary too)."""
        return await self._pending_approvals(tenant)

    async def _pending_approvals(self, tenant: contracts.TenantContext) -> list[Any]:
        fn = getattr(self._d.approvals, "list_pending", None)
        if callable(fn):
            result = await fn(tenant)
            # list_pending returns (items, total); tolerate a bare list too.
            items = result[0] if isinstance(result, tuple) else result
            return list(items)
        return []

    async def _budget_frozen(self, tenant: contracts.TenantContext) -> bool:
        fn = getattr(self._d.budgets, "is_frozen", None)
        if callable(fn):
            return bool(await fn(tenant))
        return False


# ---------------------------------------------------------------------------
# NL intent parser (RULE/KEYWORD-BASED MVP — labeled; LLM planning = future)
# ---------------------------------------------------------------------------
@dataclass
class ParsedIntent:
    operation: str                   # e.g. "list_approvals" | "unknown"
    params: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    policy_note: str = ""
    parser_kind: str = "rule-based-mvp"   # honest label


_INTENT_RULES: tuple[tuple[str, str, float, str], ...] = (
    (r"\b(what needs (my |attention)|show (me )?(urgent|attention)|attention)\b",
     "get_attention", 0.9, "Read-only aggregation."),
    (r"\b(pending |open )?approvals?\b", "list_approvals", 0.85,
     "Read-only; deciding an approval is a separate governed action."),
    (r"\bcreate (a )?task\b(?P<rest>.*)", "create_task", 0.8,
     "Task creation is idempotent and policy-checked at delegation."),
    (r"\b(pause|suspend) agent (?P<agent>[\w-]+)", "pause_agent", 0.85,
     "Lifecycle change; requires admin role."),
    (r"\b(resume|unpause) agent (?P<agent>[\w-]+)", "resume_agent", 0.85,
     "Lifecycle change; requires admin role."),
    (r"\b(how much|spend|cost).*\b", "cost_summary", 0.75,
     "Read-only cost aggregation."),
    (r"\bfailed (tasks|runs)\b", "list_failed_tasks", 0.85, "Read-only."),
    (r"\bcancel task (?P<task>[\w-]+)", "cancel_task", 0.8,
     "Cancellation is terminal; policy-checked."),
    (r"\b(goals?|objectives?)\b", "list_goals", 0.8,
     "Read-only AGRL projection."),
    (r"\bwhat.?s next\b", "what_next", 0.85, "Read-only AGRL projection."),
)


class IntentParser:
    """Keyword/rule intent parser. Deterministic; unknown input => 'unknown'."""

    def parse(self, text: str) -> ParsedIntent:
        cleaned = text.strip().lower()
        for pattern, operation, confidence, policy_note in _INTENT_RULES:
            m = re.search(pattern, cleaned)
            if not m:
                continue
            params: dict[str, Any] = {}
            gd = m.groupdict()
            if "rest" in gd and gd["rest"]:
                params["title"] = gd["rest"].strip(" :.-")[:200]
            if "agent" in gd and gd["agent"]:
                params["agent_id"] = gd["agent"]
            if "task" in gd and gd["task"]:
                params["task_id"] = gd["task"]
            return ParsedIntent(operation=operation, params=params,
                                confidence=confidence, policy_note=policy_note)
        return ParsedIntent(
            operation="unknown", confidence=0.0,
            policy_note="No intent matched. Suggestions: 'what needs my attention', "
                        "'show pending approvals', 'create task <title>'.")


# ---------------------------------------------------------------------------
# HTTP layer (thin)
# ---------------------------------------------------------------------------
class IntentBody(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@router.get("/summary")
async def summary(tenant: Any = Depends(_tenant)) -> dict[str, Any]:
    tasks = _svc("tasks")
    agrl = _svc("agrl")
    goals = await agrl.get_projection(tenant, "goals", "*")
    active_goals = [g for g in goals["goals"] if g["status"] == "active"]
    all_tasks = await tasks.list(tenant.tenant_id)
    running = [t for t in all_tasks
               if t.status.value in ("executing", "planning", "waiting_approval")]
    outcomes = await agrl.get_projection(tenant, "outcome_summary", "*")
    return {
        "active_goals": len(active_goals),
        "running_tasks": len(running),
        "pending_approvals": len(await _svc("attention").pending_approvals(tenant)),
        "outcomes_30d": outcomes["count"],
        "total_outcome_cost_usd": outcomes["total_cost_usd"],
    }


@router.get("/goals")
async def goals(status: str | None = Query(default=None),
                tenant: Any = Depends(_tenant)) -> dict[str, Any]:
    agrl = _svc("agrl")
    proj = await agrl.get_projection(tenant, "goals", "*")
    items = proj["goals"]
    if status:
        items = [g for g in items if g["status"] == status]
    return {"items": items, "next_page_token": None}


@router.get("/outcomes")
async def outcomes(tenant: Any = Depends(_tenant)) -> dict[str, Any]:
    agrl = _svc("agrl")
    proj = await agrl.get_projection(tenant, "outcome_summary", "*")
    return dict(proj)


@router.get("/costs")
async def costs(tenant: Any = Depends(_tenant)) -> dict[str, Any]:
    entries: list[dict[str, Any]] = await _svc("cost_ledger")(tenant)
    by_model: dict[str, dict[str, Any]] = {}
    total = 0.0
    for e in entries:
        m = e.get("model", "unknown")
        c = float(e.get("cost_usd", 0))
        total += c
        slot = by_model.setdefault(m, {"model": m, "cost_usd": 0.0, "calls": 0})
        slot["cost_usd"] = round(slot["cost_usd"] + c, 6)
        slot["calls"] += 1
    return {"total_cost_usd": round(total, 6),
            "by_model": sorted(by_model.values(),
                               key=lambda s: s["cost_usd"], reverse=True)}


@router.get("/policy-activity")
async def policy_activity(tenant: Any = Depends(_tenant)) -> dict[str, Any]:
    source = _svc("policy_decisions")
    items: list[dict[str, Any]] = source(tenant) if callable(source) else source
    if hasattr(items, "__await__"):
        items = await items
    counts: dict[str, int] = {}
    for d in items:
        e = d.get("effect", "unknown")
        counts[e] = counts.get(e, 0) + 1
    return {"total": len(items), "by_effect": counts, "recent": items[-50:]}


@router.get("/attention")
async def attention(limit: int = Query(default=10, ge=1, le=50),
                    tenant: Any = Depends(_tenant)) -> dict[str, Any]:
    """'What needs my attention' — aggregated, evidence-linked."""
    service: AttentionService = _svc("attention")
    return await service.attention(tenant, limit_per_section=limit)


@router.post("/intent")
async def parse_intent(body: IntentBody,
                       tenant: Any = Depends(_tenant)) -> dict[str, Any]:
    """NL → governed-operation intent (rule/keyword-based MVP, labeled).

    Returns the parsed intent only — execution goes through the normal
    governed path and is NOT performed here.
    """
    parser: IntentParser = _svc("intent_parser")
    intent = parser.parse(body.text)
    return {"operation": intent.operation, "params": intent.params,
            "confidence": intent.confidence, "policy_note": intent.policy_note,
            "parser": intent.parser_kind,
            "note": "Intent is parsed only; execution requires the governed path."}
