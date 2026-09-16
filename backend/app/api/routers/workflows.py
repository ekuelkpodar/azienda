"""Router: workflows. Contract: /API.md §4 (Workflows).

Thin HTTP layer only. All business logic lives in WorkflowService/WorkflowRunner.
"""

from __future__ import annotations

import functools
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse

from app.workflows import schemas as S
from app.workflows.service import WorkflowError

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

router = APIRouter(prefix="/workflows", tags=["workflows"])


# ------------------------------------------------------------------ helpers
def _jsonable(v: Any) -> Any:
    if isinstance(v, str):
        return str(v)
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    return v


def _dump(obj: Any, fields: list[str]) -> dict[str, Any]:
    return {f: _jsonable(getattr(obj, f)) for f in fields}


DEFINITION_FIELDS = ["id", "tenant_id", "name", "description", "autonomy_level",
                     "is_active", "created_at", "updated_at"]
VERSION_FIELDS = ["id", "definition_id", "version", "dag", "published_at", "published_by"]
EVENT_FIELDS = ["seq", "event_type", "payload", "created_at"]



def _handle(fn: Any) -> Any:
    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> JSONResponse:
        try:
            return await fn(*args, **kwargs)
        except WorkflowError as e:
            return domain_error_to_response(e)

    return wrapper


def _execution_read(execution: Any) -> dict[str, Any]:
    d = _dump(execution, ["id", "tenant_id", "definition_id", "version", "status",
                          "input", "cost_usd", "error", "started_at", "finished_at"])
    state = execution.state or {}
    d["current_node"] = state.get("current")
    return d


# ------------------------------------------------------------------ definitions
@router.get("/definitions")
@_handle
async def list_definitions(
    services: Services = ServicesDep, tenant: Any = TenantDep,
    page_size: int = Query(default=50), page_token: str | None = Query(default=None),
) -> JSONResponse:
    size, offset = page_params(page_size, page_token)
    items, total = await services.workflows.list_definitions(
        tenant, limit=size, offset=offset)
    out = []
    for d in items:
        dd = _dump(d, DEFINITION_FIELDS)
        dd["latest_version"] = await services.workflows.latest_version_number(tenant, d.id)
        out.append(dd)
    return JSONResponse(content=page_response(out, total, offset, size))


