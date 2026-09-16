"""AGRL projections: current state derived by folding the event ledger.

Projections are DERIVED, never edited. ``project(projection, aggregate_id,
events)`` folds the immutable history into the requested view. Rebuildable at
any time — there is no separate write path.

Supported projections (``aggregate_id``):
- ``goal`` (a goal id) / ``goals`` (``"*"``) — goal states
- ``resource`` (a resource id) / ``resources`` (``"*"``) — resource states
- ``constraints`` (``"*"``) — constraints currently in force
- ``plan`` (a task/plan id) — latest proposed/approved plan
- ``outcome_summary`` (``"*"``) — outcome counts + total cost
- ``what_next`` (``"*"``) — the single next recommended action:
  highest-priority active, unblocked goal
"""

from __future__ import annotations

from typing import Any

from app.core import contracts
from app.memory.agrl.ledger import EventTypes as T


def project(projection: str, aggregate_id: str,
            events: list[contracts.AGRLEvent]) -> dict[str, Any]:
    if projection == "goal":
        return _fold_goal(aggregate_id, events)
    if projection == "goals":
        return {"goals": _fold_goals(events)}
    if projection == "resource":
        return _fold_resource(aggregate_id, events)
    if projection == "resources":
        return {"resources": _fold_resources(events)}
    if projection == "constraints":
        return {"constraints": _fold_constraints(events)}
    if projection == "plan":
        return _fold_plan(aggregate_id, events)
    if projection == "outcome_summary":
        return _fold_outcomes(events)
    if projection == "what_next":
        return _what_next(events)
    raise ValueError(f"unknown projection: {projection}")


# ---------------------------------------------------------------- goals
_GOAL_UPDATERS = {
    T.GOAL_CREATED: lambda s, p: {**s, **{k: p[k] for k in
        ("title", "description", "priority", "owner_id", "parent_goal_id",
         "target_date") if k in p},
        "status": p.get("status", "active"),
        "constraints": list(p.get("constraints", []))},
    T.GOAL_UPDATED: lambda s, p: {**s, **{k: p[k] for k in
        ("title", "description", "owner_id", "target_date") if k in p}},
    T.GOAL_REPRIORITIZED: lambda s, p: {**s, "priority": p.get("priority", s.get("priority"))},
    T.GOAL_SUSPENDED: lambda s, p: {**s, "status": "suspended",
                                    "suspend_reason": p.get("reason", "")},
    T.GOAL_RESUMED: lambda s, p: {**s, "status": "active"},
    T.GOAL_COMPLETED: lambda s, p: {**s, "status": "completed",
                                    "completed_at": p.get("completed_at", "")},
}


def _fold_goal(goal_id: str, events: list[contracts.AGRLEvent]) -> dict[str, Any]:
    state: dict[str, Any] = {"goal_id": goal_id, "status": "unknown", "priority": 0.0,
                             "constraints": [], "history": []}
    for e in sorted(events, key=lambda e: e.seq):
        if e.aggregate_id != goal_id:
            continue
        updater = _GOAL_UPDATERS.get(e.event_type)
        if updater:
            state = updater(state, e.payload)
        if e.event_type == T.CONSTRAINT_ADDED and e.payload.get("goal_id") == goal_id:
            state["constraints"] = sorted(set(state["constraints"]) | {e.payload["constraint"]})
        if e.event_type == T.CONSTRAINT_REMOVED and e.payload.get("goal_id") == goal_id:
            state["constraints"] = [c for c in state["constraints"]
                                    if c != e.payload["constraint"]]
        state["history"].append({"seq": e.seq, "type": e.event_type,
                                 "at": e.occurred_at.isoformat()})
    state["at_seq"] = max((e.seq for e in events if e.aggregate_id == goal_id), default=0)
    return state


def _fold_goals(events: list[contracts.AGRLEvent]) -> list[dict[str, Any]]:
    ids = {e.aggregate_id for e in events
           if e.event_type in _GOAL_UPDATERS and e.aggregate_id.startswith("goal-")}
    return [_fold_goal(g, events) for g in sorted(ids)]


# ---------------------------------------------------------------- resources
def _fold_resource(res_id: str, events: list[contracts.AGRLEvent]) -> dict[str, Any]:
    state: dict[str, Any] = {"resource_id": res_id, "allocated": 0.0,
                             "allocations": []}
    for e in sorted(events, key=lambda e: e.seq):
        if e.aggregate_id != res_id:
            continue
        p = e.payload
        if e.event_type == T.RESOURCE_REGISTERED:
            state.update({"resource_type": p.get("resource_type"),
                          "resource_ref": p.get("resource_ref"),
                          "amount": p.get("amount"), "unit": p.get("unit")})
        elif e.event_type == T.RESOURCE_ALLOCATED:
            state["allocated"] = float(state["allocated"]) + float(p.get("amount", 0))
            state["allocations"].append({"goal_id": p.get("goal_id"),
                                         "amount": p.get("amount"),
                                         "seq": e.seq})
        elif e.event_type == T.RESOURCE_RELEASED:
            state["allocated"] = max(
                0.0, float(state["allocated"]) - float(p.get("amount", 0)))
    total = float(state.get("amount") or 0)
    state["available"] = total - float(state["allocated"])
    return state


