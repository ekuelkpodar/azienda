"""SupportService — ticket lifecycle, SLA tracking, escalation, KB, macros.

Ticket state machine (binding):
    open -> pending -> resolved -> closed
    open -> resolved | closed        (fast-path)
    pending -> open                  (customer reopens / agent pulls back)
    resolved -> open                 (reopen)
    closed -> open                   (reopen; audited)
All other transitions are illegal (409).

SLA: each ticket gets the active policy matching its priority. Breach
detection is explicit (``scan_breaches``) so a scheduler/ARQ worker can run it
on a cadence; newly breached tickets emit ``support.sla.breached`` once
(tracked by flags on the ticket — no duplicate events).

Escalation rules are evaluated against live ticket state and apply assignment
/ priority actions. AI draft replies come through the ``DraftAssistant``
protocol — the default ``UnavailableDraftAssistant`` raises honestly instead
of faking a draft; the agents package provides the real implementation.
"""
from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, runtime_checkable

from app.core.contracts import DomainEvent, EventBus, TenantContext

from .repository import InMemorySupportRepository, SupportRepository
from .schemas import (
    ArticleStatus,
    AuthorType,
    DraftReply,
    EscalationRule,
    EscalationRuleCreate,
    KBArticle,
    KBArticleCreate,
    Macro,
    MacroApply,
    MacroCreate,
    SLAPolicy,
    SLAPolicyCreate,
    SLAStatus,
    Ticket,
    TicketAssign,
    TicketCreate,
    TicketMessage,
    TicketReply,
    TicketResolve,
    TicketStatus,
    TicketTransition,
)


# ------------------------------------------------------------------ errors
class SupportError(Exception):
    """Base for support domain errors."""


class TicketNotFoundError(SupportError):
    pass


class TicketStateError(SupportError):
    """Illegal lifecycle transition (409)."""


class ArticleNotFoundError(SupportError):
    pass


class MacroNotFoundError(SupportError):
    pass


class DraftAssistantUnavailable(SupportError):
    """AI drafting is not configured. Honest stub — see README."""


# ------------------------------------------------------------------ draft protocol
@runtime_checkable
class DraftAssistant(Protocol):
    """AI draft-reply seam. Implemented by the agents package (future)."""

    async def draft(self, tenant: TenantContext, ticket: Ticket,
                    history: list[TicketMessage]) -> DraftReply: ...


class UnavailableDraftAssistant:
    """Default: AI drafting is not wired. Raises instead of faking a draft."""

    async def draft(self, tenant: TenantContext, ticket: Ticket,
                    history: list[TicketMessage]) -> DraftReply:
        raise DraftAssistantUnavailable(
            "AI draft replies are not configured: no DraftAssistant implementation "
            "is injected (agents package integration is future work)")


# ------------------------------------------------------------------ state machine
_TRANSITIONS: dict[TicketStatus, set[TicketStatus]] = {
    TicketStatus.OPEN: {TicketStatus.PENDING, TicketStatus.RESOLVED, TicketStatus.CLOSED},
    TicketStatus.PENDING: {TicketStatus.OPEN, TicketStatus.RESOLVED, TicketStatus.CLOSED},
    TicketStatus.RESOLVED: {TicketStatus.OPEN, TicketStatus.CLOSED},
    TicketStatus.CLOSED: {TicketStatus.OPEN},  # reopen only
}


def can_transition(from_status: TicketStatus, to_status: TicketStatus) -> bool:
    return to_status in _TRANSITIONS.get(from_status, set())


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid.uuid4().hex


