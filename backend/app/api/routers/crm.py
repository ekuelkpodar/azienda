"""Router: crm. Contract: /API.md §4 (CRM).

Thin HTTP layer only: schema validation, tenant scoping, pagination,
idempotency, error envelope. All business logic lives in CRMService.
"""

from __future__ import annotations

import functools
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse

from app.crm import schemas as S
from app.crm.service import CRMError

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

router = APIRouter(prefix="/crm", tags=["crm"])


# ------------------------------------------------------------------ serialization
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


ORG_FIELDS = ["id", "tenant_id", "name", "domain", "industry", "size_band",
              "lifecycle_stage", "owner_id", "tags", "custom", "is_archived",
              "created_at", "updated_at"]
CONTACT_FIELDS = ["id", "tenant_id", "org_id", "first_name", "last_name", "email",
                  "phone", "title", "tags", "custom", "is_archived",
                  "created_at", "updated_at"]
LEAD_FIELDS = ["id", "tenant_id", "contact_id", "org_id", "source", "status", "score",
               "score_breakdown", "owner_id", "custom", "converted_at",
               "created_at", "updated_at"]
OPP_FIELDS = ["id", "tenant_id", "pipeline_id", "stage_id", "org_id", "contact_id",
              "name", "amount", "currency", "close_date", "owner_id", "custom",
              "is_archived", "created_at", "updated_at"]
ACTIVITY_FIELDS = ["id", "tenant_id", "subject_type", "subject_id", "type", "body",
                   "occurred_at", "author_id", "created_at"]


def _handle(fn: Any) -> Any:
    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> JSONResponse:
        try:
            return await fn(*args, **kwargs)
        except CRMError as e:
            return domain_error_to_response(e)

    return wrapper


# ------------------------------------------------------------------ organizations
@router.get("/organizations")
@_handle
async def list_organizations(
    services: Services = ServicesDep, tenant: Any = TenantDep,
    search: str | None = Query(default=None), tag: str | None = Query(default=None),
    owner_id: str | None = Query(default=None),
    page_size: int = Query(default=50), page_token: str | None = Query(default=None),
) -> JSONResponse:
    size, offset = page_params(page_size, page_token)
    items, total = await services.crm.list_organizations(
        tenant, search=search, tag=tag, owner_id=owner_id, limit=size, offset=offset)
    return JSONResponse(content=page_response([_dump(o, ORG_FIELDS) for o in items],
                                              total, offset, size))


