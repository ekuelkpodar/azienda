"""Repository protocols + in-memory implementations for support.

Persistence seam: ``SupportRepository``. ``InMemorySupportRepository`` backs
tests and local dev; Postgres SQLAlchemy implementation (``models.py``) is
future work pending ``core/db``.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from .schemas import (
    EscalationRule,
    KBArticle,
    Macro,
    SLAPolicy,
    Ticket,
    TicketMessage,
)


@runtime_checkable
class SupportRepository(Protocol):
    # tickets
    async def add_ticket(self, ticket: Ticket) -> Ticket: ...
    async def get_ticket(self, tenant_id: str, ticket_id: str) -> Ticket | None: ...
    async def update_ticket(self, ticket: Ticket) -> Ticket: ...
    async def list_tickets(self, tenant_id: str, status: str | None = None,
                           assignee_user_id: str | None = None,
                           limit: int = 50, offset: int = 0) -> list[Ticket]: ...
    # messages
    async def add_ticket_message(self, message: TicketMessage) -> TicketMessage: ...
    async def list_ticket_messages(self, tenant_id: str, ticket_id: str) -> list[TicketMessage]: ...
    # sla policies
    async def add_sla_policy(self, policy: SLAPolicy) -> SLAPolicy: ...
    async def list_sla_policies(self, tenant_id: str) -> list[SLAPolicy]: ...
    # escalation rules
    async def add_escalation_rule(self, rule: EscalationRule) -> EscalationRule: ...
    async def list_escalation_rules(self, tenant_id: str) -> list[EscalationRule]: ...
    # kb
    async def add_article(self, article: KBArticle) -> KBArticle: ...
    async def get_article(self, tenant_id: str, article_id: str) -> KBArticle | None: ...
    async def update_article(self, article: KBArticle) -> KBArticle: ...
    async def list_articles(self, tenant_id: str, status: str | None = None,
                            category: str | None = None) -> list[KBArticle]: ...
    # macros
    async def add_macro(self, macro: Macro) -> Macro: ...
    async def get_macro(self, tenant_id: str, macro_id: str) -> Macro | None: ...
    async def list_macros(self, tenant_id: str) -> list[Macro]: ...


class InMemorySupportRepository:
    """Process-local repository. Tenant isolation enforced on every access."""

    def __init__(self) -> None:
        self._tickets: dict[tuple[str, str], Ticket] = {}
        self._messages: dict[tuple[str, str], TicketMessage] = {}
        self._sla: dict[tuple[str, str], SLAPolicy] = {}
        self._rules: dict[tuple[str, str], EscalationRule] = {}
        self._articles: dict[tuple[str, str], KBArticle] = {}
        self._macros: dict[tuple[str, str], Macro] = {}

    async def add_ticket(self, ticket: Ticket) -> Ticket:
        self._tickets[(ticket.tenant_id, ticket.id)] = ticket
        return ticket

    async def get_ticket(self, tenant_id: str, ticket_id: str) -> Ticket | None:
        return self._tickets.get((tenant_id, ticket_id))

    async def update_ticket(self, ticket: Ticket) -> Ticket:
        self._tickets[(ticket.tenant_id, ticket.id)] = ticket
        return ticket

    async def list_tickets(self, tenant_id: str, status: str | None = None,
                           assignee_user_id: str | None = None,
                           limit: int = 50, offset: int = 0) -> list[Ticket]:
        items = [
            t for (ten, _), t in self._tickets.items()
            if ten == tenant_id
            and (status is None or t.status.value == status)
            and (assignee_user_id is None or t.assignee_user_id == assignee_user_id)
        ]
        items.sort(key=lambda t: t.opened_at)
        return items[offset:offset + limit]

    async def add_ticket_message(self, message: TicketMessage) -> TicketMessage:
        self._messages[(message.tenant_id, message.id)] = message
        return message

    async def list_ticket_messages(
        self, tenant_id: str, ticket_id: str
    ) -> list[TicketMessage]:
        items = [m for (t, _), m in self._messages.items()
                 if t == tenant_id and m.ticket_id == ticket_id]
        items.sort(key=lambda m: m.created_at)
        return items

    async def add_sla_policy(self, policy: SLAPolicy) -> SLAPolicy:
        self._sla[(policy.tenant_id, policy.id)] = policy
        return policy

    async def list_sla_policies(self, tenant_id: str) -> list[SLAPolicy]:
        return [p for (t, _), p in self._sla.items() if t == tenant_id]

    async def add_escalation_rule(self, rule: EscalationRule) -> EscalationRule:
        self._rules[(rule.tenant_id, rule.id)] = rule
        return rule

    async def list_escalation_rules(self, tenant_id: str) -> list[EscalationRule]:
        return [r for (t, _), r in self._rules.items() if t == tenant_id]

    async def add_article(self, article: KBArticle) -> KBArticle:
        self._articles[(article.tenant_id, article.id)] = article
        return article

    async def get_article(self, tenant_id: str, article_id: str) -> KBArticle | None:
        return self._articles.get((tenant_id, article_id))

    async def update_article(self, article: KBArticle) -> KBArticle:
        self._articles[(article.tenant_id, article.id)] = article
        return article

    async def list_articles(self, tenant_id: str, status: str | None = None,
                            category: str | None = None) -> list[KBArticle]:
        return [
            a for (t, _), a in self._articles.items()
            if t == tenant_id
            and (status is None or a.status.value == status)
            and (category is None or a.category == category)
        ]

    async def add_macro(self, macro: Macro) -> Macro:
        self._macros[(macro.tenant_id, macro.id)] = macro
        return macro

    async def get_macro(self, tenant_id: str, macro_id: str) -> Macro | None:
        return self._macros.get((tenant_id, macro_id))

    async def list_macros(self, tenant_id: str) -> list[Macro]:
        return [m for (t, _), m in self._macros.items() if t == tenant_id]
