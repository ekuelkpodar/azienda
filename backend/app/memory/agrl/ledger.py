"""AGRL — Adaptive Goal & Resource Ledger (the event-sourced substrate).

ONE ledger (ARCHITECTURE.md §9 consolidation rule): the orchestrator's event
store, the audit-adjacent history, and AGRL are a single ``agrl_events`` table.
This module is the in-memory MVP adapter; production appends to Postgres with
the same hash chain.

Invariants (binding):
- Append-only. Events are never edited or deleted.
- Hash-chained per tenant: hash = sha256(prev_hash || canonical(event)).
- Projections are DERIVED by folding events — never written by domain code.
- Only the AGRL writer mutates goal state; everyone else reads projections.

Event types (extensible; the canonical set):
  goal.created/updated/reprioritized/suspended/resumed/completed
  constraint.added/removed
  resource.registered/allocated/released
  plan.proposed/approved
  outcome.recorded
  learning.applied
  task.created/transitioned   (mirrored from the orchestrator)
  approval.requested/decided  (mirrored from governance)
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from app.core import contracts

GENESIS_HASH = "GENESIS" + "0" * 57  # 64-char chain start


def _utcnow() -> datetime:
    return datetime.now(UTC)


def canonical(obj: Any) -> str:
    """Deterministic serialization for hashing."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      default=str, ensure_ascii=False)


def chain_hash(prev_hash: str, event_type: str, aggregate_id: str,
               seq: int, payload: dict[str, Any], occurred_at: str) -> str:
    body = canonical({"prev": prev_hash, "type": event_type, "agg": aggregate_id,
                      "seq": seq, "payload": payload, "at": occurred_at})
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


# Canonical event types -------------------------------------------------------
class EventTypes:
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


class AGRLLedger:
    """In-memory AGRLLedger (dev/test adapter). Implements contracts.AGRLLedger."""

    def __init__(self) -> None:
        # tenant_id -> list[AGRLEvent] (insertion order == seq order)
        self._events: dict[str, list[contracts.AGRLEvent]] = {}

    # -- contracts.AGRLLedger ---------------------------------------------------
    async def append(self, tenant: contracts.TenantContext, event_type: str,
                     aggregate_id: str, payload: dict[str, Any],
                     actor: str) -> contracts.AGRLEvent:
        chain = self._events.setdefault(tenant.tenant_id, [])
        seq = len(chain) + 1
        prev_hash = chain[-1].hash if chain else GENESIS_HASH
        occurred_at = _utcnow()
        digest = chain_hash(prev_hash, event_type, aggregate_id, seq, payload,
                            occurred_at.isoformat())
        event = contracts.AGRLEvent(
            tenant_id=tenant.tenant_id, event_type=event_type,
            aggregate_id=aggregate_id, payload=dict(payload), seq=seq,
            prev_hash=prev_hash, hash=digest, actor=actor,
            occurred_at=occurred_at)
        chain.append(event)
        return event

    async def read(self, tenant: contracts.TenantContext, aggregate_id: str,
                   from_seq: int = 0) -> list[contracts.AGRLEvent]:
        return [e for e in self._events.get(tenant.tenant_id, [])
                if e.aggregate_id == aggregate_id and e.seq >= from_seq]

    async def read_all(self, tenant: contracts.TenantContext,
                       from_seq: int = 0) -> list[contracts.AGRLEvent]:
        """Full tenant chain (for rebuild / verify)."""
        return [e for e in self._events.get(tenant.tenant_id, [])
                if e.seq >= from_seq]

    async def get_projection(self, tenant: contracts.TenantContext,
                             projection: str,
                             aggregate_id: str) -> dict[str, Any]:
        from app.memory.agrl.projections import project  # lazy: avoid cycle
        events = await self.read_all(tenant)
        return project(projection, aggregate_id, events)

    # -- integrity ---------------------------------------------------------------
    async def verify_chain(self, tenant: contracts.TenantContext,
                           from_seq: int = 0) -> bool:
        chain = [e for e in self._events.get(tenant.tenant_id, [])
                 if e.seq >= from_seq]
        prev = GENESIS_HASH if from_seq <= 1 else None
        for e in chain:
            if prev is None:
                # resume point: trust the stored prev_hash of the first event
                prev = e.prev_hash
            if e.prev_hash != prev:
                return False
            expect = chain_hash(e.prev_hash, e.event_type, e.aggregate_id, e.seq,
                                e.payload, e.occurred_at.isoformat())
            if e.hash != expect:
                return False
            prev = e.hash
        return True

    # -- convenience writers ------------------------------------------------------
    async def create_goal(self, tenant: contracts.TenantContext, *,
                          title: str, description: str = "",
                          priority: float = 0.5, owner_id: str | None = None,
                          parent_goal_id: str | None = None,
                          constraints: list[str] | None = None,
                          target_date: str | None = None,
                          actor: str = "system") -> str:
        goal_id = f"goal-{uuid.uuid4().hex[:12]}"
        await self.append(tenant, EventTypes.GOAL_CREATED, goal_id,
                          {"title": title, "description": description,
                           "priority": priority, "owner_id": owner_id,
                           "parent_goal_id": parent_goal_id,
                           "constraints": constraints or [],
                           "target_date": target_date, "status": "active"},
                          actor)
        return goal_id

    async def register_resource(self, tenant: contracts.TenantContext, *,
                                resource_type: str, resource_ref: str,
                                amount: float, unit: str,
                                actor: str = "system") -> str:
        res_id = f"res-{uuid.uuid4().hex[:12]}"
        await self.append(tenant, EventTypes.RESOURCE_REGISTERED, res_id,
                          {"resource_type": resource_type,
                           "resource_ref": resource_ref,
                           "amount": amount, "unit": unit,
                           "allocated": 0.0}, actor)
        return res_id


def new_aggregate_id(prefix: str = "agg") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"
