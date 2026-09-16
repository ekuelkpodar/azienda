"""Router: comms. Thin HTTP layer — all business logic lives in CommsService.

This module also hosts the shared composition container (``BizAppServices``)
for the five business-app routers. Auto-discovery in ``main.py`` mounts this
module's ``router`` under ``/api/v1``.

- TenantContext resolves from X-Tenant-Id / X-User-Id headers (DEV/TEST seam,
  replaced by the api-foundation builder's JWT auth).
- Domain errors map to the API.md §1 error envelope via
  ``bizapp_error_to_response``.
- PermissivePolicyEngine is DEV/TEST only; refused in production.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from app.comms import schemas as S
from app.comms.service import CommsService
from app.core.contracts import TenantContext
from app.finance.service import FinanceService
from app.marketing.service import MarketingService
from app.scheduling.service import SchedulingService
from app.support.service import SupportService

from ._common import (
    RecordingEventBus,
    TenantDep,
    domain_error_to_response,
    error_response,
)


# ------------------------------------------------------------------ composition
class _CommsPortAdapter:
    """Satisfies marketing's local ``CommsSenderPort`` with the comms service.

    Wired here at the composition root — marketing never imports comms.
    """

    def __init__(self, comms: CommsService) -> None:
        self._comms = comms

    async def send_message(self, tenant: TenantContext, *, channel_kind: str,
                           to_address: str, body: str, subject: str | None = None,
                           from_address: str | None = None,
                           idempotency_key: str | None = None,
                           metadata: dict[str, Any] | None = None) -> Any:
        return await self._comms.send_message(
            tenant, channel_kind=S.ChannelKind(channel_kind), to_address=to_address,
            body=body, subject=subject, from_address=from_address,
            idempotency_key=idempotency_key, metadata=metadata)


@dataclass
class BizAppServices:
    """Service container for the five business-app routers."""

    marketing: MarketingService
    support: SupportService
    scheduling: SchedulingService
    comms: CommsService
    finance: FinanceService


def build_bizapp_services(bus: Any | None = None, policy: Any | None = None,
                          nexora: Any | None = None) -> BizAppServices:
    """Compose the container. Production wiring of the real policy engine is
    the api-foundation / governance builders' job; until then explicit
    dev/test doubles. Refuses permissive policy in production."""
    from ._common import PermissivePolicyEngine

    event_bus = bus or RecordingEventBus()
    if policy is None:
        if os.environ.get("AZIENDA_ENVIRONMENT", "development") == "production":
            raise RuntimeError(
                "PermissivePolicyEngine refused in production: wire the governance "
                "PolicyEngine before serving traffic.")
        policy = PermissivePolicyEngine()
    comms = CommsService(policy=policy, events=event_bus)
    marketing = MarketingService(comms=_CommsPortAdapter(comms), policy=policy,
                                 events=event_bus)
    support = SupportService(events=event_bus)  # support takes no governance policy
    scheduling = SchedulingService(policy=policy, events=event_bus)
    finance = FinanceService(policy=policy, events=event_bus, nexora=nexora)
    return BizAppServices(marketing=marketing, support=support,
                          scheduling=scheduling, comms=comms, finance=finance)


def get_bizapp_services(request: Request) -> BizAppServices:
    """FastAPI dependency. Tests override with ``build_bizapp_services(...)``."""
    services = getattr(request.app.state, "bizapp_services", None)
    if services is None:
        services = build_bizapp_services()
        request.app.state.bizapp_services = services
    return services


BizAppDep = Depends(get_bizapp_services)


# ------------------------------------------------------------------ errors
# Domain error class name -> (API.md error code, HTTP status). Names not listed
# fall through to _common.domain_error_to_response (PolicyDeniedError -> 403).
_ERROR_MAP: dict[str, tuple[str, int]] = {
    "ChannelNotFoundError": ("not_found", 404),
    "MessageNotFoundError": ("not_found", 404),
    "TemplateNotFoundError": ("not_found", 404),
    "NoActiveChannelError": ("no_active_channel", 409),
    "TemplateRenderError": ("template_error", 422),
    "RateLimitedError": ("rate_limited", 429),
    "ApprovalRequiredError": ("approval_required", 403),
    "CampaignNotFoundError": ("not_found", 404),
    "AudienceNotFoundError": ("not_found", 404),
    "AssetNotFoundError": ("not_found", 404),
    "StepNotFoundError": ("not_found", 404),
    "CampaignStateError": ("illegal_transition", 409),
    "SegmentFilterError": ("validation_error", 400),
    "TicketNotFoundError": ("not_found", 404),
    "TicketStateError": ("illegal_transition", 409),
    "MacroNotFoundError": ("not_found", 404),
    "ArticleNotFoundError": ("not_found", 404),
    "DraftAssistantUnavailable": ("unavailable", 503),
    "SupportError": ("support_error", 400),
    "CalendarNotFoundError": ("not_found", 404),
    "BookingNotFoundError": ("not_found", 404),
    "BookingConflictError": ("booking_conflict", 409),
    "BookingStateError": ("illegal_transition", 409),
    "PermissionDeniedError": ("forbidden", 403),
    "SchedulingError": ("scheduling_error", 400),
    "FinanceNotFoundError": ("not_found", 404),
    "InvoiceStateError": ("illegal_transition", 409),
    "IssuedInvoiceImmutableError": ("immutable", 409),
    "FinanceError": ("finance_error", 400),
}


def bizapp_error_to_response(exc: Exception) -> Response:
    name = type(exc).__name__
    if name in _ERROR_MAP:
        code, status = _ERROR_MAP[name]
        return error_response(code, str(exc), None, status)
    return domain_error_to_response(exc)


# ------------------------------------------------------------------ serialization
def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return {k: _jsonable(v) for k, v in value.model_dump().items()}
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, Decimal):
        return str(value)  # money keeps full precision
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


async def _handle(awaitable: Any, status_code: int = 200) -> Response:
    """Run a service call; map domain errors to the API.md envelope."""
    try:
        result = await awaitable
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - mapped to the error envelope
        return bizapp_error_to_response(exc)
    if status_code == 204:
        return Response(status_code=204)  # 204 must not carry a body
    return JSONResponse(status_code=status_code, content=_jsonable(result))


# ------------------------------------------------------------------ router
router = APIRouter(prefix="/comms", tags=["comms"])


@router.post("/channels", status_code=201)
async def create_channel(data: S.ChannelCreate, tenant: TenantContext = TenantDep,
                         svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.comms.create_channel(tenant, data), 201)


@router.get("/channels")
async def list_channels(kind: S.ChannelKind | None = None,
                        tenant: TenantContext = TenantDep,
                        svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.comms.list_channels(tenant, kind))


@router.get("/channels/{channel_id}")
async def get_channel(channel_id: str, tenant: TenantContext = TenantDep,
                      svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.comms.get_channel(tenant, channel_id))


@router.patch("/channels/{channel_id}")
async def update_channel(channel_id: str, data: S.ChannelUpdate,
                         tenant: TenantContext = TenantDep,
                         svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.comms.update_channel(tenant, channel_id, data))


@router.post("/channels/{channel_id}/deactivate")
async def deactivate_channel(channel_id: str, tenant: TenantContext = TenantDep,
                             svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.comms.deactivate_channel(tenant, channel_id))


@router.post("/templates", status_code=201)
async def create_template(data: S.MessageTemplateCreate,
                          tenant: TenantContext = TenantDep,
                          svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.comms.create_template(tenant, data), 201)


@router.get("/templates")
async def list_templates(kind: S.ChannelKind | None = None,
                         tenant: TenantContext = TenantDep,
                         svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.comms.list_templates(tenant, kind))


@router.post("/templates/{template_id}/render")
async def render_template(template_id: str, variables: dict[str, Any],
                          tenant: TenantContext = TenantDep,
                          svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.comms.render_template(tenant, template_id, variables))


@router.post("/messages", status_code=201)
async def send_message(
    data: S.MessageSend, tenant: TenantContext = TenantDep,
    svc: BizAppServices = BizAppDep,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Response:
    if idempotency_key and not data.idempotency_key:
        data = data.model_copy(update={"idempotency_key": idempotency_key})
    return await _handle(svc.comms.send(tenant, data), 201)


@router.get("/messages")
async def list_messages(limit: int = Query(default=50, le=200),
                        offset: int = Query(default=0, ge=0),
                        tenant: TenantContext = TenantDep,
                        svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.comms.list_messages(tenant, limit=limit, offset=offset))


@router.get("/messages/{message_id}")
async def get_message(message_id: str, tenant: TenantContext = TenantDep,
                      svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.comms.get_message(tenant, message_id))


@router.post("/messages/{message_id}/delivery")
async def record_delivery(message_id: str, update: S.DeliveryUpdate,
                          tenant: TenantContext = TenantDep,
                          svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.comms.record_delivery(tenant, message_id, update))


@router.get("/usage")
async def usage_summary(period_start: datetime, period_end: datetime,
                        tenant: TenantContext = TenantDep,
                        svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.comms.usage_summary(tenant, period_start, period_end))


# Re-exported so other bizapp routers share one composition seam.
__all__ = ["BizAppDep", "BizAppServices", "_handle", "bizapp_error_to_response",
           "build_bizapp_services", "get_bizapp_services", "router"]
