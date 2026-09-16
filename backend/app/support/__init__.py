"""Package: support — tickets, SLA, escalation, knowledge base, macros.

Public interface (other packages/agents import ONLY these):
  - ``SupportService`` — ticket lifecycle, SLA tracking + breach scan,
    escalation evaluation, KB articles, macros.
  - ``DraftAssistant`` (protocol) + ``UnavailableDraftAssistant`` — AI
    draft-reply seam (agents package implements the real one).
  - ``SupportRepository`` / ``InMemorySupportRepository`` — persistence seam.
  - ``can_transition`` — ticket state-machine guard.
  - schemas (``Ticket``, ``SLAPolicy``, ``KBArticle``, ...).

See README.md for the boundary contract.
"""
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
    TicketPriority,
    TicketReply,
    TicketResolve,
    TicketStatus,
    TicketTransition,
)
from .service import (
    ArticleNotFoundError,
    DraftAssistant,
    DraftAssistantUnavailable,
    MacroNotFoundError,
    SupportError,
    SupportService,
    TicketNotFoundError,
    TicketStateError,
    UnavailableDraftAssistant,
    can_transition,
)

__all__ = [
    "ArticleNotFoundError",
    "ArticleStatus",
    "AuthorType",
    "DraftAssistant",
    "DraftAssistantUnavailable",
    "DraftReply",
    "EscalationRule",
    "EscalationRuleCreate",
    "KBArticle",
    "KBArticleCreate",
    "Macro",
    "MacroApply",
    "MacroCreate",
    "MacroNotFoundError",
    "SLAPolicy",
    "SLAPolicyCreate",
    "SLAStatus",
    "SupportError",
    "SupportRepository",
    "SupportService",
    "Ticket",
    "TicketAssign",
    "TicketCreate",
    "TicketMessage",
    "TicketNotFoundError",
    "TicketPriority",
    "TicketReply",
    "TicketResolve",
    "TicketStateError",
    "TicketStatus",
    "TicketTransition",
    "UnavailableDraftAssistant",
    "can_transition",
    "InMemorySupportRepository",
]