@router.post("/definitions", status_code=201)
@_handle
async def create_definition(
    request: Request, body: S.DefinitionCreate,
    services: Services = ServicesDep, tenant: Any = TenantDep,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JSONResponse:
    _key_guard = require_idempotency_key(request)
    if isinstance(_key_guard, JSONResponse):
        return _key_guard
    async def handler() -> tuple[dict, int]:
        definition = await services.workflows.create_definition(
            tenant, body.name, body.description, body.autonomy_level, body.dag,
        )
        d = _dump(definition, DEFINITION_FIELDS)
        d["latest_version"] = None
        return d, 201

    return await idempotent_post(request, services, tenant, handler)


@router.get("/definitions/{definition_id}")
@_handle
async def get_definition(
    definition_id: str, services: Services = ServicesDep, tenant: Any = TenantDep,
) -> JSONResponse:
    definition = await services.workflows.get_definition(tenant, definition_id)
    d = _dump(definition, DEFINITION_FIELDS + ["draft_dag"])
    d["latest_version"] = await services.workflows.latest_version_number(
        tenant, definition_id)
    return JSONResponse(content=d)


@router.patch("/definitions/{definition_id}")
@_handle
async def update_definition(
    request: Request,
    definition_id: str, body: S.DefinitionUpdate,
    services: Services = ServicesDep, tenant: Any = TenantDep,
) -> JSONResponse:
    _key_guard = require_idempotency_key(request)
    if isinstance(_key_guard, JSONResponse):
        return _key_guard
    definition = await services.workflows.update_definition(
        tenant, definition_id, body.model_dump(exclude_unset=True, mode="json"))
    d = _dump(definition, DEFINITION_FIELDS + ["draft_dag"])
    d["latest_version"] = await services.workflows.latest_version_number(
        tenant, definition_id)
    return JSONResponse(content=d)


@router.post("/definitions/{definition_id}/publish", status_code=201)
@_handle
async def publish_version(
    request: Request, definition_id: str,
    services: Services = ServicesDep, tenant: Any = TenantDep,
) -> JSONResponse:
    _key_guard = require_idempotency_key(request)
    if isinstance(_key_guard, JSONResponse):
        return _key_guard
    async def handler() -> tuple[dict, int]:
        version = await services.workflows.publish_version(tenant, definition_id)
        return _dump(version, VERSION_FIELDS), 201

    return await idempotent_post(request, services, tenant, handler)


@router.get("/definitions/{definition_id}/versions")
@_handle
async def list_versions(
    definition_id: str, services: Services = ServicesDep, tenant: Any = TenantDep,
) -> JSONResponse:
    versions = await services.workflows.list_versions(tenant, definition_id)
    return JSONResponse(content={
        "items": [_dump(v, VERSION_FIELDS) for v in versions],
        "next_page_token": None})


@router.get("/definitions/{definition_id}/versions/{version}")
@_handle
async def get_version(
    definition_id: str, version: int,
    services: Services = ServicesDep, tenant: Any = TenantDep,
) -> JSONResponse:
    row = await services.workflows.get_version(tenant, definition_id, version)
    return JSONResponse(content=_dump(row, VERSION_FIELDS))


# --------------------------------------------- seed: the complete lead workflow (extension)
@router.post("/definitions/seed/lead-outreach", status_code=201)
@_handle
async def seed_lead_outreach(
    request: Request,
    services: Services = ServicesDep, tenant: Any = TenantDep,
) -> JSONResponse:
    _key_guard = require_idempotency_key(request)
    if isinstance(_key_guard, JSONResponse):
        return _key_guard
    """Idempotently seed + publish the lead-qualification-outreach workflow."""
    definition = await services.workflows.seed_lead_outreach(tenant)
    d = _dump(definition, DEFINITION_FIELDS)
    d["latest_version"] = await services.workflows.latest_version_number(
        tenant, definition.id)
    return JSONResponse(status_code=201, content=d)


# ------------------------------------------------------------------ executions
@router.post("/executions", status_code=201)
@_handle
async def start_execution(
    request: Request, body: S.ExecutionStart,
    services: Services = ServicesDep, tenant: Any = TenantDep,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JSONResponse:
    _key_guard = require_idempotency_key(request)
    if isinstance(_key_guard, JSONResponse):
        return _key_guard
    key = idempotency_key or body.idempotency_key

    async def handler() -> tuple[dict, int]:
        execution = await services.workflows.start_execution(
            tenant, definition_id=body.definition_id,
            definition_name=body.definition_name, version=body.version,
            input=body.input, idempotency_key=key)
        return _execution_read(execution), 201

    if key:
        return await idempotent_post(request, services, tenant, handler)
    body2, status = await handler()
    return JSONResponse(status_code=status, content=body2)


@router.get("/executions")
@_handle
async def list_executions(
    services: Services = ServicesDep, tenant: Any = TenantDep,
    definition_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page_size: int = Query(default=50), page_token: str | None = Query(default=None),
) -> JSONResponse:
    size, offset = page_params(page_size, page_token)
    items, total = await services.workflows.list_executions(
        tenant, definition_id=definition_id, status=status, limit=size, offset=offset)
    return JSONResponse(content=page_response([_execution_read(e) for e in items],
                                              total, offset, size))


@router.get("/executions/{execution_id}")
@_handle
async def get_execution(
    execution_id: str, services: Services = ServicesDep, tenant: Any = TenantDep,
) -> JSONResponse:
    execution = await services.workflows.get_execution(tenant, execution_id)
    return JSONResponse(content=_execution_read(execution))


@router.get("/executions/{execution_id}/inspect")
@_handle
async def inspect_execution(
    execution_id: str, services: Services = ServicesDep, tenant: Any = TenantDep,
) -> JSONResponse:
    data = await services.workflows.inspect(tenant, execution_id)
    out = {k: (_jsonable(v) if not isinstance(v, list) else v) for k, v in data.items()}
    out["started_at"] = _jsonable(data["started_at"])
    out["finished_at"] = _jsonable(data["finished_at"])
    out["events"] = [
        {**e, "created_at": _jsonable(e["created_at"])} for e in data["events"]]
    return JSONResponse(content=out)


@router.get("/executions/{execution_id}/events")
@_handle
async def list_execution_events(
    execution_id: str, services: Services = ServicesDep, tenant: Any = TenantDep,
    page_size: int = Query(default=200), page_token: str | None = Query(default=None),
) -> JSONResponse:
    size, offset = page_params(page_size, page_token)
    items, total = await services.workflows.list_execution_events(
        tenant, execution_id, limit=size, offset=offset)
    return JSONResponse(content=page_response([_dump(e, EVENT_FIELDS) for e in items],
                                              total, offset, size))


@router.post("/executions/{execution_id}/signal")
@_handle
async def signal_execution(
    request: Request,
    execution_id: str, body: S.SignalRequest,
    services: Services = ServicesDep, tenant: Any = TenantDep,
) -> JSONResponse:
    _key_guard = require_idempotency_key(request)
    if isinstance(_key_guard, JSONResponse):
        return _key_guard
    execution = await services.workflows.signal_execution(
        tenant, execution_id, body.signal, body.payload)
    return JSONResponse(content=_execution_read(execution))


@router.post("/executions/{execution_id}/cancel")
@_handle
async def cancel_execution(
    request: Request,
    execution_id: str, body: S.CancelRequest,
    services: Services = ServicesDep, tenant: Any = TenantDep,
) -> JSONResponse:
    _key_guard = require_idempotency_key(request)
    if isinstance(_key_guard, JSONResponse):
        return _key_guard
    execution = await services.workflows.cancel_execution(tenant, execution_id,
                                                         body.reason)
    return JSONResponse(content=_execution_read(execution))

