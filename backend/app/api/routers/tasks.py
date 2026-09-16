"""Router: tasks. Contract: /API.md §4 (Tasks/Projects).

Thin HTTP layer only. All business logic lives in TaskService.
"""

from __future__ import annotations

import functools
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse

from app.tasks import schemas as S
from app.tasks.service import TaskError

from ._common import (
    Services,
    ServicesDep,
    TenantDep,
    domain_error_to_response,
    idempotent_post,
    page_params,
    page_response,
    require_idempotency_key,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])


# ------------------------------------------------------------------ helpers
def _jsonable(v: Any) -> Any:
    if isinstance(v, str):
        return str(v)
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    return v


def _dump(obj: Any, fields: list[str]) -> dict[str, Any]:
    return {f: _jsonable(getattr(obj, f)) for f in fields}


PROJECT_FIELDS = ["id", "tenant_id", "name", "description", "status", "owner_id",
                  "created_at", "updated_at"]
MILESTONE_FIELDS = ["id", "tenant_id", "project_id", "name", "description", "due_at",
                    "status", "created_at", "updated_at"]
TASK_FIELDS = ["id", "tenant_id", "project_id", "parent_id", "title", "description",
               "status", "priority", "assignee_user_id", "assignee_agent_id",
               "plan", "cost_usd", "due_at", "completed_at", "is_archived",
               "created_at", "updated_at"]
COMMENT_FIELDS = ["id", "tenant_id", "task_id", "author_id", "author_type", "body",
                  "created_at"]
TRANSITION_FIELDS = ["id", "from_status", "to_status", "actor", "note", "created_at"]
DEPENDENCY_FIELDS = ["id", "task_id", "depends_on_id"]



def _handle(fn: Any) -> Any:
    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> JSONResponse:
        try:
            return await fn(*args, **kwargs)
        except TaskError as e:
            return domain_error_to_response(e)

    return wrapper


# ------------------------------------------------------------------ projects
@router.get("/projects")
@_handle
async def list_projects(
    services: Services = ServicesDep, tenant: Any = TenantDep,
    status: str | None = Query(default=None),
    page_size: int = Query(default=50), page_token: str | None = Query(default=None),
) -> JSONResponse:
    size, offset = page_params(page_size, page_token)
    items, total = await services.tasks.list_projects(
        tenant, status=status, limit=size, offset=offset)
    return JSONResponse(content=page_response([_dump(p, PROJECT_FIELDS) for p in items],
                                              total, offset, size))


