"""Router: scheduling. Thin HTTP layer — all business logic lives in SchedulingService.

Auto-discovery in ``main.py`` mounts this module's ``router`` under ``/api/v1``.
Composition (``BizAppServices``), error mapping, and serialization live in
``.comms`` — the shared router seam for the five business-app routers.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Header, Query
from fastapi.responses import Response

from app.core.contracts import TenantContext
from app.scheduling import schemas as S

from ._common import TenantDep
from .comms import BizAppDep, BizAppServices, _handle

router = APIRouter(prefix="/scheduling", tags=["scheduling"])


# ------------------------------------------------------------------ calendars
@router.post("/calendars", status_code=201)
async def create_calendar(data: S.CalendarCreate, tenant: TenantContext = TenantDep,
                          svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.scheduling.create_calendar(tenant, data), 201)


@router.get("/calendars")
async def list_calendars(tenant: TenantContext = TenantDep,
                         svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.scheduling.list_calendars(tenant))


@router.get("/calendars/{calendar_id}")
async def get_calendar(calendar_id: str, tenant: TenantContext = TenantDep,
                       svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.scheduling.get_calendar(tenant, calendar_id))


@router.patch("/calendars/{calendar_id}")
async def update_calendar(calendar_id: str, data: S.CalendarUpdate,
                          tenant: TenantContext = TenantDep,
                          svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.scheduling.update_calendar(tenant, calendar_id, data))


@router.delete("/calendars/{calendar_id}", status_code=204)
async def delete_calendar(calendar_id: str, tenant: TenantContext = TenantDep,
                          svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.scheduling.delete_calendar(tenant, calendar_id), 204)


# ------------------------------------------------------- availability & events
@router.post("/calendars/{calendar_id}/availability-rules", status_code=201)
async def set_availability_rule(calendar_id: str, data: S.AvailabilityRuleCreate,
                                tenant: TenantContext = TenantDep,
                                svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(
        svc.scheduling.set_availability_rule(tenant, calendar_id, data), 201)


@router.get("/calendars/{calendar_id}/availability-rules")
async def list_availability_rules(calendar_id: str, tenant: TenantContext = TenantDep,
                                  svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.scheduling.list_availability_rules(tenant, calendar_id))


@router.post("/calendars/{calendar_id}/events", status_code=201)
async def create_event(calendar_id: str, data: S.CalendarEventCreate,
                       tenant: TenantContext = TenantDep,
                       svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.scheduling.create_event(tenant, calendar_id, data), 201)


@router.get("/calendars/{calendar_id}/availability")
async def availability(calendar_id: str, start: datetime, end: datetime,
                       duration_minutes: int = Query(gt=0, le=1440),
                       buffer_minutes: int = Query(default=0, ge=0),
                       min_notice_minutes: int = Query(default=0, ge=0),
                       tenant: TenantContext = TenantDep,
                       svc: BizAppServices = BizAppDep) -> Response:
    query = S.AvailabilityQuery(start=start, end=end,
                                duration_minutes=duration_minutes,
                                buffer_minutes=buffer_minutes,
                                min_notice_minutes=min_notice_minutes)
    return await _handle(svc.scheduling.availability(tenant, calendar_id, query))


# ------------------------------------------------------------------ bookings
@router.post("/bookings", status_code=201)
async def book(calendar_id: str, data: S.BookingCreate,
               tenant: TenantContext = TenantDep, svc: BizAppServices = BizAppDep,
               idempotency_key: str | None = Header(default=None,
                                                   alias="Idempotency-Key")) -> Response:
    if idempotency_key and not data.idempotency_key:
        data = data.model_copy(update={"idempotency_key": idempotency_key})
    return await _handle(svc.scheduling.book(tenant, calendar_id, data), 201)


@router.get("/bookings")
async def list_bookings(calendar_id: str | None = None,
                        tenant: TenantContext = TenantDep,
                        svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.scheduling.list_bookings(tenant, calendar_id=calendar_id))


@router.get("/bookings/{booking_id}")
async def get_booking(booking_id: str, tenant: TenantContext = TenantDep,
                      svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.scheduling.get_booking(tenant, booking_id))


@router.post("/bookings/{booking_id}/reschedule")
async def reschedule(booking_id: str, data: S.BookingReschedule,
                     tenant: TenantContext = TenantDep,
                     svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.scheduling.reschedule(tenant, booking_id, data))


@router.post("/bookings/{booking_id}/cancel")
async def cancel(booking_id: str, data: S.BookingCancel,
                 tenant: TenantContext = TenantDep,
                 svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.scheduling.cancel(tenant, booking_id, data))


@router.get("/reminders/due")
async def due_reminders(tenant: TenantContext = TenantDep,
                        svc: BizAppServices = BizAppDep) -> Response:
    """Due booking reminders — consumed by the ARQ worker, which delivers via comms."""
    return await _handle(svc.scheduling.due_reminders(tenant))


@router.post("/bookings/{booking_id}/reminders/sent")
async def mark_reminder_sent(booking_id: str, offset_minutes: int, channel: str,
                             tenant: TenantContext = TenantDep,
                             svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.scheduling.mark_reminder_sent(
        tenant, booking_id, offset_minutes, channel))
