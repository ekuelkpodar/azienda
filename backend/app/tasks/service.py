"""TaskService: projects, milestones, tasks, dependencies, comments, delegation.

Binding state machine (ARCHITECTURE.md §6):
  pending -> planning -> waiting_approval -> executing -> blocked -> completed|failed
  plus cancelled from any non-terminal state.

Rules:
- can_transition(from, to) is a pure function; every state change goes through it.
- Dependencies form a DAG: adding an edge that creates a cycle is rejected.
- A task cannot complete while its dependencies are incomplete.
- Consequential writes (delete/archive, delegate to agents, bulk) are policy-gated.
- Every transition is recorded in task_transitions and emitted as a DomainEvent.
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

from .models import (
    Milestone,
    Project,
    Task,
    TaskBulkOpRecord,
    TaskComment,
    TaskDependency,
    TaskTransition,
)


# ------------------------------------------------------------------ errors
class TaskError(Exception):
    """Base for task domain errors."""


class TaskNotFound(TaskError):
    def __init__(self, entity: str, entity_id: str):
        super().__init__(f"{entity} {entity_id} not found")
        self.entity = entity
        self.entity_id = entity_id


class TaskValidationError(TaskError):
    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.details = details or {}


class IllegalTransitionError(TaskError):
    def __init__(self, from_status: str, to_status: str):
        super().__init__(f"illegal transition: {from_status} -> {to_status}")
        self.from_status = from_status
        self.to_status = to_status
        self.details = {"from": from_status, "to": to_status}


class DependencyCycleError(TaskError):
    def __init__(self, task_id: str, depends_on_id: str):
        super().__init__("dependency would create a cycle")
        self.details = {"task_id": task_id, "depends_on_id": depends_on_id}


class PolicyDeniedError(TaskError):
    def __init__(self, action: str, reasons: tuple[str, ...]):
        super().__init__(
            f"policy denied action '{action}': {'; '.join(reasons) or 'no reason given'}")
        self.action = action
        self.reasons = reasons


# ------------------------------------------------------------------ state machine
TERMINAL = frozenset({"completed", "failed", "cancelled"})

_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"planning", "cancelled"}),
    "planning": frozenset({"waiting_approval", "executing", "failed", "cancelled"}),
    "waiting_approval": frozenset({"executing", "blocked", "failed", "cancelled"}),
    "executing": frozenset({"waiting_approval", "blocked", "completed", "failed", "cancelled"}),
    "blocked": frozenset({"executing", "waiting_approval", "failed", "cancelled"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}

STATUSES = tuple(_TRANSITIONS)


def can_transition(from_status: str, to_status: str) -> bool:
    """Pure guard for the task state machine."""
    return to_status in _TRANSITIONS.get(from_status, frozenset())


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _tid(tenant: TenantContext) -> str:
    return tenant.tenant_id


def _actor(tenant: TenantContext) -> str:
    return tenant.user_id or tenant.agent_id or "system"


# ------------------------------------------------------------------ service
class TaskService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        policy_engine: PolicyEngine,
    ) -> None:
        self._sessions = session_factory
        self._bus = event_bus
        self._policy = policy_engine

    # ---------------------------------------------------------- internals
    async def _emit(
        self, topic: str, tenant: TenantContext, aggregate_id: str, payload: dict[str, Any]
    ) -> None:
        await self._bus.publish(
            DomainEvent(
                topic=topic,
                tenant_id=tenant.tenant_id,
                aggregate_id=aggregate_id,
                payload=payload,
                event_id=str(uuid.uuid4()),
                occurred_at=_utcnow(),
            )
        )

    async def _check_policy(
        self, tenant: TenantContext, action: str, resource: str | None = None,
        args: dict[str, Any] | None = None, idempotency_key: str | None = None,
    ) -> None:
        decision = await self._policy.evaluate(
            ActionRequest(tenant=tenant, action=action, resource=resource,
                          args=args or {}, idempotency_key=idempotency_key)
        )
        if decision.effect is PolicyEffect.DENY:
            raise PolicyDeniedError(action, decision.reasons)

    async def _get(
        self, session: AsyncSession, tenant: TenantContext, model: Any, entity: str,
        entity_id: str,
    ) -> Any:
        row = (
            await session.execute(
                select(model).where(model.id == entity_id, model.tenant_id == _tid(tenant))
            )
        ).scalar_one_or_none()
        if row is None:
            raise TaskNotFound(entity, str(entity_id))
        return row

    async def _get_task(self, session: AsyncSession, tenant: TenantContext,
                        task_id: str) -> Task:
        return await self._get(session, tenant, Task, "task", task_id)

    # ---------------------------------------------------------- projects
    async def create_project(self, tenant: TenantContext, data: dict[str, Any]) -> Project:
        await self._check_policy(tenant, "tasks.project.create", args={"name": data.get("name")})
        async with self._sessions() as session:
            project = Project(tenant_id=_tid(tenant), **data)
            session.add(project)
            await session.commit()
            await session.refresh(project)
        await self._emit("tasks.project.created", tenant, str(project.id),
                         {"name": project.name, "actor": _actor(tenant)})
        return project

    async def get_project(self, tenant: TenantContext, project_id: str) -> Project:
        async with self._sessions() as session:
            return await self._get(session, tenant, Project, "project", project_id)

    async def list_projects(
        self, tenant: TenantContext, *, status: str | None = None,
        limit: int = 50, offset: int = 0,
    ) -> tuple[list[Project], int]:
        async with self._sessions() as session:
            q = select(Project).where(Project.tenant_id == _tid(tenant))
            if status:
                q = q.where(Project.status == status)
            total = (await session.execute(
                select(func.count()).select_from(q.subquery()))).scalar_one()
            rows = (await session.execute(
                q.order_by(Project.created_at.desc()).limit(limit).offset(offset)
            )).scalars().all()
            return list(rows), total

    async def update_project(
        self, tenant: TenantContext, project_id: str, data: dict[str, Any]
    ) -> Project:
        await self._check_policy(tenant, "tasks.project.update", resource=f"project:{project_id}")
        async with self._sessions() as session:
            project = await self._get(session, tenant, Project, "project", project_id)
            for k, v in data.items():
                setattr(project, k, v)
            await session.commit()
            await session.refresh(project)
        await self._emit("tasks.project.updated", tenant, str(project.id),
                         {"actor": _actor(tenant), "fields": sorted(data)})
        return project

    # ---------------------------------------------------------- milestones
    async def create_milestone(self, tenant: TenantContext, data: dict[str, Any]) -> Milestone:
        await self._check_policy(tenant, "tasks.milestone.create", args={"name": data.get("name")})
        async with self._sessions() as session:
            await self._get(session, tenant, Project, "project", data["project_id"])
            ms = Milestone(tenant_id=_tid(tenant), **data)
            session.add(ms)
            await session.commit()
            await session.refresh(ms)
        await self._emit("tasks.milestone.created", tenant, str(ms.id),
                         {"name": ms.name, "project_id": str(ms.project_id)})
        return ms

    async def list_milestones(
        self, tenant: TenantContext, project_id: str
    ) -> list[Milestone]:
        async with self._sessions() as session:
            await self._get(session, tenant, Project, "project", project_id)
            rows = (await session.execute(
                select(Milestone).where(
                    Milestone.tenant_id == _tid(tenant), Milestone.project_id == project_id)
                .order_by(Milestone.due_at)
            )).scalars().all()
            return list(rows)

    async def update_milestone(
        self, tenant: TenantContext, milestone_id: str, data: dict[str, Any]
    ) -> Milestone:
        await self._check_policy(tenant, "tasks.milestone.update",
                                 resource=f"milestone:{milestone_id}")
        async with self._sessions() as session:
            ms = await self._get(session, tenant, Milestone, "milestone", milestone_id)
            for k, v in data.items():
                setattr(ms, k, v)
            await session.commit()
            await session.refresh(ms)
        return ms

    # ---------------------------------------------------------- tasks
    async def create_task(
        self, tenant: TenantContext, data: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> Task:
        await self._check_policy(tenant, "tasks.task.create",
                                 args={"title": data.get("title")},
                                 idempotency_key=idempotency_key)
        payload = {k: v for k, v in data.items() if k != "idempotency_key"}
        key = idempotency_key or data.get("idempotency_key")
        async with self._sessions() as session:
            if key:
                existing = (await session.execute(
                    select(Task).where(Task.tenant_id == _tid(tenant),
                                       Task.idempotency_key == key)
                )).scalar_one_or_none()
                if existing:
                    return existing
            if payload.get("project_id"):
                await self._get(session, tenant, Project, "project", payload["project_id"])
            if payload.get("parent_id"):
                await self._get(session, tenant, Task, "task", payload["parent_id"])
            if payload.get("milestone_id"):
                await self._get(session, tenant, Milestone, "milestone", payload["milestone_id"])
            task = Task(tenant_id=_tid(tenant), idempotency_key=key, **payload)
            session.add(task)
            try:
                await session.commit()
            except IntegrityError as e:
                await session.rollback()
                raise TaskValidationError(
                    "task creation failed (duplicate idempotency key?)") from e
            await session.refresh(task)
            await self._record_transition(
                session, tenant, task.id, "", "pending", _actor(tenant), "created")
            await session.commit()
        await self._emit("tasks.task.created", tenant, str(task.id),
                         {"title": task.title, "actor": _actor(tenant)})
        return task

    async def get_task(self, tenant: TenantContext, task_id: str) -> Task:
        async with self._sessions() as session:
            return await self._get_task(session, tenant, task_id)

    async def list_tasks(
        self, tenant: TenantContext, *, status: str | None = None,
        assignee_user_id: str | None = None, project_id: str | None = None,
        priority: str | None = None, parent_id: str | None = None,
        limit: int = 50, offset: int = 0,
    ) -> tuple[list[Task], int]:
        async with self._sessions() as session:
            q = select(Task).where(Task.tenant_id == _tid(tenant),
                                   Task.is_archived.is_(False))
            if status:
                q = q.where(Task.status == status)
            if assignee_user_id:
                q = q.where(Task.assignee_user_id == assignee_user_id)
            if project_id:
                q = q.where(Task.project_id == project_id)
            if priority:
                q = q.where(Task.priority == priority)
            if parent_id:
                q = q.where(Task.parent_id == parent_id)
            total = (await session.execute(
                select(func.count()).select_from(q.subquery()))).scalar_one()
            rows = (await session.execute(
                q.order_by(Task.created_at.desc()).limit(limit).offset(offset)
            )).scalars().all()
            return list(rows), total

    async def update_task(
        self, tenant: TenantContext, task_id: str, data: dict[str, Any]
    ) -> Task:
        await self._check_policy(tenant, "tasks.task.update", resource=f"task:{task_id}")
        async with self._sessions() as session:
            task = await self._get_task(session, tenant, task_id)
            if task.status in TERMINAL:
                raise TaskValidationError(
                    f"cannot edit task in terminal state '{task.status}'")
            for k, v in data.items():
                setattr(task, k, v)
            await session.commit()
            await session.refresh(task)
        await self._emit("tasks.task.updated", tenant, str(task.id),
                         {"actor": _actor(tenant), "fields": sorted(data)})
        return task

    async def _record_transition(
        self, session: AsyncSession, tenant: TenantContext, task_id: str,
        from_status: str, to_status: str, actor: str, note: str | None,
    ) -> None:
        session.add(TaskTransition(
            tenant_id=_tid(tenant), task_id=task_id, from_status=from_status,
            to_status=to_status, actor=actor, note=note,
        ))

    async def transition_task(
        self, tenant: TenantContext, task_id: str, to_status: str,
        note: str | None = None,
    ) -> Task:
        if to_status not in STATUSES:
            raise TaskValidationError(f"unknown status '{to_status}'")
        await self._check_policy(tenant, "tasks.task.transition",
                                 resource=f"task:{task_id}", args={"to": to_status})
        async with self._sessions() as session:
            task = await self._get_task(session, tenant, task_id)
            if not can_transition(task.status, to_status):
                raise IllegalTransitionError(task.status, to_status)
            if to_status == "completed":
                incomplete = await self._incomplete_dependencies(session, tenant, task.id)
                if incomplete:
                    raise TaskValidationError(
                        "cannot complete: dependencies are not completed",
                        {"incomplete_dependency_ids": incomplete},
                    )
            from_status = task.status
            task.status = to_status
            if to_status == "completed":
                task.completed_at = _utcnow()
            await self._record_transition(
                session, tenant, task.id, from_status, to_status, _actor(tenant), note)
            await session.commit()
            await session.refresh(task)
        await self._emit("tasks.task.transitioned", tenant, str(task.id), {
            "from": from_status, "to": to_status, "note": note, "actor": _actor(tenant),
        })
        return task

    async def assign_task(
        self, tenant: TenantContext, task_id: str,
        assignee_user_id: str | None, assignee_agent_id: str | None,
    ) -> Task:
        await self._check_policy(tenant, "tasks.task.assign", resource=f"task:{task_id}",
                                 args={"user": str(assignee_user_id) if assignee_user_id else None,
                                       "agent": assignee_agent_id})
        async with self._sessions() as session:
            task = await self._get_task(session, tenant, task_id)
            task.assignee_user_id = assignee_user_id
            task.assignee_agent_id = assignee_agent_id
            await self._record_transition(
                session, tenant, task.id, task.status, task.status, _actor(tenant),
                f"assigned user={assignee_user_id} agent={assignee_agent_id}")
            await session.commit()
            await session.refresh(task)
        await self._emit("tasks.task.assigned", tenant, str(task.id), {
            "assignee_user_id": str(assignee_user_id) if assignee_user_id else None,
            "assignee_agent_id": assignee_agent_id, "actor": _actor(tenant),
        })
        return task

    async def archive_task(self, tenant: TenantContext, task_id: str) -> Task:
        await self._check_policy(tenant, "tasks.task.archive", resource=f"task:{task_id}")
        async with self._sessions() as session:
            task = await self._get_task(session, tenant, task_id)
            task.is_archived = True
            await session.commit()
            await session.refresh(task)
        await self._emit("tasks.task.archived", tenant, str(task.id), {"actor": _actor(tenant)})
        return task

    # ---------------------------------------------------------- dependencies
    async def _dependency_graph(
        self, session: AsyncSession, tenant: TenantContext
    ) -> dict[str, set[str]]:
        rows = (await session.execute(
            select(TaskDependency.task_id, TaskDependency.depends_on_id).where(
                TaskDependency.tenant_id == _tid(tenant))
        )).all()
        graph: dict[str, set[str]] = {}
        for task_id, depends_on_id in rows:
            graph.setdefault(task_id, set()).add(depends_on_id)
        return graph

    @staticmethod
    def _creates_cycle(graph: dict[str, set[str]],
                       task_id: str, depends_on_id: str) -> bool:
        """Would edge task_id -> depends_on_id create a cycle? DFS from depends_on_id."""
        stack = [depends_on_id]
        seen: set[str] = set()
        while stack:
            node = stack.pop()
            if node == task_id:
                return True
            if node in seen:
                continue
            seen.add(node)
            stack.extend(graph.get(node, ()))
        return False

    async def add_dependency(
        self, tenant: TenantContext, task_id: str, depends_on_id: str
    ) -> TaskDependency:
        if task_id == depends_on_id:
            raise TaskValidationError("a task cannot depend on itself")
        await self._check_policy(tenant, "tasks.dependency.create",
                                 resource=f"task:{task_id}")
        async with self._sessions() as session:
            await self._get_task(session, tenant, task_id)
            await self._get_task(session, tenant, depends_on_id)
            graph = await self._dependency_graph(session, tenant)
            if self._creates_cycle(graph, task_id, depends_on_id):
                raise DependencyCycleError(str(task_id), str(depends_on_id))
            dep = TaskDependency(tenant_id=_tid(tenant), task_id=task_id,
                                 depends_on_id=depends_on_id)
            session.add(dep)
            try:
                await session.commit()
            except IntegrityError as e:
                await session.rollback()
                raise TaskValidationError("dependency already exists") from e
            await session.refresh(dep)
        await self._emit("tasks.dependency.added", tenant, str(task_id),
                         {"depends_on_id": str(depends_on_id), "actor": _actor(tenant)})
        return dep

    async def list_dependencies(
        self, tenant: TenantContext, task_id: str
    ) -> list[TaskDependency]:
        async with self._sessions() as session:
            await self._get_task(session, tenant, task_id)
            rows = (await session.execute(
                select(TaskDependency).where(
                    TaskDependency.tenant_id == _tid(tenant),
                    TaskDependency.task_id == task_id)
            )).scalars().all()
            return list(rows)

    async def _incomplete_dependencies(
        self, session: AsyncSession, tenant: TenantContext, task_id: str
    ) -> list[str]:
        rows = (await session.execute(
            select(TaskDependency.depends_on_id).where(
                TaskDependency.tenant_id == _tid(tenant), TaskDependency.task_id == task_id)
        )).scalars().all()
        incomplete: list[str] = []
        for dep_id in rows:
            t = (await session.execute(
                select(Task.status).where(Task.id == dep_id,
                                          Task.tenant_id == _tid(tenant))
            )).scalar_one_or_none()
            if t != "completed":
                incomplete.append(str(dep_id))
        return incomplete

    # ---------------------------------------------------------- comments
    async def add_comment(
        self, tenant: TenantContext, task_id: str, body: str,
        author_type: str = "user",
    ) -> TaskComment:
        await self._check_policy(tenant, "tasks.comment.create", resource=f"task:{task_id}")
        async with self._sessions() as session:
            await self._get_task(session, tenant, task_id)
            comment = TaskComment(
                tenant_id=_tid(tenant), task_id=task_id, body=body,
                author_id=str(tenant.user_id) if tenant.user_id else None,
                author_type=author_type,
            )
            session.add(comment)
            await session.commit()
            await session.refresh(comment)
        await self._emit("tasks.comment.added", tenant, str(comment.id),
                         {"task_id": str(task_id), "actor": _actor(tenant)})
        return comment

    async def list_comments(
        self, tenant: TenantContext, task_id: str, limit: int = 50, offset: int = 0
    ) -> tuple[list[TaskComment], int]:
        async with self._sessions() as session:
            await self._get_task(session, tenant, task_id)
            q = select(TaskComment).where(
                TaskComment.tenant_id == _tid(tenant), TaskComment.task_id == task_id)
            total = (await session.execute(
                select(func.count()).select_from(q.subquery()))).scalar_one()
            rows = (await session.execute(
                q.order_by(TaskComment.created_at).limit(limit).offset(offset)
            )).scalars().all()
            return list(rows), total

    async def list_transitions(
        self, tenant: TenantContext, task_id: str
    ) -> list[TaskTransition]:
        async with self._sessions() as session:
            await self._get_task(session, tenant, task_id)
            rows = (await session.execute(
                select(TaskTransition).where(
                    TaskTransition.tenant_id == _tid(tenant),
                    TaskTransition.task_id == task_id)
                .order_by(TaskTransition.created_at)
            )).scalars().all()
            return list(rows)

    # ---------------------------------------------------------- delegate
    async def delegate_task(
        self, tenant: TenantContext, task_id: str,
        agent_id: str | None, capability: str | None, goal: str | None,
    ) -> Task:
        """Hand a task to the agent workforce.

        Policy-gated (external compute + cost). Records the delegation intent on
        the task plan and moves the task into the governed pipeline (planning).
        The agents package owns actual agent-run creation; this is the
        controlled entry point for objective -> task decomposition.
        """
        await self._check_policy(tenant, "tasks.delegate", resource=f"task:{task_id}",
                                 args={"agent_id": agent_id, "capability": capability})
        async with self._sessions() as session:
            task = await self._get_task(session, tenant, task_id)
            if not can_transition(task.status, "planning") and task.status != "planning":
                raise IllegalTransitionError(task.status, "planning")
            plan = dict(task.plan or {})
            plan["delegation"] = {
                "agent_id": agent_id, "capability": capability,
                "goal": goal or task.title,
                "requested_by": _actor(tenant),
                "requested_at": _utcnow().isoformat(),
            }
            task.plan = plan
            from_status = task.status
            if task.status == "pending":
                task.status = "planning"
                await self._record_transition(
                    session, tenant, task.id, from_status, "planning",
                    _actor(tenant), "delegated to agent workforce")
            await session.commit()
            await session.refresh(task)
        await self._emit("tasks.task.delegated", tenant, str(task.id), {
            "agent_id": agent_id, "capability": capability, "actor": _actor(tenant),
        })
        return task

    # ---------------------------------------------------------- outcome
    async def get_outcome(self, tenant: TenantContext, task_id: str) -> dict[str, Any]:
        async with self._sessions() as session:
            task = await self._get_task(session, tenant, task_id)
            transitions = await self.list_transitions(tenant, task_id)
            deps = await self.list_dependencies(tenant, task_id)
            comments, comments_total = await self.list_comments(tenant, task_id, limit=1)
            subtasks, _ = await self.list_tasks(tenant, parent_id=task.id, limit=100)
            return {
                "task_id": str(task.id), "status": task.status,
                "plan": task.plan or {},
                "cost_usd": task.cost_usd,
                "transitions": transitions, "comments_count": comments_total,
                "dependencies": deps, "subtasks": subtasks,
            }

    # ---------------------------------------------------------- bulk
    async def bulk(self, tenant: TenantContext,
                   operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Bulk ops with durable per-operation idempotency keys.

        Non-atomic by design: each item is applied independently and reports
        its own result. A repeated idempotency key replays the stored result
        (no re-execution) for every op kind, not just create.
        """
        await self._check_policy(tenant, "tasks.bulk", args={"count": len(operations)})
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for op in operations:
            key = op.get("idempotency_key", "")
            if not key or key in seen:
                results.append({"idempotency_key": key, "ok": False,
                                "error": {"code": "duplicate_idempotency_key",
                                          "message": "idempotency key missing"
                                          " or repeated in batch"}})
                continue
            seen.add(key)
            replayed = await self._bulk_replay(tenant, key)
            if replayed is not None:
                results.append({**replayed, "replayed": True})
                continue
            try:
                kind = op["op"]
                payload = op.get("payload", {})
                if kind == "create":
                    task = await self.create_task(tenant, payload, idempotency_key=key)
                elif kind == "update":
                    task = await self.update_task(tenant, op["task_id"], payload)
                elif kind == "transition":
                    task = await self.transition_task(tenant, op["task_id"],
                                                      payload["to"], payload.get("note"))
                elif kind == "assign":
                    task = await self.assign_task(tenant, op["task_id"],
                                                  payload.get("assignee_user_id"),
                                                  payload.get("assignee_agent_id"))
                else:
                    raise TaskValidationError(f"unknown bulk op '{kind}'")
                item = {"idempotency_key": key, "ok": True, "task_id": str(task.id)}
                await self._bulk_record(tenant, key, kind, str(task.id), True, item)
                results.append(item)
            except TaskError as e:
                code = "illegal_transition" if isinstance(e, IllegalTransitionError) \
                    else "validation_error"
                if isinstance(e, PolicyDeniedError):
                    code = "policy_denied"
                item = {"idempotency_key": key, "ok": False,
                        "error": {"code": code, "message": str(e),
                                  "details": getattr(e, "details", {})}}
                await self._bulk_record(tenant, key, op.get("op", "unknown"), None,
                                        False, item)
                results.append(item)
        await self._emit("tasks.bulk.completed", tenant, str(uuid.uuid4()), {
            "count": len(operations),
            "succeeded": sum(1 for r in results if r["ok"]),
            "actor": _actor(tenant),
        })
        return results

    async def _bulk_replay(self, tenant: TenantContext, key: str) -> dict[str, Any] | None:
        async with self._sessions() as session:
            record = (await session.execute(
                select(TaskBulkOpRecord).where(
                    TaskBulkOpRecord.tenant_id == _tid(tenant),
                    TaskBulkOpRecord.idempotency_key == key)
            )).scalar_one_or_none()
            return dict(record.result) if record else None

    async def _bulk_record(self, tenant: TenantContext, key: str, op: str,
                           task_id: str | None, ok: bool, result: dict[str, Any]) -> None:
        async with self._sessions() as session:
            session.add(TaskBulkOpRecord(
                tenant_id=_tid(tenant), idempotency_key=key, op=op,
                task_id=str(task_id) if task_id else None,
                ok=ok, result=result))
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()  # lost a race with a duplicate; replay wins


__all__ = [
    "TaskService", "TaskError", "TaskNotFound", "TaskValidationError",
    "IllegalTransitionError", "DependencyCycleError", "PolicyDeniedError",
    "can_transition", "STATUSES", "TERMINAL",
]