@router.post("/projects", status_code=201)
@_handle
async def create_project(
    request: Request, body: S.ProjectCreate,
    services: Services = ServicesDep, tenant: Any = TenantDep,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JSONResponse:
    _key_guard = require_idempotency_key(request)
    if isinstance(_key_guard, JSONResponse):
        return _key_guard
    async def handler() -> tuple[dict, int]:
        project = await services.tasks.create_project(tenant, body.model_dump())
        return _dump(project, PROJECT_FIELDS), 201

    return await idempotent_post(request, services, tenant, handler)


@router.get("/projects/{project_id}")
@_handle
async def get_project(
    project_id: str, services: Services = ServicesDep, tenant: Any = TenantDep,
) -> JSONResponse:
    project = await services.tasks.get_project(tenant, project_id)
    return JSONResponse(content=_dump(project, PROJECT_FIELDS))


@router.patch("/projects/{project_id}")
@_handle
async def update_project(
    request: Request,
    project_id: str, body: S.ProjectUpdate,
    services: Services = ServicesDep, tenant: Any = TenantDep,
) -> JSONResponse:
    _key_guard = require_idempotency_key(request)
    if isinstance(_key_guard, JSONResponse):
        return _key_guard
    project = await services.tasks.update_project(
        tenant, project_id, body.model_dump(exclude_unset=True))
    return JSONResponse(content=_dump(project, PROJECT_FIELDS))


# ------------------------------------------------------------------ milestones
@router.get("/projects/{project_id}/milestones")
@_handle
async def list_milestones(
    project_id: str, services: Services = ServicesDep, tenant: Any = TenantDep,
) -> JSONResponse:
    milestones = await services.tasks.list_milestones(tenant, project_id)
    return JSONResponse(content={
        "items": [_dump(m, MILESTONE_FIELDS) for m in milestones],
        "next_page_token": None})


@router.post("/projects/{project_id}/milestones", status_code=201)
@_handle
async def create_milestone(
    request: Request, project_id: str, body: S.MilestoneCreate,
    services: Services = ServicesDep, tenant: Any = TenantDep,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JSONResponse:
    _key_guard = require_idempotency_key(request)
    if isinstance(_key_guard, JSONResponse):
        return _key_guard
    data = body.model_dump()
    data["project_id"] = project_id
    async def handler() -> tuple[dict, int]:
        milestone = await services.tasks.create_milestone(tenant, data)
        return _dump(milestone, MILESTONE_FIELDS), 201

    return await idempotent_post(request, services, tenant, handler)


@router.patch("/milestones/{milestone_id}")
@_handle
async def update_milestone(
    request: Request,
    milestone_id: str, body: S.MilestoneUpdate,
    services: Services = ServicesDep, tenant: Any = TenantDep,
) -> JSONResponse:
    _key_guard = require_idempotency_key(request)
    if isinstance(_key_guard, JSONResponse):
        return _key_guard
    milestone = await services.tasks.update_milestone(
        tenant, milestone_id, body.model_dump(exclude_unset=True))
    return JSONResponse(content=_dump(milestone, MILESTONE_FIELDS))


# ------------------------------------------------------------------ tasks
@router.get("")
@_handle
async def list_tasks(
    services: Services = ServicesDep, tenant: Any = TenantDep,
    project_id: str | None = Query(default=None),
    assignee: str | None = Query(default=None),
    status: str | None = Query(default=None),
    priority: str | None = Query(default=None),
    parent_id: str | None = Query(default=None),
    page_size: int = Query(default=50), page_token: str | None = Query(default=None),
) -> JSONResponse:
    size, offset = page_params(page_size, page_token)
    items, total = await services.tasks.list_tasks(
        tenant, status=status, assignee_user_id=assignee, project_id=project_id,
        priority=priority, parent_id=parent_id, limit=size, offset=offset)
    return JSONResponse(content=page_response([_dump(t, TASK_FIELDS) for t in items],
                                              total, offset, size))


@router.post("", status_code=201)
@_handle
async def create_task(
    request: Request, body: S.TaskCreate,
    services: Services = ServicesDep, tenant: Any = TenantDep,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JSONResponse:
    _key_guard = require_idempotency_key(request)
    if isinstance(_key_guard, JSONResponse):
        return _key_guard
    async def handler() -> tuple[dict, int]:
        task = await services.tasks.create_task(tenant, body.model_dump(),
                                                idempotency_key=idempotency_key)
        return _dump(task, TASK_FIELDS), 201

    return await idempotent_post(request, services, tenant, handler)


@router.get("/{task_id}")
@_handle
async def get_task(
    task_id: str, services: Services = ServicesDep, tenant: Any = TenantDep,
) -> JSONResponse:
    task = await services.tasks.get_task(tenant, task_id)
    return JSONResponse(content=_dump(task, TASK_FIELDS))


@router.patch("/{task_id}")
@_handle
async def update_task(
    request: Request,
    task_id: str, body: S.TaskUpdate,
    services: Services = ServicesDep, tenant: Any = TenantDep,
) -> JSONResponse:
    _key_guard = require_idempotency_key(request)
    if isinstance(_key_guard, JSONResponse):
        return _key_guard
    task = await services.tasks.update_task(
        tenant, task_id, body.model_dump(exclude_unset=True))
    return JSONResponse(content=_dump(task, TASK_FIELDS))


@router.delete("/{task_id}")
@_handle
async def archive_task(
    request: Request,
    task_id: str, services: Services = ServicesDep, tenant: Any = TenantDep,
) -> JSONResponse:
    _key_guard = require_idempotency_key(request)
    if isinstance(_key_guard, JSONResponse):
        return _key_guard
    task = await services.tasks.archive_task(tenant, task_id)
    return JSONResponse(content=_dump(task, TASK_FIELDS))


@router.post("/{task_id}/transition")
@_handle
async def transition_task(
    request: Request,
    task_id: str, body: S.TaskTransitionRequest,
    services: Services = ServicesDep, tenant: Any = TenantDep,
) -> JSONResponse:
    _key_guard = require_idempotency_key(request)
    if isinstance(_key_guard, JSONResponse):
        return _key_guard
    task = await services.tasks.transition_task(tenant, task_id, body.to, note=body.note)
    return JSONResponse(content=_dump(task, TASK_FIELDS))


@router.put("/{task_id}/assign")
@_handle
async def assign_task(
    request: Request,
    task_id: str, body: S.TaskAssignRequest,
    services: Services = ServicesDep, tenant: Any = TenantDep,
) -> JSONResponse:
    _key_guard = require_idempotency_key(request)
    if isinstance(_key_guard, JSONResponse):
        return _key_guard
    task = await services.tasks.assign_task(tenant, task_id, body.assignee_user_id,
                                            body.assignee_agent_id)
    return JSONResponse(content=_dump(task, TASK_FIELDS))


@router.get("/{task_id}/transitions")
@_handle
async def list_transitions(
    task_id: str, services: Services = ServicesDep, tenant: Any = TenantDep,
) -> JSONResponse:
    transitions = await services.tasks.list_transitions(tenant, task_id)
    return JSONResponse(content={
        "items": [_dump(t, TRANSITION_FIELDS) for t in transitions],
        "next_page_token": None})


@router.post("/{task_id}/delegate")
@_handle
async def delegate_task(
    request: Request,
    task_id: str, body: S.TaskDelegateRequest,
    services: Services = ServicesDep, tenant: Any = TenantDep,
) -> JSONResponse:
    _key_guard = require_idempotency_key(request)
    if isinstance(_key_guard, JSONResponse):
        return _key_guard
    task = await services.tasks.delegate_task(tenant, task_id, body.agent_id,
                                              body.capability, body.goal)
    return JSONResponse(content=_dump(task, TASK_FIELDS))


@router.get("/{task_id}/outcome")
@_handle
async def get_outcome(
    task_id: str, services: Services = ServicesDep, tenant: Any = TenantDep,
) -> JSONResponse:
    outcome = await services.tasks.get_outcome(tenant, task_id)

    def _s(v: Any) -> Any:
        if isinstance(v, str):
            return str(v)
        if isinstance(v, datetime):
            return v.isoformat()
        if isinstance(v, Decimal):
            return str(v)
        return v

    def _row(r: Any, fields: list[str]) -> dict[str, Any]:
        return {f: _s(getattr(r, f)) for f in fields}

    return JSONResponse(content={
        "task_id": outcome["task_id"],
        "status": outcome["status"],
        "plan": outcome["plan"],
        "cost_usd": _s(outcome["cost_usd"]),
        "transitions": [_row(t, TRANSITION_FIELDS) for t in outcome["transitions"]],
        "comments_count": outcome["comments_count"],
        "dependencies": [_row(d, DEPENDENCY_FIELDS) for d in outcome["dependencies"]],
        "subtasks": [_row(t, TASK_FIELDS) for t in outcome["subtasks"]],
    })


# ------------------------------------------------------------------ dependencies
@router.post("/{task_id}/dependencies", status_code=201)
@_handle
async def add_dependency(
    request: Request,
    task_id: str, body: S.TaskDependencyCreate,
    services: Services = ServicesDep, tenant: Any = TenantDep,
) -> JSONResponse:
    _key_guard = require_idempotency_key(request)
    if isinstance(_key_guard, JSONResponse):
        return _key_guard
    dep = await services.tasks.add_dependency(tenant, task_id, body.depends_on_id)
    return JSONResponse(status_code=201, content=_dump(dep, DEPENDENCY_FIELDS))


@router.get("/{task_id}/dependencies")
@_handle
async def list_dependencies(
    task_id: str, services: Services = ServicesDep, tenant: Any = TenantDep,
) -> JSONResponse:
    deps = await services.tasks.list_dependencies(tenant, task_id)
    return JSONResponse(content={
        "items": [_dump(d, DEPENDENCY_FIELDS) for d in deps],
        "next_page_token": None})


# ------------------------------------------------------------------ comments
@router.post("/{task_id}/comments", status_code=201)
@_handle
async def add_comment(
    request: Request,
    task_id: str, body: S.TaskCommentCreate,
    services: Services = ServicesDep, tenant: Any = TenantDep,
) -> JSONResponse:
    _key_guard = require_idempotency_key(request)
    if isinstance(_key_guard, JSONResponse):
        return _key_guard
    comment = await services.tasks.add_comment(tenant, task_id, body.body,
                                               author_type=body.author_type)
    return JSONResponse(status_code=201, content=_dump(comment, COMMENT_FIELDS))


@router.get("/{task_id}/comments")
@_handle
async def list_comments(
    task_id: str, services: Services = ServicesDep, tenant: Any = TenantDep,
    page_size: int = Query(default=50), page_token: str | None = Query(default=None),
) -> JSONResponse:
    size, offset = page_params(page_size, page_token)
    items, total = await services.tasks.list_comments(tenant, task_id, limit=size,
                                                      offset=offset)
    return JSONResponse(content=page_response([_dump(c, COMMENT_FIELDS) for c in items],
                                              total, offset, size))


# ------------------------------------------------------------------ bulk
@router.post("/bulk")
@_handle
async def bulk(
    request: Request,
    body: S.BulkRequest, services: Services = ServicesDep, tenant: Any = TenantDep,
) -> JSONResponse:
    _key_guard = require_idempotency_key(request)
    if isinstance(_key_guard, JSONResponse):
        return _key_guard
    results = await services.tasks.bulk(
        tenant, [op.model_dump(mode="json") for op in body.operations])
    return JSONResponse(content={"results": results})
