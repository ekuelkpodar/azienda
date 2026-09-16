"""Router: support. Thin HTTP layer — all business logic lives in SupportService.

Auto-discovery in ``main.py`` mounts this module's ``router`` under ``/api/v1``.
Composition (``BizAppServices``), error mapping, and serialization live in
``.comms`` — the shared router seam for the five business-app routers.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import Response

from app.core.contracts import TenantContext
from app.support import schemas as S

from ._common import TenantDep
from .comms import BizAppDep, BizAppServices, _handle

router = APIRouter(prefix="/support", tags=["support"])


# ------------------------------------------------------------------ tickets
@router.post("/tickets", status_code=201)
async def create_ticket(data: S.TicketCreate, tenant: TenantContext = TenantDep,
                        svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.support.create_ticket(tenant, data), 201)


@router.get("/tickets")
async def list_tickets(status: S.TicketStatus | None = None,
                       assignee_user_id: str | None = None,
                       limit: int = 50, offset: int = 0,
                       tenant: TenantContext = TenantDep,
                       svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.support.list_tickets(
        tenant, status=status, assignee_user_id=assignee_user_id,
        limit=limit, offset=offset))


@router.get("/tickets/{ticket_id}")
async def get_ticket(ticket_id: str, tenant: TenantContext = TenantDep,
                     svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.support.get_ticket(tenant, ticket_id))


@router.post("/tickets/{ticket_id}/reply")
async def reply(ticket_id: str, data: S.TicketReply,
                tenant: TenantContext = TenantDep,
                svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.support.reply(tenant, ticket_id, data))


@router.get("/tickets/{ticket_id}/conversation")
async def conversation(ticket_id: str, tenant: TenantContext = TenantDep,
                       svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.support.conversation(tenant, ticket_id))


@router.post("/tickets/{ticket_id}/transition")
async def transition(ticket_id: str, data: S.TicketTransition,
                     tenant: TenantContext = TenantDep,
                     svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.support.transition(tenant, ticket_id, data))


@router.post("/tickets/{ticket_id}/assign")
async def assign(ticket_id: str, data: S.TicketAssign,
                 tenant: TenantContext = TenantDep,
                 svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.support.assign(tenant, ticket_id, data))


@router.post("/tickets/{ticket_id}/auto-assign")
async def auto_assign(ticket_id: str, body: dict[str, Any],
                      tenant: TenantContext = TenantDep,
                      svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.support.auto_assign(
        tenant, ticket_id, body.get("candidate_user_ids", [])))


@router.post("/tickets/{ticket_id}/resolve")
async def resolve(ticket_id: str, data: S.TicketResolve,
                  tenant: TenantContext = TenantDep,
                  svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.support.resolve(tenant, ticket_id, data))


@router.post("/tickets/{ticket_id}/escalate")
async def escalate(ticket_id: str, tenant: TenantContext = TenantDep,
                   svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.support.evaluate_escalations(tenant, ticket_id))


@router.get("/tickets/{ticket_id}/sla")
async def sla_status(ticket_id: str, tenant: TenantContext = TenantDep,
                     svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.support.sla_status(tenant, ticket_id))


@router.post("/tickets/{ticket_id}/draft-reply")
async def draft_reply(ticket_id: str, tenant: TenantContext = TenantDep,
                      svc: BizAppServices = BizAppDep) -> Response:
    # Honest 503 when no draft assistant is wired (never fakes a draft).
    return await _handle(svc.support.draft_reply(tenant, ticket_id))


@router.post("/sla/scan-breaches")
async def scan_breaches(tenant: TenantContext = TenantDep,
                        svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.support.scan_breaches(tenant))


@router.post("/sla/policies", status_code=201)
async def create_sla_policy(data: S.SLAPolicyCreate,
                            tenant: TenantContext = TenantDep,
                            svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.support.create_sla_policy(tenant, data), 201)


@router.get("/sla/policies")
async def list_sla_policies(tenant: TenantContext = TenantDep,
                            svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.support.list_sla_policies(tenant))


@router.post("/escalation-rules", status_code=201)
async def create_escalation_rule(data: S.EscalationRuleCreate,
                                 tenant: TenantContext = TenantDep,
                                 svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.support.create_escalation_rule(tenant, data), 201)


@router.get("/escalation-rules")
async def list_escalation_rules(tenant: TenantContext = TenantDep,
                                svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.support.list_escalation_rules(tenant))


# ------------------------------------------------------------------ knowledge base
@router.post("/kb/articles", status_code=201)
async def create_article(data: S.KBArticleCreate, tenant: TenantContext = TenantDep,
                         svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.support.create_article(tenant, data), 201)


@router.get("/kb/articles")
async def list_articles(status: S.ArticleStatus | None = None,
                        category: str | None = None,
                        tenant: TenantContext = TenantDep,
                        svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.support.list_articles(
        tenant, status=status, category=category))


@router.get("/kb/articles/search")
async def search_articles(q: str, limit: int = 10,
                          tenant: TenantContext = TenantDep,
                          svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.support.search_articles(tenant, q, limit=limit))


@router.get("/kb/articles/{article_id}")
async def get_article(article_id: str, tenant: TenantContext = TenantDep,
                      svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.support.get_article(tenant, article_id))


@router.post("/kb/articles/{article_id}/publish")
async def publish_article(article_id: str, tenant: TenantContext = TenantDep,
                          svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.support.publish_article(tenant, article_id))


# ------------------------------------------------------------------ macros
@router.post("/macros", status_code=201)
async def create_macro(data: S.MacroCreate, tenant: TenantContext = TenantDep,
                       svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.support.create_macro(tenant, data), 201)


@router.get("/macros")
async def list_macros(tenant: TenantContext = TenantDep,
                      svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.support.list_macros(tenant))


@router.post("/macros/{macro_id}/apply")
async def apply_macro(macro_id: str, data: S.MacroApply,
                      tenant: TenantContext = TenantDep,
                      svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.support.apply_macro(tenant, macro_id, data))