@router.post("/organizations", status_code=201)
@_handle
async def create_organization(
    request: Request, body: S.OrganizationCreate,
    services: Services = ServicesDep, tenant: Any = TenantDep,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JSONResponse:
    _key_guard = require_idempotency_key(request)
    if isinstance(_key_guard, JSONResponse):
        return _key_guard
    async def handler() -> tuple[dict, int]:
        org = await services.crm.create_organization(tenant, body.model_dump(),
                                                     idempotency_key=idempotency_key)
        return _dump(org, ORG_FIELDS), 201

    return await idempotent_post(request, services, tenant, handler)


@router.get("/organizations/{org_id}")
@_handle
async def get_organization(
    org_id: str, services: Services = ServicesDep, tenant: Any = TenantDep,
) -> JSONResponse:
    org = await services.crm.get_organization(tenant, org_id)
    return JSONResponse(content=_dump(org, ORG_FIELDS))


@router.patch("/organizations/{org_id}")
@_handle
async def update_organization(
    request: Request,
    org_id: str, body: S.OrganizationUpdate,
    services: Services = ServicesDep, tenant: Any = TenantDep,
) -> JSONResponse:
    _key_guard = require_idempotency_key(request)
    if isinstance(_key_guard, JSONResponse):
        return _key_guard
    org = await services.crm.update_organization(
        tenant, org_id, body.model_dump(exclude_unset=True))
    return JSONResponse(content=_dump(org, ORG_FIELDS))


@router.delete("/organizations/{org_id}")
@_handle
async def archive_organization(
    request: Request,
    org_id: str, services: Services = ServicesDep, tenant: Any = TenantDep,
) -> JSONResponse:
    _key_guard = require_idempotency_key(request)
    if isinstance(_key_guard, JSONResponse):
        return _key_guard
    org = await services.crm.archive_organization(tenant, org_id)
    return JSONResponse(content=_dump(org, ORG_FIELDS))


# ------------------------------------------------------------------ contacts
@router.get("/contacts")
@_handle
async def list_contacts(
    services: Services = ServicesDep, tenant: Any = TenantDep,
    org_id: str | None = Query(default=None),
    tag: str | None = Query(default=None), search: str | None = Query(default=None),
    page_size: int = Query(default=50), page_token: str | None = Query(default=None),
) -> JSONResponse:
    size, offset = page_params(page_size, page_token)
    items, total = await services.crm.list_contacts(
        tenant, org_id=org_id, tag=tag, search=search, limit=size, offset=offset)
    return JSONResponse(content=page_response([_dump(c, CONTACT_FIELDS) for c in items],
                                              total, offset, size))


@router.post("/contacts", status_code=201)
@_handle
async def create_contact(
    request: Request, body: S.ContactCreate,
    services: Services = ServicesDep, tenant: Any = TenantDep,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JSONResponse:
    _key_guard = require_idempotency_key(request)
    if isinstance(_key_guard, JSONResponse):
        return _key_guard
    async def handler() -> tuple[dict, int]:
        contact = await services.crm.create_contact(tenant, body.model_dump(),
                                                    idempotency_key=idempotency_key)
        return _dump(contact, CONTACT_FIELDS), 201

    return await idempotent_post(request, services, tenant, handler)


@router.get("/contacts/{contact_id}")
@_handle
async def get_contact(
    contact_id: str, services: Services = ServicesDep, tenant: Any = TenantDep,
) -> JSONResponse:
    contact = await services.crm.get_contact(tenant, contact_id)
    return JSONResponse(content=_dump(contact, CONTACT_FIELDS))


@router.patch("/contacts/{contact_id}")
@_handle
async def update_contact(
    request: Request,
    contact_id: str, body: S.ContactUpdate,
    services: Services = ServicesDep, tenant: Any = TenantDep,
) -> JSONResponse:
    _key_guard = require_idempotency_key(request)
    if isinstance(_key_guard, JSONResponse):
        return _key_guard
    contact = await services.crm.update_contact(
        tenant, contact_id, body.model_dump(exclude_unset=True))
    return JSONResponse(content=_dump(contact, CONTACT_FIELDS))


# ------------------------------------------------------------------ leads
@router.get("/leads")
@_handle
async def list_leads(
    services: Services = ServicesDep, tenant: Any = TenantDep,
    status: str | None = Query(default=None),
    owner_id: str | None = Query(default=None),
    source: str | None = Query(default=None),
    min_score: int | None = Query(default=None),
    page_size: int = Query(default=50), page_token: str | None = Query(default=None),
) -> JSONResponse:
    size, offset = page_params(page_size, page_token)
    items, total = await services.crm.list_leads(
        tenant, status=status, owner_id=owner_id, source=source,
        min_score=min_score, limit=size, offset=offset)
    return JSONResponse(content=page_response([_dump(ld, LEAD_FIELDS) for ld in items],
                                              total, offset, size))


@router.post("/leads", status_code=201)
@_handle
async def create_lead(
    request: Request, body: S.LeadCreate,
    services: Services = ServicesDep, tenant: Any = TenantDep,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JSONResponse:
    _key_guard = require_idempotency_key(request)
    if isinstance(_key_guard, JSONResponse):
        return _key_guard
    async def handler() -> tuple[dict, int]:
        lead = await services.crm.create_lead(tenant, body.model_dump(),
                                              idempotency_key=idempotency_key)
        return _dump(lead, LEAD_FIELDS), 201

    return await idempotent_post(request, services, tenant, handler)


@router.get("/leads/{lead_id}")
@_handle
async def get_lead(
    lead_id: str, services: Services = ServicesDep, tenant: Any = TenantDep,
) -> JSONResponse:
    lead = await services.crm.get_lead(tenant, lead_id)
    return JSONResponse(content=_dump(lead, LEAD_FIELDS))


@router.patch("/leads/{lead_id}")
@_handle
async def update_lead(
    request: Request,
    lead_id: str, body: S.LeadUpdate,
    services: Services = ServicesDep, tenant: Any = TenantDep,
) -> JSONResponse:
    _key_guard = require_idempotency_key(request)
    if isinstance(_key_guard, JSONResponse):
        return _key_guard
    lead = await services.crm.update_lead(tenant, lead_id, body.model_dump(exclude_unset=True))
    return JSONResponse(content=_dump(lead, LEAD_FIELDS))


@router.post("/leads/{lead_id}/score")
@_handle
async def score_lead(
    request: Request,
    lead_id: str, services: Services = ServicesDep, tenant: Any = TenantDep,
) -> JSONResponse:
    _key_guard = require_idempotency_key(request)
    if isinstance(_key_guard, JSONResponse):
        return _key_guard
    """Recompute the transparent rule-based lead score (extension beyond API.md)."""
    score, breakdown = await services.crm.score_lead(tenant, lead_id)
    return JSONResponse(content={"lead_id": str(lead_id), "score": score,
                                 "breakdown": breakdown})


@router.post("/leads/{lead_id}/convert", status_code=201)
@_handle
async def convert_lead(
    request: Request, lead_id: str,
    services: Services = ServicesDep, tenant: Any = TenantDep,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JSONResponse:
    _key_guard = require_idempotency_key(request)
    if isinstance(_key_guard, JSONResponse):
        return _key_guard
    async def handler() -> tuple[dict, int]:
        org, contact, opp = await services.crm.convert_lead(
            tenant, lead_id, idempotency_key=idempotency_key)
        return {
            "lead_id": str(lead_id),
            "organization_id": str(org.id),
            "contact_id": str(contact.id),
            "opportunity_id": str(opp.id),
        }, 201

    return await idempotent_post(request, services, tenant, handler)


# ------------------------------------------------------------------ pipelines
@router.get("/pipelines")
@_handle
async def list_pipelines(
    services: Services = ServicesDep, tenant: Any = TenantDep,
) -> JSONResponse:
    pipelines = await services.crm.list_pipelines(tenant)
    out = []
    for p in pipelines:
        _, stages = await services.crm.get_pipeline_with_stages(tenant, p.id)
        d = _dump(p, ["id", "tenant_id", "name", "object_type", "is_active"])
        d["stages"] = [_dump(s, ["id", "name", "position", "probability",
                                 "is_closed_won", "is_closed_lost"]) for s in stages]
        out.append(d)
    return JSONResponse(content={"items": out, "next_page_token": None})


@router.post("/pipelines", status_code=201)
@_handle
async def create_pipeline(
    request: Request, body: S.PipelineCreate,
    services: Services = ServicesDep, tenant: Any = TenantDep,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JSONResponse:
    _key_guard = require_idempotency_key(request)
    if isinstance(_key_guard, JSONResponse):
        return _key_guard
    async def handler() -> tuple[dict, int]:
        pipeline = await services.crm.create_pipeline(
            tenant, body.name, body.object_type,
            [s.model_dump(mode="json") for s in body.stages],
            idempotency_key=idempotency_key)
        _, stages = await services.crm.get_pipeline_with_stages(tenant, pipeline.id)
        d = _dump(pipeline, ["id", "tenant_id", "name", "object_type", "is_active"])
        d["stages"] = [_dump(s, ["id", "name", "position", "probability",
                                 "is_closed_won", "is_closed_lost"]) for s in stages]
        return d, 201

    return await idempotent_post(request, services, tenant, handler)


@router.get("/pipelines/{pipeline_id}")
@_handle
async def get_pipeline(
    pipeline_id: str, services: Services = ServicesDep, tenant: Any = TenantDep,
) -> JSONResponse:
    pipeline, stages = await services.crm.get_pipeline_with_stages(tenant, pipeline_id)
    d = _dump(pipeline, ["id", "tenant_id", "name", "object_type", "is_active"])
    d["stages"] = [_dump(s, ["id", "name", "position", "probability",
                             "is_closed_won", "is_closed_lost"]) for s in stages]
    return JSONResponse(content=d)


# ------------------------------------------------------------------ opportunities
@router.get("/opportunities")
@_handle
async def list_opportunities(
    services: Services = ServicesDep, tenant: Any = TenantDep,
    pipeline_id: str | None = Query(default=None),
    stage_id: str | None = Query(default=None),
    owner_id: str | None = Query(default=None),
    page_size: int = Query(default=50), page_token: str | None = Query(default=None),
) -> JSONResponse:
    size, offset = page_params(page_size, page_token)
    items, total = await services.crm.list_opportunities(
        tenant, pipeline_id=pipeline_id, stage_id=stage_id, owner_id=owner_id,
        limit=size, offset=offset)
    return JSONResponse(content=page_response([_dump(o, OPP_FIELDS) for o in items],
                                              total, offset, size))


@router.post("/opportunities", status_code=201)
@_handle
async def create_opportunity(
    request: Request, body: S.OpportunityCreate,
    services: Services = ServicesDep, tenant: Any = TenantDep,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JSONResponse:
    _key_guard = require_idempotency_key(request)
    if isinstance(_key_guard, JSONResponse):
        return _key_guard
    async def handler() -> tuple[dict, int]:
        opp = await services.crm.create_opportunity(tenant, body.model_dump(),
                                                    idempotency_key=idempotency_key)
        return _dump(opp, OPP_FIELDS), 201

    return await idempotent_post(request, services, tenant, handler)


@router.get("/opportunities/{opp_id}")
@_handle
async def get_opportunity(
    opp_id: str, services: Services = ServicesDep, tenant: Any = TenantDep,
) -> JSONResponse:
    opp = await services.crm.get_opportunity(tenant, opp_id)
    return JSONResponse(content=_dump(opp, OPP_FIELDS))


@router.patch("/opportunities/{opp_id}")
@_handle
async def update_opportunity(
    request: Request,
    opp_id: str, body: S.OpportunityUpdate,
    services: Services = ServicesDep, tenant: Any = TenantDep,
) -> JSONResponse:
    _key_guard = require_idempotency_key(request)
    if isinstance(_key_guard, JSONResponse):
        return _key_guard
    opp = await services.crm.update_opportunity(
        tenant, opp_id, body.model_dump(exclude_unset=True))
    return JSONResponse(content=_dump(opp, OPP_FIELDS))


@router.post("/opportunities/{opp_id}/move")
@_handle
async def move_opportunity(
    request: Request,
    opp_id: str, body: S.OpportunityMove,
    services: Services = ServicesDep, tenant: Any = TenantDep,
) -> JSONResponse:
    _key_guard = require_idempotency_key(request)
    if isinstance(_key_guard, JSONResponse):
        return _key_guard
    opp = await services.crm.move_opportunity(tenant, opp_id, body.stage_id)
    return JSONResponse(content=_dump(opp, OPP_FIELDS))


# ------------------------------------------------------------------ activities
@router.get("/activities")
@_handle
async def list_activities(
    services: Services = ServicesDep, tenant: Any = TenantDep,
    subject_type: str | None = Query(default=None),
    subject_id: str | None = Query(default=None),
    type: str | None = Query(default=None, alias="type"),
    page_size: int = Query(default=50), page_token: str | None = Query(default=None),
) -> JSONResponse:
    size, offset = page_params(page_size, page_token)
    items, total = await services.crm.list_activities(
        tenant, subject_type=subject_type, subject_id=subject_id, type=type,
        limit=size, offset=offset)
    return JSONResponse(content=page_response([_dump(a, ACTIVITY_FIELDS) for a in items],
                                              total, offset, size))


@router.post("/activities", status_code=201)
@_handle
async def log_activity(
    request: Request, body: S.ActivityCreate,
    services: Services = ServicesDep, tenant: Any = TenantDep,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JSONResponse:
    _key_guard = require_idempotency_key(request)
    if isinstance(_key_guard, JSONResponse):
        return _key_guard
    async def handler() -> tuple[dict, int]:
        activity = await services.crm.log_activity(
            tenant, body.subject_type, body.subject_id, body.type, body.body,
            occurred_at=body.occurred_at)
        return _dump(activity, ACTIVITY_FIELDS), 201

    return await idempotent_post(request, services, tenant, handler)


# ------------------------------------------------------------------ custom fields (extension)
@router.get("/custom-fields")
@_handle
async def list_custom_fields(
    services: Services = ServicesDep, tenant: Any = TenantDep,
    object_type: str | None = Query(default=None),
) -> JSONResponse:
    fields = await services.crm.list_custom_fields(tenant, object_type)
    return JSONResponse(content={
        "items": [_dump(f, ["id", "tenant_id", "object_type", "name",
                            "field_type", "required", "options"]) for f in fields],
        "next_page_token": None})


@router.post("/custom-fields", status_code=201)
@_handle
async def create_custom_field(
    request: Request,
    body: S.CustomFieldDefinitionCreate,
    services: Services = ServicesDep, tenant: Any = TenantDep,
) -> JSONResponse:
    _key_guard = require_idempotency_key(request)
    if isinstance(_key_guard, JSONResponse):
        return _key_guard
    cfd = await services.crm.create_custom_field(tenant, body.model_dump())
    return JSONResponse(status_code=201, content=_dump(
        cfd, ["id", "tenant_id", "object_type", "name", "field_type",
              "required", "options"]))


# ------------------------------------------------------------------ relationships (extension)
@router.post("/relationships", status_code=201)
@_handle
async def create_relationship(
    request: Request,
    body: S.RelationshipCreate,
    services: Services = ServicesDep, tenant: Any = TenantDep,
) -> JSONResponse:
    _key_guard = require_idempotency_key(request)
    if isinstance(_key_guard, JSONResponse):
        return _key_guard
    rel = await services.crm.link(tenant, body.from_type, body.from_id,
                                  body.to_type, body.to_id, body.relation)
    return JSONResponse(status_code=201, content=_dump(
        rel, ["id", "from_type", "from_id", "to_type", "to_id", "relation"]))


@router.get("/relationships")
@_handle
async def list_relationships(
    subject_type: str = Query(), subject_id: str = Query(),
    services: Services = ServicesDep, tenant: Any = TenantDep,
) -> JSONResponse:
    rels = await services.crm.list_relationships(tenant, subject_type, subject_id)
    return JSONResponse(content={
        "items": [_dump(r, ["id", "from_type", "from_id", "to_type", "to_id",
                            "relation"]) for r in rels],
        "next_page_token": None})