def _fold_resources(events: list[contracts.AGRLEvent]) -> list[dict[str, Any]]:
    ids = {e.aggregate_id for e in events
           if e.event_type == T.RESOURCE_REGISTERED}
    return [_fold_resource(r, events) for r in sorted(ids)]


# ---------------------------------------------------------------- constraints
def _fold_constraints(events: list[contracts.AGRLEvent]) -> list[dict[str, Any]]:
    active: dict[str, dict[str, Any]] = {}
    for e in sorted(events, key=lambda e: e.seq):
        p = e.payload
        if e.event_type == T.CONSTRAINT_ADDED:
            active[p["constraint"]] = {"constraint": p["constraint"],
                                       "goal_id": p.get("goal_id"),
                                       "added_at_seq": e.seq,
                                       "added_by": e.actor}
        elif e.event_type == T.CONSTRAINT_REMOVED:
            active.pop(p["constraint"], None)
    return list(active.values())


# ---------------------------------------------------------------- plans
def _fold_plan(task_or_plan_id: str, events: list[contracts.AGRLEvent]) -> dict[str, Any]:
    proposed: dict[str, Any] | None = None
    approved = False
    for e in sorted(events, key=lambda e: e.seq):
        p = e.payload
        key = p.get("plan_id") or p.get("task_id")
        if key != task_or_plan_id:
            continue
        if e.event_type == T.PLAN_PROPOSED:
            proposed = dict(p)
        elif e.event_type == T.PLAN_APPROVED:
            approved = True
    return {"plan": proposed, "approved": approved,
            "task_or_plan_id": task_or_plan_id}


# ---------------------------------------------------------------- outcomes
def _fold_outcomes(events: list[contracts.AGRLEvent]) -> dict[str, Any]:
    outcomes = [e for e in events if e.event_type == T.OUTCOME_RECORDED]
    by_status: dict[str, int] = {}
    total_cost = 0.0
    for e in outcomes:
        s = str(e.payload.get("status", "unknown"))
        by_status[s] = by_status.get(s, 0) + 1
        try:
            total_cost += float(e.payload.get("cost_usd") or 0)
        except (TypeError, ValueError):
            pass
    return {"count": len(outcomes), "by_status": by_status,
            "total_cost_usd": round(total_cost, 4),
            "latest": [{"seq": e.seq, "aggregate": e.aggregate_id,
                        "status": e.payload.get("status"),
                        "at": e.occurred_at.isoformat()}
                       for e in outcomes[-10:]]}


# ---------------------------------------------------------------- what_next
def _what_next(events: list[contracts.AGRLEvent]) -> dict[str, Any]:
    """Answer: what is the business trying to accomplish, and what is next?

    Picks the highest-priority ACTIVE goal that is not blocked by a
    ``blocks``/``conflicts_with`` relation recorded on the ledger.
    Goal links are recorded as outcome payloads with relation metadata
    (goal_links table in production).
    """
    goals = _fold_goals(events)
    active = [g for g in goals if g["status"] == "active"]
    if not active:
        return {"action": "none", "reason": "no active goals",
                "active_goal_count": 0}
    blocked: set[str] = set()
    for e in events:
        if e.event_type == T.OUTCOME_RECORDED:
            rel = (e.payload.get("relation") or "")
            if rel in ("blocks", "conflicts_with") and e.payload.get("status") == "open":
                blocked.add(str(e.payload.get("from_goal_id", "")))
    candidates = [g for g in active if g["goal_id"] not in blocked]
    pool = candidates or active
    top = max(pool, key=lambda g: float(g.get("priority") or 0))
    constraints = _fold_constraints(events)
    goal_constraints = [c for c in constraints if c.get("goal_id") == top["goal_id"]]
    return {
        "action": "advance_goal",
        "goal_id": top["goal_id"],
        "title": top.get("title"),
        "priority": top.get("priority"),
        "constraints_in_force": [c["constraint"] for c in goal_constraints],
        "suggested": (f"Advance goal '{top.get('title')}' "
                      f"(priority {top.get('priority')})"),
        "active_goal_count": len(active),
        "blocked_goal_count": len(blocked & {g["goal_id"] for g in active}),
    }