class SupportService:
    def __init__(
        self,
        repo: SupportRepository | None = None,
        events: EventBus | None = None,
        draft_assistant: DraftAssistant | None = None,
    ) -> None:
        self._repo = repo or InMemorySupportRepository()
        self._events = events
        self._drafts = draft_assistant or UnavailableDraftAssistant()

    # ---------------------------------------------------------------- tickets
    async def create_ticket(self, tenant: TenantContext, data: TicketCreate) -> Ticket:
        policies = await self._repo.list_sla_policies(tenant.tenant_id)
        policy = next((p for p in policies
                       if p.priority == data.priority and p.is_active), None)
        now = _utcnow()
        ticket = Ticket(
            id=_new_id(), tenant_id=tenant.tenant_id, subject=data.subject,
            status=TicketStatus.OPEN, priority=data.priority,
            requester_contact_id=data.requester_contact_id,
            channel=data.channel, queue=data.queue,
            sla_policy_id=policy.id if policy else None,
            opened_at=now, tags=list(data.tags),
        )
        ticket = await self._repo.add_ticket(ticket)
        if data.body:
            await self._add_message(
                tenant, ticket, AuthorType.CONTACT, data.requester_contact_id,
                data.body, channel=data.channel, is_internal_note=False)
        await self._emit(tenant, "support.ticket.created", ticket.id, {
            "ticket_id": ticket.id, "priority": ticket.priority.value,
            "channel": ticket.channel})
        return ticket

    async def get_ticket(self, tenant: TenantContext, ticket_id: str) -> Ticket:
        ticket = await self._repo.get_ticket(tenant.tenant_id, ticket_id)
        if ticket is None:
            raise TicketNotFoundError(ticket_id)
        return ticket

    async def list_tickets(self, tenant: TenantContext, status: TicketStatus | None = None,
                           assignee_user_id: str | None = None,
                           limit: int = 50, offset: int = 0) -> list[Ticket]:
        return await self._repo.list_tickets(
            tenant.tenant_id, status.value if status else None,
            assignee_user_id, limit, offset)

    async def reply(
        self, tenant: TenantContext, ticket_id: str, data: TicketReply
    ) -> TicketMessage:
        ticket = await self.get_ticket(tenant, ticket_id)
        if ticket.status == TicketStatus.CLOSED:
            raise TicketStateError("cannot reply to a closed ticket (reopen it first)")
        message = await self._add_message(
            tenant, ticket, data.author_type, data.author_id, data.body,
            channel=data.channel, is_internal_note=data.is_internal_note)
        patch: dict[str, Any] = {}
        # First human/agent response stops the first-response SLA clock.
        if (ticket.first_response_at is None and data.author_type in
                (AuthorType.USER, AuthorType.AGENT) and not data.is_internal_note):
            patch["first_response_at"] = message.created_at
        if ticket.status == TicketStatus.OPEN and not data.is_internal_note:
            patch["status"] = TicketStatus.PENDING
        if patch:
            await self._repo.update_ticket(ticket.model_copy(update=patch))
        await self._emit(tenant, "support.ticket.replied", ticket.id, {
            "ticket_id": ticket.id, "message_id": message.id,
            "author_type": data.author_type.value})
        return message

    async def _add_message(self, tenant: TenantContext, ticket: Ticket,
                           author_type: AuthorType, author_id: str | None,
                           body: str, channel: str | None,
                           is_internal_note: bool) -> TicketMessage:
        message = TicketMessage(
            id=_new_id(), tenant_id=tenant.tenant_id, ticket_id=ticket.id,
            author_type=author_type, author_id=author_id, channel=channel,
            body=body, is_internal_note=is_internal_note, created_at=_utcnow())
        return await self._repo.add_ticket_message(message)

    async def conversation(self, tenant: TenantContext, ticket_id: str) -> list[TicketMessage]:
        """Full conversation history, threaded across channels (ordered by time)."""
        await self.get_ticket(tenant, ticket_id)
        return await self._repo.list_ticket_messages(tenant.tenant_id, ticket_id)

    async def transition(
        self, tenant: TenantContext, ticket_id: str, data: TicketTransition
    ) -> Ticket:
        ticket = await self.get_ticket(tenant, ticket_id)
        if not can_transition(ticket.status, data.to):
            raise TicketStateError(
                f"illegal transition {ticket.status.value} -> {data.to.value}")
        patch: dict[str, Any] = {"status": data.to}
        if data.to == TicketStatus.RESOLVED and ticket.resolved_at is None:
            patch["resolved_at"] = _utcnow()
        if data.to == TicketStatus.CLOSED:
            patch["closed_at"] = _utcnow()
        if data.to == TicketStatus.OPEN:  # reopen clears resolution markers
            patch.update({"resolved_at": None, "closed_at": None, "resolution": None})
        ticket = await self._repo.update_ticket(ticket.model_copy(update=patch))
        await self._emit(tenant, "support.ticket.transitioned", ticket.id, {
            "ticket_id": ticket.id, "to": data.to.value, "note": data.note})
        return ticket

    async def assign(self, tenant: TenantContext, ticket_id: str, data: TicketAssign) -> Ticket:
        ticket = await self.get_ticket(tenant, ticket_id)
        patch: dict[str, Any] = {}
        if data.assignee_user_id is not None:
            patch["assignee_user_id"] = data.assignee_user_id
            patch["assignee_agent_id"] = None
        if data.assignee_agent_id is not None:
            patch["assignee_agent_id"] = data.assignee_agent_id
            patch["assignee_user_id"] = None
        if data.queue is not None:
            patch["queue"] = data.queue
        ticket = await self._repo.update_ticket(ticket.model_copy(update=patch))
        await self._emit(tenant, "support.ticket.assigned", ticket.id, {
            "ticket_id": ticket.id, "assignee_user_id": ticket.assignee_user_id,
            "assignee_agent_id": ticket.assignee_agent_id, "queue": ticket.queue})
        return ticket

    async def auto_assign(
        self, tenant: TenantContext, ticket_id: str, candidate_user_ids: list[str]
    ) -> Ticket:
        """Assign to the candidate with the fewest open/pending tickets (least-loaded)."""
        if not candidate_user_ids:
            raise SupportError("auto_assign needs at least one candidate")
        loads: dict[str, int] = {}
        for user_id in candidate_user_ids:
            open_tickets = await self._repo.list_tickets(
                tenant.tenant_id, assignee_user_id=user_id, limit=10_000)
            loads[user_id] = sum(1 for t in open_tickets
                                 if t.status in (TicketStatus.OPEN, TicketStatus.PENDING))
        chosen = min(candidate_user_ids, key=lambda u: (loads[u], u))
        return await self.assign(tenant, ticket_id, TicketAssign(assignee_user_id=chosen))

    async def resolve(self, tenant: TenantContext, ticket_id: str,
                      data: TicketResolve) -> Ticket:
        ticket = await self.get_ticket(tenant, ticket_id)
        if not can_transition(ticket.status, TicketStatus.RESOLVED):
            raise TicketStateError(
                f"cannot resolve ticket in status {ticket.status.value}")
        ticket = await self._repo.update_ticket(ticket.model_copy(update={
            "status": TicketStatus.RESOLVED, "resolution": data.resolution,
            "resolved_at": _utcnow()}))
        await self._emit(tenant, "support.ticket.resolved", ticket.id, {
            "ticket_id": ticket.id})
        return ticket

    # ---------------------------------------------------------------- SLA
    async def create_sla_policy(
        self, tenant: TenantContext, data: SLAPolicyCreate
    ) -> SLAPolicy:
        policy = SLAPolicy(
            id=_new_id(), tenant_id=tenant.tenant_id, name=data.name,
            priority=data.priority, first_response_minutes=data.first_response_minutes,
            resolution_hours=data.resolution_hours)
        return await self._repo.add_sla_policy(policy)

    async def list_sla_policies(self, tenant: TenantContext) -> list[SLAPolicy]:
        return await self._repo.list_sla_policies(tenant.tenant_id)

    async def sla_status(
        self, tenant: TenantContext, ticket_id: str, now: datetime | None = None
    ) -> SLAStatus:
        ticket = await self.get_ticket(tenant, ticket_id)
        return self._compute_sla(ticket, await self._policy_for(tenant, ticket),
                                 now or _utcnow())

    async def _policy_for(self, tenant: TenantContext, ticket: Ticket) -> SLAPolicy | None:
        if ticket.sla_policy_id:
            policies = await self._repo.list_sla_policies(tenant.tenant_id)
            return next((p for p in policies if p.id == ticket.sla_policy_id), None)
        return None

    @staticmethod
    def _compute_sla(ticket: Ticket, policy: SLAPolicy | None, now: datetime) -> SLAStatus:
        if policy is None:
            return SLAStatus(ticket_id=ticket.id, policy_id=None,
                             first_response_due_at=None, resolution_due_at=None,
                             first_response_breached=False, resolution_breached=False,
                             breached=False)
        fr_due = ticket.opened_at + timedelta(minutes=policy.first_response_minutes)
        res_due = ticket.opened_at + timedelta(hours=policy.resolution_hours)
        fr_breach = (ticket.first_response_at is None and now > fr_due
                     and ticket.status in (TicketStatus.OPEN, TicketStatus.PENDING))
        res_breach = (ticket.status in (TicketStatus.OPEN, TicketStatus.PENDING)
                      and now > res_due)
        return SLAStatus(
            ticket_id=ticket.id, policy_id=policy.id,
            first_response_due_at=fr_due, resolution_due_at=res_due,
            first_response_breached=fr_breach or ticket.first_response_breached,
            resolution_breached=res_breach or ticket.resolution_breached,
            breached=(fr_breach or res_breach or ticket.first_response_breached
                      or ticket.resolution_breached))

    async def scan_breaches(
        self, tenant: TenantContext, now: datetime | None = None
    ) -> list[SLAStatus]:
        """Detect newly breached tickets; each breach emits exactly one event."""
        now = now or _utcnow()
        breached: list[SLAStatus] = []
        for ticket in await self._repo.list_tickets(tenant.tenant_id, limit=10_000):
            if ticket.status not in (TicketStatus.OPEN, TicketStatus.PENDING):
                continue
            status = self._compute_sla(ticket, await self._policy_for(tenant, ticket), now)
            newly = ((status.first_response_breached and not ticket.first_response_breached)
                     or (status.resolution_breached and not ticket.resolution_breached))
            if newly:
                ticket = await self._repo.update_ticket(ticket.model_copy(update={
                    "first_response_breached": status.first_response_breached,
                    "resolution_breached": status.resolution_breached}))
                status = self._compute_sla(
                    ticket, await self._policy_for(tenant, ticket), now)
                breached.append(status)
                await self._emit(tenant, "support.sla.breached", ticket.id, {
                    "ticket_id": ticket.id, "policy_id": status.policy_id,
                    "first_response_breached": status.first_response_breached,
                    "resolution_breached": status.resolution_breached})
        return breached

    # ---------------------------------------------------------------- escalation
    async def create_escalation_rule(
        self, tenant: TenantContext, data: EscalationRuleCreate
    ) -> EscalationRule:
        rule = EscalationRule(
            id=_new_id(), tenant_id=tenant.tenant_id, name=data.name,
            priorities=list(data.priorities), breach_type=data.breach_type,
            min_age_minutes=data.min_age_minutes,
            action_assign_user_id=data.action_assign_user_id,
            action_assign_queue=data.action_assign_queue,
            action_set_priority=data.action_set_priority,
            created_at=_utcnow())
        return await self._repo.add_escalation_rule(rule)

    async def list_escalation_rules(self, tenant: TenantContext) -> list[EscalationRule]:
        return await self._repo.list_escalation_rules(tenant.tenant_id)

    async def evaluate_escalations(
        self, tenant: TenantContext, ticket_id: str, now: datetime | None = None
    ) -> Ticket:
        """Apply the first matching active escalation rule to the ticket."""
        now = now or _utcnow()
        ticket = await self.get_ticket(tenant, ticket_id)
        sla = self._compute_sla(ticket, await self._policy_for(tenant, ticket), now)
        age_minutes = (now - ticket.opened_at).total_seconds() / 60
        for rule in await self._repo.list_escalation_rules(tenant.tenant_id):
            if not rule.is_active:
                continue
            if rule.priorities and ticket.priority not in rule.priorities:
                continue
            if rule.breach_type == "first_response" and not sla.first_response_breached:
                continue
            if rule.breach_type == "resolution" and not sla.resolution_breached:
                continue
            if rule.breach_type == "any" and not sla.breached:
                continue
            if age_minutes < rule.min_age_minutes:
                continue
            patch: dict[str, Any] = {}
            if rule.action_assign_user_id:
                patch["assignee_user_id"] = rule.action_assign_user_id
                patch["assignee_agent_id"] = None
            if rule.action_assign_queue:
                patch["queue"] = rule.action_assign_queue
            if rule.action_set_priority:
                patch["priority"] = rule.action_set_priority
            if not patch:
                continue
            ticket = await self._repo.update_ticket(ticket.model_copy(update=patch))
            await self._emit(tenant, "support.ticket.escalated", ticket.id, {
                "ticket_id": ticket.id, "rule_id": rule.id, "rule_name": rule.name,
                "applied": patch})
            break  # first matching rule wins
        return ticket

    # ---------------------------------------------------------------- knowledge base
    async def create_article(
        self, tenant: TenantContext, data: KBArticleCreate
    ) -> KBArticle:
        now = _utcnow()
        article = KBArticle(
            id=_new_id(), tenant_id=tenant.tenant_id, title=data.title,
            body=data.body, category=data.category, tags=list(data.tags),
            is_faq=data.is_faq, status=ArticleStatus.DRAFT,
            created_by=tenant.user_id, created_at=now, updated_at=now)
        return await self._repo.add_article(article)

    async def publish_article(self, tenant: TenantContext, article_id: str) -> KBArticle:
        article = await self._repo.get_article(tenant.tenant_id, article_id)
        if article is None:
            raise ArticleNotFoundError(article_id)
        return await self._repo.update_article(article.model_copy(update={
            "status": ArticleStatus.PUBLISHED, "updated_at": _utcnow()}))

    async def get_article(self, tenant: TenantContext, article_id: str) -> KBArticle:
        article = await self._repo.get_article(tenant.tenant_id, article_id)
        if article is None:
            raise ArticleNotFoundError(article_id)
        return article

    async def list_articles(self, tenant: TenantContext,
                            status: ArticleStatus | None = None,
                            category: str | None = None) -> list[KBArticle]:
        return await self._repo.list_articles(
            tenant.tenant_id, status.value if status else None, category)

    async def search_articles(self, tenant: TenantContext, query: str,
                              limit: int = 10) -> list[KBArticle]:
        """Keyword search over published articles.

        MVP uses substring scoring; pgvector semantic search over ingested
        chunks is the documented step-up (knowledge package, ADR-002).
        """
        terms = [t.lower() for t in re.findall(r"\w+", query)]
        if not terms:
            return []
        scored: list[tuple[int, KBArticle]] = []
        for article in await self._repo.list_articles(
                tenant.tenant_id, ArticleStatus.PUBLISHED.value):
            haystack = f"{article.title} {article.body} {' '.join(article.tags)}".lower()
            score = sum(haystack.count(t) for t in terms)
            if score:
                scored.append((score, article))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [a for _, a in scored[:limit]]

    # ---------------------------------------------------------------- macros
    async def create_macro(self, tenant: TenantContext, data: MacroCreate) -> Macro:
        macro = Macro(
            id=_new_id(), tenant_id=tenant.tenant_id, name=data.name,
            body=data.body, shared=data.shared, created_by=tenant.user_id,
            created_at=_utcnow())
        return await self._repo.add_macro(macro)

    async def list_macros(self, tenant: TenantContext) -> list[Macro]:
        return await self._repo.list_macros(tenant.tenant_id)

    async def apply_macro(
        self, tenant: TenantContext, macro_id: str, data: MacroApply
    ) -> str:
        """Render a macro body with ``{{variable}}`` substitution.

        Deliberately simple (no Jinja): macros are agent-authored snippets, not
        code. Missing variables render as empty strings.
        """
        macro = await self._repo.get_macro(tenant.tenant_id, macro_id)
        if macro is None:
            raise MacroNotFoundError(macro_id)

        def _replace(match: re.Match[str]) -> str:
            return str(data.variables.get(match.group(1).strip(), ""))

        return re.sub(r"\{\{\s*(\w+)\s*\}\}", _replace, macro.body)

    # ---------------------------------------------------------------- AI draft
    async def draft_reply(self, tenant: TenantContext, ticket_id: str) -> DraftReply:
        """Generate an AI draft reply via the injected DraftAssistant protocol.

        Default implementation raises ``DraftAssistantUnavailable`` — an honest
        signal, not a fake draft. The agents package provides the real one.
        """
        ticket = await self.get_ticket(tenant, ticket_id)
        history = await self._repo.list_ticket_messages(tenant.tenant_id, ticket_id)
        return await self._drafts.draft(tenant, ticket, history)

    # ---------------------------------------------------------------- events
    async def _emit(self, tenant: TenantContext, topic: str, aggregate_id: str,
                    payload: dict[str, Any]) -> None:
        if self._events is None:
            return
        await self._events.publish(DomainEvent(
            topic=topic, tenant_id=tenant.tenant_id, aggregate_id=aggregate_id,
            payload=payload, event_id=_new_id(), occurred_at=_utcnow()))
