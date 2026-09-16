"""Tests for the support package: ticket lifecycle, assignment, SLA,
escalations, KB search, macros, honest AI-draft stub, cross-tenant isolation."""
from __future__ import annotations

import pytest

from app.core.contracts import DomainEvent, TenantContext
from app.support import schemas as S
from app.support.service import (
    DraftAssistantUnavailable,
    SupportService,
    TicketNotFoundError,
    TicketStateError,
)


class _Bus:
    def __init__(self) -> None:
        self.events: list[DomainEvent] = []

    async def publish(self, event: DomainEvent) -> None:
        self.events.append(event)

    def subscribe(self, topic: str, handler):  # noqa: ANN001, ANN202
        return None


def _svc(bus=None) -> SupportService:  # noqa: ANN001
    return SupportService(events=bus if bus is not None else _Bus())


async def _ticket(svc: SupportService, tenant: TenantContext, **kw) -> S.Ticket:
    data = {"subject": "Help", "priority": S.TicketPriority.HIGH, "body": "broken"}
    data.update(kw)
    return await svc.create_ticket(tenant, S.TicketCreate(**data))


# ----------------------------------------------------------------- lifecycle
async def test_ticket_lifecycle_guarded_transitions(tenant: TenantContext) -> None:
    bus = _Bus()
    svc = _svc(bus)
    t = await _ticket(svc, tenant)
    assert t.status == S.TicketStatus.OPEN

    t = await svc.transition(tenant, t.id, S.TicketTransition(to=S.TicketStatus.PENDING))
    assert t.status == S.TicketStatus.PENDING
    t = await svc.resolve(tenant, t.id, S.TicketResolve(resolution="fixed"))
    assert t.status == S.TicketStatus.RESOLVED
    t = await svc.transition(tenant, t.id, S.TicketTransition(to=S.TicketStatus.CLOSED))
    assert t.status == S.TicketStatus.CLOSED

    # closed is terminal except reopen: closed -> pending is illegal
    with pytest.raises(TicketStateError):
        await svc.transition(tenant, t.id, S.TicketTransition(to=S.TicketStatus.PENDING))
    # reopen is allowed
    t = await svc.transition(tenant, t.id, S.TicketTransition(to=S.TicketStatus.OPEN))
    assert t.status == S.TicketStatus.OPEN
    assert any(e.topic == "support.ticket.created" for e in bus.events)
    assert any(e.topic == "support.ticket.resolved" for e in bus.events)


async def test_invalid_transition_rejected(tenant: TenantContext) -> None:
    svc = _svc()
    t = await _ticket(svc, tenant)
    t = await svc.resolve(tenant, t.id, S.TicketResolve(resolution="fixed"))
    with pytest.raises(TicketStateError):
        # RESOLVED allows only OPEN/CLOSED — jumping back to PENDING is illegal
        await svc.transition(tenant, t.id, S.TicketTransition(to=S.TicketStatus.PENDING))


async def test_conversation_history(tenant: TenantContext) -> None:
    svc = _svc()
    t = await _ticket(svc, tenant)
    await svc.reply(tenant, t.id, S.TicketReply(
        body="looking into it", author_type="agent", author_id="a1"))
    await svc.reply(tenant, t.id, S.TicketReply(
        body="thanks!", author_type="contact", author_id="c1"))
    convo = await svc.conversation(tenant, t.id)
    assert [m.body for m in convo] == ["broken", "looking into it", "thanks!"]


# ---------------------------------------------------------------- assignment
async def test_manual_and_auto_assignment(tenant: TenantContext) -> None:
    svc = _svc()
    t1 = await _ticket(svc, tenant)
    t2 = await _ticket(svc, tenant)
    t1 = await svc.assign(tenant, t1.id, S.TicketAssign(assignee_user_id="u1"))
    assert t1.assignee_user_id == "u1"
    # least-loaded: u1 has 1 ticket, u2 has 0 -> u2 gets t2
    t2 = await svc.auto_assign(tenant, t2.id, candidate_user_ids=["u1", "u2"])
    assert t2.assignee_user_id == "u2"


