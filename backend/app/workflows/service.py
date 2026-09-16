"""WorkflowService: definitions, versions, executions.

- Definitions hold an editable draft DAG; publishing validates it and creates
  an IMMUTABLE version. Executions pin a version.
- start() creates the execution row (idempotent on idempotency_key) and drives
  it via the injected WorkflowRunner.
- The service never imports another domain package; the runner's ports are
  injected (see adapters.py for the CRM/tasks adapters).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.contracts import (
    ActionRequest,
    DomainEvent,
    EventBus,
    PolicyEffect,
    PolicyEngine,
    TenantContext,
)

from .definitions.lead_outreach import (
    DEFINITION_DESCRIPTION,
    DEFINITION_NAME,
    build_dag,
)
from .dsl import DagValidationError, validate_dag
from .models import ExecutionEvent, WorkflowDefinition, WorkflowExecution, WorkflowVersion
from .runner import WorkflowRunner


class WorkflowError(Exception):
    pass


class WorkflowNotFound(WorkflowError):
    def __init__(self, entity: str, entity_id: str):
        super().__init__(f"{entity} {entity_id} not found")
        self.entity = entity
        self.entity_id = entity_id


class WorkflowValidationError(WorkflowError):
    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.details = details or {}


class PolicyDeniedError(WorkflowError):
    def __init__(self, action: str, reasons: tuple[str, ...]):
        super().__init__(
            f"policy denied action '{action}': {'; '.join(reasons) or 'no reason given'}")
        self.action = action
        self.reasons = reasons


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _tid(tenant: TenantContext) -> str:
    return tenant.tenant_id


def _actor(tenant: TenantContext) -> str:
    return tenant.user_id or tenant.agent_id or "system"


class WorkflowService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        policy_engine: PolicyEngine,
        runner: WorkflowRunner,
    ) -> None:
        self._sessions = session_factory
        self._bus = event_bus
        self._policy = policy_engine
        self._runner = runner

    # ---------------------------------------------------------- internals
    async def _emit(self, topic: str, tenant: TenantContext, aggregate_id: str,
                    payload: dict[str, Any]) -> None:
        await self._bus.publish(DomainEvent(
            topic=topic, tenant_id=tenant.tenant_id, aggregate_id=aggregate_id,
            payload=payload, event_id=str(uuid.uuid4()), occurred_at=_utcnow()))

    async def _check_policy(self, tenant: TenantContext, action: str,
                            args: dict[str, Any] | None = None) -> None:
        decision = await self._policy.evaluate(
            ActionRequest(tenant=tenant, action=action, args=args or {}))
        if decision.effect is PolicyEffect.DENY:
            raise PolicyDeniedError(action, decision.reasons)

    # ---------------------------------------------------------- definitions
    async def create_definition(self, tenant: TenantContext, name: str,
                                description: str | None, autonomy_level: int,
                                dag: dict[str, Any]) -> WorkflowDefinition:
        await self._check_policy(tenant, "workflows.definition.create", {"name": name})
        try:
            validate_dag(dag)
        except DagValidationError as e:
            raise WorkflowValidationError("invalid dag", {"errors": e.errors}) from e
        async with self._sessions() as session:
            existing = (await session.execute(
                select(WorkflowDefinition).where(
                    WorkflowDefinition.tenant_id == _tid(tenant),
                    WorkflowDefinition.name == name)
            )).scalar_one_or_none()
            if existing:
                raise WorkflowValidationError(
                    f"definition '{name}' already exists", {"name": name})
            definition = WorkflowDefinition(
                tenant_id=_tid(tenant), name=name, description=description,
                autonomy_level=autonomy_level, draft_dag=dag)
            session.add(definition)
            await session.commit()
            await session.refresh(definition)
        await self._emit("workflows.definition.created", tenant, str(definition.id),
                         {"name": name, "actor": _actor(tenant)})
        return definition

    async def get_definition(self, tenant: TenantContext,
                             definition_id: str) -> WorkflowDefinition:
        async with self._sessions() as session:
            definition = (await session.execute(
                select(WorkflowDefinition).where(
                    WorkflowDefinition.id == definition_id,
                    WorkflowDefinition.tenant_id == _tid(tenant))
            )).scalar_one_or_none()
            if definition is None:
                raise WorkflowNotFound("workflow_definition", str(definition_id))
            return definition

    async def list_definitions(
        self, tenant: TenantContext, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[WorkflowDefinition], int]:
        async with self._sessions() as session:
            q = select(WorkflowDefinition).where(
                WorkflowDefinition.tenant_id == _tid(tenant))
            total = (await session.execute(
                select(func.count()).select_from(q.subquery()))).scalar_one()
            rows = (await session.execute(
                q.order_by(WorkflowDefinition.created_at.desc()).limit(limit).offset(offset)
            )).scalars().all()
            return list(rows), total

    async def update_definition(self, tenant: TenantContext, definition_id: str,
                                data: dict[str, Any]) -> WorkflowDefinition:
        await self._check_policy(tenant, "workflows.definition.update",
                                 {"definition_id": str(definition_id)})
        if "dag" in data:
            try:
                validate_dag(data["dag"])
            except DagValidationError as e:
                raise WorkflowValidationError("invalid dag", {"errors": e.errors}) from e
        async with self._sessions() as session:
            definition = await self.get_definition(tenant, definition_id)
            session.add(definition)
            for k, v in data.items():
                if k == "dag":
                    definition.draft_dag = v
                else:
                    setattr(definition, k, v)
            await session.commit()
            await session.refresh(definition)
        return definition

    async def publish_version(self, tenant: TenantContext,
                              definition_id: str) -> WorkflowVersion:
        """Validate the draft and freeze it as a new immutable version."""
        await self._check_policy(tenant, "workflows.version.publish",
                                 {"definition_id": str(definition_id)})
        async with self._sessions() as session:
            definition = (await session.execute(
                select(WorkflowDefinition).where(
                    WorkflowDefinition.id == definition_id,
                    WorkflowDefinition.tenant_id == _tid(tenant))
            )).scalar_one_or_none()
            if definition is None:
                raise WorkflowNotFound("workflow_definition", str(definition_id))
            try:
                spec = validate_dag(definition.draft_dag)
            except DagValidationError as e:
                raise WorkflowValidationError("draft dag is invalid",
                                              {"errors": e.errors}) from e
            latest = (await session.execute(
                select(func.coalesce(func.max(WorkflowVersion.version), 0)).where(
                    WorkflowVersion.definition_id == definition.id)
            )).scalar_one()
            version = WorkflowVersion(
                tenant_id=_tid(tenant), definition_id=definition.id,
                version=latest + 1, dag=spec.model_dump(by_alias=True),
                published_by=_actor(tenant))
            session.add(version)
            await session.commit()
            await session.refresh(version)
        await self._emit("workflows.version.published", tenant, str(version.id), {
            "definition_id": str(definition_id), "version": version.version,
            "actor": _actor(tenant)})
        return version

    async def get_version(self, tenant: TenantContext, definition_id: str,
                          version: int) -> WorkflowVersion:
        async with self._sessions() as session:
            row = (await session.execute(
                select(WorkflowVersion).where(
                    WorkflowVersion.definition_id == definition_id,
                    WorkflowVersion.version == version,
                    WorkflowVersion.tenant_id == _tid(tenant))
            )).scalar_one_or_none()
            if row is None:
                raise WorkflowNotFound("workflow_version", f"{definition_id} v{version}")
            return row

    async def latest_version_number(self, tenant: TenantContext,
                                    definition_id: str) -> int | None:
        async with self._sessions() as session:
            n = (await session.execute(
                select(func.max(WorkflowVersion.version)).where(
                    WorkflowVersion.definition_id == definition_id,
                    WorkflowVersion.tenant_id == _tid(tenant))
            )).scalar_one_or_none()
            return n

    async def list_versions(self, tenant: TenantContext,
                            definition_id: str) -> list[WorkflowVersion]:
        async with self._sessions() as session:
            rows = (await session.execute(
                select(WorkflowVersion).where(
                    WorkflowVersion.definition_id == definition_id,
                    WorkflowVersion.tenant_id == _tid(tenant))
                .order_by(WorkflowVersion.version.desc())
            )).scalars().all()
            return list(rows)

    # ---------------------------------------------------------- seed: the complete lead workflow
    async def seed_lead_outreach(self, tenant: TenantContext) -> WorkflowDefinition:
        """Idempotently seed the shippable lead-qualification workflow (§48 item 17)."""
        async with self._sessions() as session:
            existing = (await session.execute(
                select(WorkflowDefinition).where(
                    WorkflowDefinition.tenant_id == _tid(tenant),
                    WorkflowDefinition.name == DEFINITION_NAME)
            )).scalar_one_or_none()
            if existing:
                return existing
        definition = await self.create_definition(
            tenant, DEFINITION_NAME, DEFINITION_DESCRIPTION, 2, build_dag())
        await self.publish_version(tenant, definition.id)
        return definition

    # ---------------------------------------------------------- executions
    async def start_execution(
        self, tenant: TenantContext, *, definition_id: str | None = None,
        definition_name: str | None = None, version: int | None = None,
        input: dict[str, Any] | None = None, idempotency_key: str | None = None,
    ) -> WorkflowExecution:
        await self._check_policy(tenant, "workflows.execution.start",
                                 {"definition_id": str(definition_id or definition_name)})
        async with self._sessions() as session:
            if definition_id is None and definition_name:
                definition = (await session.execute(
                    select(WorkflowDefinition).where(
                        WorkflowDefinition.tenant_id == _tid(tenant),
                        WorkflowDefinition.name == definition_name)
                )).scalar_one_or_none()
                if definition is None:
                    raise WorkflowNotFound("workflow_definition", definition_name)
                definition_id = definition.id
            if definition_id is None:
                raise WorkflowValidationError("definition_id or definition_name is required")
            if idempotency_key:
                existing = (await session.execute(
                    select(WorkflowExecution).where(
                        WorkflowExecution.tenant_id == _tid(tenant),
                        WorkflowExecution.idempotency_key == idempotency_key)
                )).scalar_one_or_none()
                if existing:
                    return existing
            version_row: WorkflowVersion | None
            if version is not None:
                version_row = (await session.execute(
                    select(WorkflowVersion).where(
                        WorkflowVersion.definition_id == definition_id,
                        WorkflowVersion.version == version,
                        WorkflowVersion.tenant_id == _tid(tenant))
                )).scalar_one_or_none()
            else:
                version_row = (await session.execute(
                    select(WorkflowVersion).where(
                        WorkflowVersion.definition_id == definition_id,
                        WorkflowVersion.tenant_id == _tid(tenant))
                    .order_by(WorkflowVersion.version.desc())
                )).scalars().first()
            if version_row is None:
                raise WorkflowValidationError(
                    "definition has no published version; publish first")
            execution = WorkflowExecution(
                tenant_id=_tid(tenant), definition_id=definition_id,
                version_id=version_row.id, version=version_row.version,
                input=input or {}, state={}, idempotency_key=idempotency_key)
            session.add(execution)
            try:
                await session.commit()
            except IntegrityError as e:
                await session.rollback()
                raise WorkflowValidationError(
                    "duplicate idempotency key for workflow execution") from e
            await session.refresh(execution)
            execution_id = execution.id
        return await self._runner.drive(tenant, execution_id)

    async def get_execution(self, tenant: TenantContext,
                            execution_id: str) -> WorkflowExecution:
        async with self._sessions() as session:
            execution = (await session.execute(
                select(WorkflowExecution).where(
                    WorkflowExecution.id == execution_id,
                    WorkflowExecution.tenant_id == _tid(tenant))
            )).scalar_one_or_none()
            if execution is None:
                raise WorkflowNotFound("workflow_execution", str(execution_id))
            return execution

    async def list_executions(
        self, tenant: TenantContext, *, definition_id: str | None = None,
        status: str | None = None, limit: int = 50, offset: int = 0,
    ) -> tuple[list[WorkflowExecution], int]:
        async with self._sessions() as session:
            q = select(WorkflowExecution).where(
                WorkflowExecution.tenant_id == _tid(tenant))
            if definition_id:
                q = q.where(WorkflowExecution.definition_id == definition_id)
            if status:
                q = q.where(WorkflowExecution.status == status)
            total = (await session.execute(
                select(func.count()).select_from(q.subquery()))).scalar_one()
            rows = (await session.execute(
                q.order_by(WorkflowExecution.started_at.desc()).limit(limit).offset(offset)
            )).scalars().all()
            return list(rows), total

    async def list_execution_events(
        self, tenant: TenantContext, execution_id: str,
        limit: int = 200, offset: int = 0,
    ) -> tuple[list[ExecutionEvent], int]:
        async with self._sessions() as session:
            await self.get_execution(tenant, execution_id)  # tenant-scoped existence check
            q = select(ExecutionEvent).where(
                ExecutionEvent.execution_id == execution_id,
                ExecutionEvent.tenant_id == _tid(tenant))
            total = (await session.execute(
                select(func.count()).select_from(q.subquery()))).scalar_one()
            rows = (await session.execute(
                q.order_by(ExecutionEvent.seq).limit(limit).offset(offset)
            )).scalars().all()
            return list(rows), total

    async def signal_execution(self, tenant: TenantContext, execution_id: str,
                               signal: str, payload: dict[str, Any]) -> WorkflowExecution:
        await self._check_policy(tenant, "workflows.execution.signal",
                                 {"execution_id": str(execution_id), "signal": signal})
        return await self._runner.signal(tenant, execution_id, signal, payload)

    async def cancel_execution(self, tenant: TenantContext, execution_id: str,
                               reason: str) -> WorkflowExecution:
        await self._check_policy(tenant, "workflows.execution.cancel",
                                 {"execution_id": str(execution_id)})
        return await self._runner.cancel(tenant, execution_id, reason)

    # ---------------------------------------------------------- inspection
    async def inspect(self, tenant: TenantContext,
                      execution_id: str) -> dict[str, Any]:
        """Full inspection payload: execution + current state + recent events."""
        execution = await self.get_execution(tenant, execution_id)
        events, _ = await self.list_execution_events(tenant, execution_id, limit=200)
        state = execution.state or {}
        return {
            "execution_id": str(execution.id),
            "definition_id": str(execution.definition_id),
            "version": execution.version,
            "status": execution.status,
            "current_node": state.get("current"),
            "finished_nodes": state.get("finished_nodes", []),
            "attempts": state.get("attempts", {}),
            "pending_approval": state.get("pending_approval"),
            "cost_usd": execution.cost_usd,
            "error": execution.error,
            "started_at": execution.started_at,
            "finished_at": execution.finished_at,
            "events": [
                {"seq": e.seq, "event_type": e.event_type, "payload": e.payload,
                 "created_at": e.created_at}
                for e in events
            ],
        }


__all__ = [
    "WorkflowService", "WorkflowError", "WorkflowNotFound",
    "WorkflowValidationError", "PolicyDeniedError",
]
