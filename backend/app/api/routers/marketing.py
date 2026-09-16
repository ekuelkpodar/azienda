"""Router: marketing. Thin HTTP layer — all business logic lives in MarketingService.

Auto-discovery in ``main.py`` mounts this module's ``router`` under ``/api/v1``.
Composition (``BizAppServices``), error mapping, and serialization live in
``.comms`` — the shared router seam for the five business-app routers.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse, Response

from app.core.contracts import TenantContext
from app.marketing import schemas as S
from app.marketing.segmentation import SegmentFilterError, segment_contacts

from ._common import TenantDep
from .comms import BizAppDep, BizAppServices, _handle

router = APIRouter(prefix="/marketing", tags=["marketing"])


# ------------------------------------------------------------------ campaigns
@router.post("/campaigns", status_code=201)
async def create_campaign(data: S.CampaignCreate, tenant: TenantContext = TenantDep,
                          svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.marketing.create_campaign(tenant, data), 201)


@router.get("/campaigns")
async def list_campaigns(limit: int = 50, offset: int = 0,
                         tenant: TenantContext = TenantDep,
                         svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.marketing.list_campaigns(tenant, limit=limit, offset=offset))


@router.get("/campaigns/{campaign_id}")
async def get_campaign(campaign_id: str, tenant: TenantContext = TenantDep,
                       svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.marketing.get_campaign(tenant, campaign_id))


@router.patch("/campaigns/{campaign_id}")
async def update_campaign(campaign_id: str, data: S.CampaignUpdate,
                          tenant: TenantContext = TenantDep,
                          svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.marketing.update_campaign(tenant, campaign_id, data))


@router.post("/campaigns/{campaign_id}/archive")
async def archive_campaign(campaign_id: str, tenant: TenantContext = TenantDep,
                           svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.marketing.archive_campaign(tenant, campaign_id))


@router.post("/campaigns/{campaign_id}/steps", status_code=201)
async def add_step(campaign_id: str, data: S.CampaignStepCreate,
                   tenant: TenantContext = TenantDep,
                   svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.marketing.add_step(tenant, campaign_id, data), 201)


@router.get("/campaigns/{campaign_id}/steps")
async def list_steps(campaign_id: str, tenant: TenantContext = TenantDep,
                     svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.marketing.list_steps(tenant, campaign_id))


@router.patch("/campaigns/{campaign_id}/steps/{step_id}")
async def update_step(campaign_id: str, step_id: str, data: S.CampaignStepUpdate,
                      tenant: TenantContext = TenantDep,
                      svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.marketing.update_step(tenant, step_id, data))


@router.delete("/campaigns/{campaign_id}/steps/{step_id}", status_code=204)
async def delete_step(campaign_id: str, step_id: str,
                      tenant: TenantContext = TenantDep,
                      svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.marketing.delete_step(tenant, step_id), 204)


@router.post("/campaigns/{campaign_id}/launch")
async def launch_campaign(
    campaign_id: str, body: dict[str, Any] | None = None,
    tenant: TenantContext = TenantDep, svc: BizAppServices = BizAppDep,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Response:
    contacts = (body or {}).get("contacts")
    return await _handle(
        svc.marketing.launch_campaign(tenant, campaign_id, contacts=contacts,
                                      idempotency_key=idempotency_key))


@router.get("/campaigns/{campaign_id}/analytics")
async def campaign_analytics(campaign_id: str, tenant: TenantContext = TenantDep,
                             svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.marketing.campaign_analytics(tenant, campaign_id))


@router.get("/campaigns/{campaign_id}/attribution")
async def attribution_report(campaign_id: str, tenant: TenantContext = TenantDep,
                             svc: BizAppServices = BizAppDep) -> Response:
    # Honest stub: raises AttributionNotImplementedError until CRM opportunity
    # linkage exists. Not mapped to _ERROR_MAP on purpose — 501.
    try:
        result = await svc.marketing.attribution_report(tenant, campaign_id)
    except Exception as exc:  # noqa: BLE001 - known honest stub
        from ._common import error_response
        return error_response("not_implemented", str(exc), None, 501)
    return JSONResponse(status_code=200, content=result)


# ------------------------------------------------------------------ audiences
@router.post("/audiences", status_code=201)
async def create_audience(data: S.AudienceCreate, tenant: TenantContext = TenantDep,
                          svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.marketing.create_audience(tenant, data), 201)


@router.get("/audiences")
async def list_audiences(tenant: TenantContext = TenantDep,
                         svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.marketing.list_audiences(tenant))


@router.get("/audiences/{audience_id}")
async def get_audience(audience_id: str, tenant: TenantContext = TenantDep,
                       svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.marketing.get_audience(tenant, audience_id))


@router.patch("/audiences/{audience_id}")
async def update_audience(audience_id: str, data: S.AudienceUpdate,
                          tenant: TenantContext = TenantDep,
                          svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.marketing.update_audience(tenant, audience_id, data))


@router.post("/audiences/{audience_id}/resolve")
async def resolve_audience(audience_id: str, body: dict[str, Any],
                           tenant: TenantContext = TenantDep,
                           svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(
        svc.marketing.resolve_audience(tenant, audience_id, body.get("contacts", [])))


@router.post("/segments/evaluate")
async def evaluate_segment(body: dict[str, Any]) -> Response:
    """Pure segmentation preview — no tenant needed, no persistence."""
    try:
        matched = segment_contacts(body.get("filter", {}), body.get("contacts", []))
    except SegmentFilterError as exc:
        from ._common import error_response
        return error_response("validation_error", str(exc), None, 400)
    return JSONResponse(status_code=200,
                        content={"matched_count": len(matched), "matched": matched})


# ------------------------------------------------------------------ assets
@router.post("/assets", status_code=201)
async def create_asset(data: S.ContentAssetCreate, tenant: TenantContext = TenantDep,
                       svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.marketing.create_asset(tenant, data), 201)


@router.get("/assets")
async def list_assets(kind: str | None = None, tenant: TenantContext = TenantDep,
                      svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.marketing.list_assets(tenant, kind=kind))


@router.get("/assets/{asset_id}")
async def get_asset(asset_id: str, tenant: TenantContext = TenantDep,
                    svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.marketing.get_asset(tenant, asset_id))


@router.post("/assets/{asset_id}/publish")
async def publish_asset(asset_id: str, tenant: TenantContext = TenantDep,
                        svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.marketing.publish_asset(tenant, asset_id))