# ----------------------------------------------------------------------- SLA
async def test_sla_status_and_breach_scan(tenant: TenantContext) -> None:
    from datetime import UTC, datetime, timedelta
    svc = _svc()
    await svc.create_sla_policy(tenant, S.SLAPolicyCreate(
        name="p1", priority=S.TicketPriority.HIGH,
        first_response_minutes=30, resolution_hours=1))
    t = await _ticket(svc, tenant)  # policy auto-attached on create by priority
    status = await svc.sla_status(tenant, t.id)
    assert status.policy_id is not None
    assert not status.breached
    future = datetime.now(UTC) + timedelta(hours=2)
    breached = await svc.scan_breaches(tenant, now=future)
    assert t.id in {b.ticket_id for b in breached}
    assert any(b.resolution_breached for b in breached if b.ticket_id == t.id)


# --------------------------------------------------------------- escalations
async def test_escalation_rule_assigns_queue(tenant: TenantContext) -> None:
    from datetime import UTC, datetime, timedelta
    svc = _svc()
    await svc.create_sla_policy(tenant, S.SLAPolicyCreate(
        name="p1", priority=S.TicketPriority.URGENT,
        first_response_minutes=30, resolution_hours=1))
    await svc.create_escalation_rule(tenant, S.EscalationRuleCreate(
        name="urgent-escalation", priorities=[S.TicketPriority.URGENT],
        breach_type="resolution", min_age_minutes=0,
        action_assign_queue="tier2"))
    t = await _ticket(svc, tenant, priority=S.TicketPriority.URGENT)
    future = datetime.now(UTC) + timedelta(hours=2)
    updated = await svc.evaluate_escalations(tenant, t.id, now=future)
    assert updated.queue == "tier2", "breached urgent ticket should escalate to tier2"


# ------------------------------------------------------------------------ KB
async def test_kb_search_keyword(tenant: TenantContext) -> None:
    svc = _svc()
    art = await svc.create_article(tenant, S.KBArticleCreate(
        title="Reset your password", body="Click forgot password link",
        category="account", tags=["password"]))
    await svc.publish_article(tenant, art.id)
    hits = await svc.search_articles(tenant, "password reset")
    assert hits and hits[0].id == art.id
    # unpublished articles are not searchable
    draft = await svc.create_article(tenant, S.KBArticleCreate(
        title="Internal runbook", body="secret steps"))
    hits = await svc.search_articles(tenant, "runbook")
    assert all(h.id != draft.id for h in hits)


# --------------------------------------------------------------------- macros
async def test_macro_apply_renders_variables(tenant: TenantContext) -> None:
    svc = _svc()
    macro = await svc.create_macro(tenant, S.MacroCreate(
        name="greeting", body="Thanks {{ name }} for reaching out!"))
    rendered = await svc.apply_macro(tenant, macro.id, S.MacroApply(
        variables={"name": "Ada"}))
    assert rendered == "Thanks Ada for reaching out!"
    # missing variables render as empty strings, never crash
    assert await svc.apply_macro(tenant, macro.id, S.MacroApply()) == \
        "Thanks  for reaching out!"


# ------------------------------------------------------------ draft honesty
async def test_draft_reply_unavailable_is_honest(tenant: TenantContext) -> None:
    svc = _svc()
    t = await _ticket(svc, tenant)
    with pytest.raises(DraftAssistantUnavailable):
        await svc.draft_reply(tenant, t.id)


# ------------------------------------------------------- cross-tenant safety
async def test_cross_tenant_invisibility(tenant: TenantContext,
                                         tenant_b: TenantContext) -> None:
    svc = _svc()
    t = await _ticket(svc, tenant)
    assert await svc.list_tickets(tenant_b) == []
    assert await svc.list_macros(tenant_b) == []
    with pytest.raises(TicketNotFoundError):
        await svc.get_ticket(tenant_b, t.id)
    with pytest.raises(TicketNotFoundError):
        await svc.conversation(tenant_b, t.id)
