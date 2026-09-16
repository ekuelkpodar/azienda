"""Durable, resumable workflow execution engine.

The runner drives a published DAG version node-by-node, persisting resumable
state after every step. It is RE-ENTRANT: ``drive()`` can be called any number
of times (after a crash, after an approval signal) and continues from the
stored ``current`` node. All progress is recorded in execution_events.

Node semantics:
- trigger: seeds context from the execution input.
- condition: safe expression evaluation; follows the "true"/"false" edge.
- agent: delegated via the injected AgentTaskDelegate (INTERFACE NEED — the
  agents/ACP builder provides it; missing delegate fails the node closed).
- tool: executed via the injected ToolExecutor (contracts protocol).
- approval: PolicyEngine decides allow / deny / require_approval for the
  node's action. require_approval -> ApprovalStore.request -> execution parks
  in waiting_approval until signal() resumes it. Deny follows the "denied"
  edge or fails the execution (fail closed).
- action: built-in catalog (actions.py), policy-gated.
- notification: via NotificationPort; default records + emits an event
  (real delivery is the comms package's job).

Failure handling: node.retry = {max_attempts, backoff_seconds}; on exhaustion
the node's on_error target runs, else the execution FAILS with the reason.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from app.core.contracts import (
    ActionRequest,
    ApprovalStore,
    DomainEvent,
    EventBus,
    ExecutionStatus,
    PolicyEffect,
    PolicyEngine,
    TenantContext,
    ToolCall,
    ToolExecutor,
)

from .actions import ACTION_CATALOG, ActionError, run_action
from .conditions import ExpressionError, evaluate, render
from .dsl import DagSpec, default_next, outgoing
from .models import ExecutionEvent, WorkflowExecution, WorkflowVersion
from .ports import AgentTaskDelegate, LeadPort, NotificationPort, TaskPort


class WorkflowError(Exception):
    pass


class NodeError(Exception):
    """A node failed; the runner decides retry / on_error / fail."""

    def __init__(self, message: str, *, policy_denied: bool = False,
                 misconfigured: bool = False):
        super().__init__(message)
        self.policy_denied = policy_denied
        self.misconfigured = misconfigured


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _tid(tenant: TenantContext) -> str:
    return tenant.tenant_id


def _actor(tenant: TenantContext) -> str:
    return tenant.user_id or tenant.agent_id or "system"


TERMINAL_STATUSES = {
    ExecutionStatus.SUCCEEDED.value, ExecutionStatus.FAILED.value,
    ExecutionStatus.CANCELLED.value,
}


class WorkflowRunner:
    """Drives executions. All cross-package deps are injected protocols."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        policy_engine: PolicyEngine,
        *,
        approval_store: ApprovalStore | None = None,
        tool_executor: ToolExecutor | None = None,
        agent_delegate: AgentTaskDelegate | None = None,
        lead_port: LeadPort | None = None,
        task_port: TaskPort | None = None,
        notification_port: NotificationPort | None = None,
    ) -> None:
        self._sessions = session_factory
        self._bus = event_bus
        self._policy = policy_engine
        self._approvals = approval_store
        self._tools = tool_executor
        self._agents = agent_delegate
        self._lead_port = lead_port
        self._task_port = task_port
        self._notifications = notification_port

    # ---------------------------------------------------------- events
    async def _emit(self, topic: str, tenant: TenantContext, aggregate_id: str,
                    payload: dict[str, Any]) -> None:
        await self._bus.publish(DomainEvent(
            topic=topic, tenant_id=tenant.tenant_id, aggregate_id=aggregate_id,
            payload=payload, event_id=str(uuid.uuid4()), occurred_at=_utcnow()))

    async def _record(self, session: AsyncSession, tenant: TenantContext,
                      execution_id: str, event_type: str,
                      payload: dict[str, Any]) -> None:
        seq = (await session.execute(
            select(func.coalesce(func.max(ExecutionEvent.seq), -1)).where(
                ExecutionEvent.execution_id == execution_id)
        )).scalar_one() + 1
        session.add(ExecutionEvent(
            tenant_id=_tid(tenant), execution_id=execution_id, seq=seq,
            event_type=event_type, payload=payload))

    # ---------------------------------------------------------- public API
    async def drive(self, tenant: TenantContext, execution_id: str) -> WorkflowExecution:
        """Advance an execution as far as possible. Safe to call repeatedly."""
        async with self._sessions() as session:
            execution = await self._load_execution(session, tenant, execution_id)
            if execution.status in TERMINAL_STATUSES:
                return execution
            if execution.status == ExecutionStatus.WAITING_APPROVAL.value:
                return execution  # parked until signal()
            version = await self._load_version(session, tenant, execution.version_id)
            spec = DagSpec.model_validate(version.dag)
            by_id = {n.id: n for n in spec.nodes}
            state = dict(execution.state or {})
            state.setdefault("context", {})
            state.setdefault("attempts", {})
            state.setdefault("approvals", {})

            if not state.get("current"):
                trigger = next(n for n in spec.nodes if n.type == "trigger")
                state["current"] = trigger.id
                state["context"]["input"] = execution.input or {}
                state["context"]["execution_id"] = str(execution.id)
                # Lead-triggered flows: load the lead through the injected port
                # so conditions/actions can reference context["lead"].*
                lead_id = (execution.input or {}).get("lead_id")
                if lead_id and self._lead_port is not None:
                    lead = await self._lead_port.get_lead(tenant, str(lead_id))
                    if lead:
                        state["context"]["lead"] = lead
                await self._record(session, tenant, execution.id, "execution.started",
                                   {"version": execution.version})
                await self._emit("workflow.started", tenant, str(execution.id),
                                 {"definition_id": str(execution.definition_id),
                                  "version": execution.version})

            cost = Decimal(str(execution.cost_usd or 0))
            while True:
                node_id = state.get("current")
                node = by_id.get(node_id) if node_id else None
                if node is None:
                    await self._fail(session, tenant, execution, state,
                                     f"unknown current node '{node_id}'")
                    break
                await self._record(session, tenant, execution.id, "node.started",
                                   {"node": node.id, "type": node.type})
                try:
                    result, next_node, extra_cost, parked = await self._execute_node(
                        session, tenant, execution, state, spec, node)
                except NodeError as e:
                    attempts = state["attempts"].get(node.id, 0) + 1
                    state["attempts"][node.id] = attempts
                    if attempts < node.retry.max_attempts:
                        await self._record(session, tenant, execution.id, "node.retry", {
                            "node": node.id, "attempt": attempts,
                            "max_attempts": node.retry.max_attempts,
                            "backoff_seconds": node.retry.backoff_seconds,
                            "error": str(e)})
                        execution.state = state
                        flag_modified(execution, "state")
                        await session.commit()
                        if node.retry.backoff_seconds:
                            await asyncio.sleep(node.retry.backoff_seconds)
                        continue  # re-run the same node
                    await self._record(session, tenant, execution.id, "node.failed",
                                       {"node": node.id, "error": str(e),
                                        "policy_denied": e.policy_denied})
                    if node.on_error:
                        state["current"] = node.on_error
                        execution.state = state
                        flag_modified(execution, "state")
                        await session.commit()
                        continue
                    await self._fail(session, tenant, execution, state, str(e))
                    break

                cost += extra_cost
                execution.cost_usd = cost
                if parked:
                    # Parked for approval: the node is NOT complete. The
                    # approval.requested event (recorded by _execute_approval)
                    # is the durable marker; node.completed is recorded by
                    # signal() once the decision lands.
                    execution.status = ExecutionStatus.WAITING_APPROVAL.value
                    execution.state = state
                    flag_modified(execution, "state")
                    await session.commit()
                    return execution
                if result is not None:
                    state["context"][node.id] = result
                state.setdefault("finished_nodes", []).append(node.id)
                await self._record(session, tenant, execution.id, "node.completed",
                                   {"node": node.id, "type": node.type,
                                    "result_keys": sorted(result.keys())
                                    if isinstance(result, dict) else []})
                await self._emit("workflow.node_completed", tenant, str(execution.id),
                                 {"node": node.id, "type": node.type})
                if next_node is None:
                    execution.status = ExecutionStatus.SUCCEEDED.value
                    execution.finished_at = _utcnow()
                    execution.state = state
                    flag_modified(execution, "state")
                    await self._record(session, tenant, execution.id, "execution.finished",
                                       {"status": "succeeded", "cost_usd": str(cost)})
                    await self._emit("workflow.finished", tenant, str(execution.id),
                                     {"status": "succeeded"})
                    await session.commit()
                    break
                state["current"] = next_node
                execution.state = state
                flag_modified(execution, "state")
                await session.commit()
            return execution

    async def signal(self, tenant: TenantContext, execution_id: str,
                     signal: str, payload: dict[str, Any]) -> WorkflowExecution:
        """Deliver a signal — incl. approval decisions for parked executions."""
        async with self._sessions() as session:
            execution = await self._load_execution(session, tenant, execution_id)
            state = dict(execution.state or {})
            if signal == "approval":
                pending = state.get("pending_approval") or {}
                approval_id = payload.get("approval_id")
                if not pending or pending.get("approval_id") != approval_id:
                    raise WorkflowError("no matching pending approval for this signal")
                if self._approvals is None:
                    raise WorkflowError("approval store not configured")
                approved = bool(payload.get("approved"))
                approval = await self._approvals.decide(
                    tenant, approval_id, approved, payload.get("note", ""))
                node_id = pending["node_id"]
                await self._record(session, tenant, execution.id, "approval.decided", {
                    "node": node_id, "approval_id": approval_id, "approved": approved,
                    "status": approval.status.value})
                state.pop("pending_approval", None)
                if approved:
                    state["approvals"][node_id] = approval_id
                    state["current"] = pending["next_node"]
                else:
                    denied_target = pending.get("denied_node")
                    if denied_target:
                        state["current"] = denied_target
                    else:
                        await self._fail(session, tenant, execution, state,
                                         f"approval denied at node '{node_id}'")
                        await session.commit()
                        return execution
                # The decision landed: now the approval node completes, then the
                # run continues from the approved/denied edge.
                state.setdefault("finished_nodes", []).append(node_id)
                await self._record(session, tenant, execution.id, "node.completed",
                                   {"node": node_id, "type": "approval",
                                    "approved": approved,
                                    "approval_id": approval_id})
                await self._emit("workflow.node_completed", tenant,
                                 str(execution.id),
                                 {"node": node_id, "type": "approval"})
                execution.status = ExecutionStatus.RUNNING.value
                execution.state = state
                flag_modified(execution, "state")
                await session.commit()
            else:
                raise WorkflowError(f"unknown signal '{signal}'")
        return await self.drive(tenant, execution_id)

    # ---------------------------------------------------------- node execution
    async def _execute_node(
        self, session: AsyncSession, tenant: TenantContext, execution: WorkflowExecution,
        state: dict[str, Any], spec: DagSpec, node: Any,
    ) -> tuple[dict[str, Any] | None, str | None, Decimal, bool]:
        """Returns (result, next_node_id, cost_delta, parked_for_approval)."""
        cfg = node.config or {}
        context = state["context"]

        if node.type == "trigger":
            return {"input": execution.input or {}}, default_next(spec, node.id), Decimal(0), False

        if node.type == "condition":
            try:
                outcome = evaluate(cfg["expression"], context)
            except ExpressionError as e:
                raise NodeError(f"condition expression error: {e}") from e
            label = "true" if outcome else "false"
            targets = outgoing(spec, node.id, label)
            if not targets:
                raise NodeError(
                    f"condition node '{node.id}' evaluated {label} but has no '{label}' edge")
            return {"evaluated": outcome}, targets[0], Decimal(0), False

        if node.type == "agent":
            await self._policy_check(tenant, f"workflow.agent.{cfg['capability']}",
                                     {"node": node.id, "goal": cfg.get("goal")})
            if self._agents is None:
                raise NodeError(
                    "agent delegation is not configured (agents/ACP builder: "
                    "implement AgentTaskDelegate, see workflows/ports.py)",
                    misconfigured=True)
            result = await self._agents.run(
                tenant, capability=cfg["capability"], goal=str(cfg.get("goal", "")),
                context={"execution_id": str(execution.id), **context},
                autonomy_level=int(cfg.get("autonomy_level", 1)))
            if not result.ok:
                raise NodeError(f"agent step failed: {result.error or result.summary}")
            return {"summary": result.summary, "output": result.output or {}}, \
                default_next(spec, node.id), Decimal(str(result.cost_usd)), False

        if node.type == "tool":
            tool_name = cfg["tool_name"]
            await self._policy_check(tenant, f"tool.{tool_name}",
                                     {"node": node.id, "arguments": cfg.get("arguments", {})})
            if self._tools is None:
                raise NodeError("tool executor is not configured", misconfigured=True)
            tool_result = await self._tools.execute(
                tenant, ToolCall(
                    tool_name=tool_name,
                    arguments=render(cfg.get("arguments", {}), context),
                    grant_id=f"wf:{execution.id}:{node.id}",
                    idempotency_key=f"wf-{execution.id}-{node.id}",
                ))
            if not tool_result.ok:
                raise NodeError(f"tool '{tool_name}' failed: {tool_result.error}")
            return {"output": tool_result.output}, default_next(spec, node.id), \
                tool_result.cost_usd, False

        if node.type == "approval":
            return await self._execute_approval(session, tenant, execution, state, spec, node)

        if node.type == "action":
            action_name = cfg["action"]
            if action_name not in ACTION_CATALOG:
                raise NodeError(f"unknown action '{action_name}'")
            await self._policy_check(tenant, f"workflow.action.{action_name}",
                                     {"node": node.id, "params": cfg.get("params", {})})
            try:
                result = await run_action(
                    action_name, cfg.get("params", {}), context, tenant,
                    self._lead_port, self._task_port)
            except ActionError as e:
                raise NodeError(str(e)) from e
            return result, default_next(spec, node.id), Decimal(0), False

        if node.type == "notification":
            message = render(cfg.get("message", ""), context)
            if self._notifications is not None:
                await self._notifications.notify(tenant, message=message, context=context)
            await self._record(session, tenant, execution.id, "notification", {
                "node": node.id, "message": message,
                "delivered_by": "comms" if self._notifications else "event-only (comms not wired)"})
            await self._emit("workflow.notification", tenant, str(execution.id),
                             {"node": node.id, "message": message})
            return {"message": message}, default_next(spec, node.id), Decimal(0), False

        raise NodeError(f"unhandled node type '{node.type}'")  # unreachable: dsl validates

    async def _execute_approval(
        self, session: AsyncSession, tenant: TenantContext, execution: WorkflowExecution,
        state: dict[str, Any], spec: DagSpec, node: Any,
    ) -> tuple[dict[str, Any] | None, str | None, Decimal, bool]:
        cfg = node.config or {}
        action = cfg["action"]
        args = render(cfg.get("args", {}), state["context"])
        action_request = ActionRequest(
            tenant=tenant, action=action, resource=cfg.get("resource"),
            args={"node": node.id, **args},
            risk_context={"policy_ref": cfg["policy_ref"]})
        decision = await self._policy.evaluate(action_request)
        await self._record(session, tenant, execution.id, "policy.evaluated", {
            "node": node.id, "action": action, "effect": decision.effect.value,
            "reasons": list(decision.reasons)})

        if decision.effect is PolicyEffect.ALLOW:
            return {"skipped": True, "reason": "policy allowed without approval"}, \
                default_next(spec, node.id), Decimal(0), False
        if decision.effect is PolicyEffect.DENY:
            denied = outgoing(spec, node.id, "denied")
            if denied:
                return {"denied": True}, denied[0], Decimal(0), False
            raise NodeError(
                f"policy denied action '{action}': {'; '.join(decision.reasons)}",
                policy_denied=True)
        # REQUIRE_APPROVAL
        if self._approvals is None:
            raise NodeError("approval store is not configured", misconfigured=True)
        pending = state.get("pending_approval")
        if pending and pending.get("node_id") == node.id:
            approval = await self._approvals.get(tenant, pending["approval_id"])
            if approval is None:
                raise NodeError(f"approval {pending['approval_id']} vanished")
            status = approval.status.value
            if status == "approved":
                state.pop("pending_approval", None)
                state["approvals"][node.id] = approval.approval_id
                return {"approved": True}, default_next(spec, node.id), Decimal(0), False
            if status == "denied":
                state.pop("pending_approval", None)
                denied = outgoing(spec, node.id, "denied")
                if denied:
                    return {"denied": True}, denied[0], Decimal(0), False
                raise NodeError(f"approval denied at node '{node.id}'", policy_denied=True)
            # still pending -> stay parked
            return None, None, Decimal(0), True
        # ApprovalStore.request takes (decision, request, ...) per contracts.
        approval = await self._approvals.request(decision, action_request)
        next_node = default_next(spec, node.id)
        denied_targets = outgoing(spec, node.id, "denied")
        state["pending_approval"] = {
            "node_id": node.id, "approval_id": approval.approval_id,
            "next_node": next_node,
            "denied_node": denied_targets[0] if denied_targets else None,
        }
        await self._record(session, tenant, execution.id, "approval.requested", {
            "node": node.id, "approval_id": approval.approval_id,
            "action": action, "policy_ref": cfg["policy_ref"]})
        await self._emit("workflow.waiting_approval", tenant, str(execution.id),
                         {"node": node.id, "approval_id": approval.approval_id})
        return None, None, Decimal(0), True

    async def _policy_check(self, tenant: TenantContext, action: str,
                            args: dict[str, Any]) -> None:
        decision = await self._policy.evaluate(
            ActionRequest(tenant=tenant, action=action, args=args))
        if decision.effect is PolicyEffect.DENY:
            raise NodeError(
                f"policy denied action '{action}': {'; '.join(decision.reasons)}",
                policy_denied=True)

    # ---------------------------------------------------------- helpers
    async def _load_execution(
        self, session: AsyncSession, tenant: TenantContext, execution_id: str
    ) -> WorkflowExecution:
        execution = (await session.execute(
            select(WorkflowExecution).where(
                WorkflowExecution.id == execution_id,
                WorkflowExecution.tenant_id == _tid(tenant))
        )).scalar_one_or_none()
        if execution is None:
            raise WorkflowError(f"execution {execution_id} not found")
        return execution

    async def _load_version(
        self, session: AsyncSession, tenant: TenantContext, version_id: str
    ) -> WorkflowVersion:
        version = (await session.execute(
            select(WorkflowVersion).where(
                WorkflowVersion.id == version_id,
                WorkflowVersion.tenant_id == _tid(tenant))
        )).scalar_one_or_none()
        if version is None:
            raise WorkflowError(f"workflow version {version_id} not found")
        return version

    async def _fail(self, session: AsyncSession, tenant: TenantContext,
                    execution: WorkflowExecution, state: dict[str, Any], reason: str) -> None:
        execution.status = ExecutionStatus.FAILED.value
        execution.error = reason
        execution.finished_at = _utcnow()
        execution.state = state
        flag_modified(execution, "state")
        await self._record(session, tenant, execution.id, "execution.finished",
                           {"status": "failed", "error": reason})
        await self._emit("workflow.finished", tenant, str(execution.id),
                         {"status": "failed", "error": reason})
        await session.commit()

    async def cancel(self, tenant: TenantContext, execution_id: str,
                     reason: str) -> WorkflowExecution:
        async with self._sessions() as session:
            execution = await self._load_execution(session, tenant, execution_id)
            if execution.status in TERMINAL_STATUSES:
                raise WorkflowError(f"execution is already {execution.status}")
            execution.status = ExecutionStatus.CANCELLED.value
            execution.error = reason
            execution.finished_at = _utcnow()
            await self._record(session, tenant, execution.id, "execution.cancelled",
                               {"reason": reason, "actor": _actor(tenant)})
            await self._emit("workflow.finished", tenant, str(execution.id),
                             {"status": "cancelled", "reason": reason})
            await session.commit()
            return execution


__all__ = ["WorkflowRunner", "WorkflowError", "NodeError", "TERMINAL_STATUSES"]
