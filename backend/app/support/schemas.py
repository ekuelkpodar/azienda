# ruff: noqa: UP042 -- keep (str, Enum) for consistency across all bizapp schemas
"""Pydantic schemas for the support package.

Tickets, conversation messages (threaded across channels), SLA policies,
escalation rules, knowledge-base articles/FAQs, macros, AI draft replies.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class TicketStatus(str, Enum):
    OPEN = "open"
    PENDING = "pending"        # waiting on customer / third party
    RESOLVED = "resolved"
    CLOSED = "closed"


class TicketPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class AuthorType(str, Enum):
    CONTACT = "contact"        # the customer
    USER = "user"              # human agent
    AGENT = "agent"            # AI agent workforce member
    SYSTEM = "system"


class ArticleStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


# ------------------------------------------------------------------ tickets
class Ticket(BaseModel):
    id: str
    tenant_id: str
    subject: str
    status: TicketStatus
    priority: TicketPriority
    requester_contact_id: str | None = None
    assignee_user_id: str | None = None
    assignee_agent_id: str | None = None
    queue: str | None = None
    channel: str | None = None            # originating channel: email/chat/sms/...
    conversation_id: str | None = None    # comms conversation link
    sla_policy_id: str | None = None
    first_response_at: datetime | None = None
    first_response_breached: bool = False
    resolution_breached: bool = False
    opened_at: datetime
    resolved_at: datetime | None = None
    closed_at: datetime | None = None
    resolution: str | None = None
    tags: list[str] = Field(default_factory=list)


class TicketCreate(BaseModel):
    subject: str = Field(min_length=1, max_length=300)
    priority: TicketPriority = TicketPriority.NORMAL
    requester_contact_id: str | None = None
    channel: str | None = Field(default=None, max_length=32)
    queue: str | None = Field(default=None, max_length=100)
    tags: list[str] = Field(default_factory=list)
    body: str | None = Field(default=None, max_length=20000)  # opening message


class TicketMessage(BaseModel):
    id: str
    tenant_id: str
    ticket_id: str
    author_type: AuthorType
    author_id: str | None = None
    channel: str | None = None            # channel this message arrived on
    body: str
    is_internal_note: bool = False
    created_at: datetime


class TicketReply(BaseModel):
    body: str = Field(min_length=1, max_length=20000)
    author_type: AuthorType = AuthorType.USER
    author_id: str | None = None
    channel: str | None = Field(default=None, max_length=32)
    is_internal_note: bool = False


class TicketTransition(BaseModel):
    to: TicketStatus
    note: str | None = Field(default=None, max_length=1000)


class TicketAssign(BaseModel):
    assignee_user_id: str | None = None
    assignee_agent_id: str | None = None
    queue: str | None = None


class TicketResolve(BaseModel):
    resolution: str = Field(min_length=1, max_length=5000)


# ------------------------------------------------------------------ SLA
class SLAPolicy(BaseModel):
    id: str
    tenant_id: str
    name: str
    priority: TicketPriority
    first_response_minutes: int = Field(gt=0)
    resolution_hours: int = Field(gt=0)
    is_active: bool = True


class SLAPolicyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    priority: TicketPriority
    first_response_minutes: int = Field(gt=0)
    resolution_hours: int = Field(gt=0)


class SLAStatus(BaseModel):
    ticket_id: str
    policy_id: str | None
    first_response_due_at: datetime | None
    resolution_due_at: datetime | None
    first_response_breached: bool
    resolution_breached: bool
    breached: bool


# ------------------------------------------------------------------ escalation
class EscalationRule(BaseModel):
    id: str
    tenant_id: str
    name: str
    priorities: list[TicketPriority] = Field(default_factory=list)  # empty = any
    breach_type: str = "any"             # "any" | "first_response" | "resolution"
    min_age_minutes: int = Field(default=0, ge=0)
    action_assign_user_id: str | None = None
    action_assign_queue: str | None = None
    action_set_priority: TicketPriority | None = None
    is_active: bool = True
    created_at: datetime


class EscalationRuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    priorities: list[TicketPriority] = Field(default_factory=list)
    breach_type: str = Field(default="any", pattern="^(any|first_response|resolution)$")
    min_age_minutes: int = Field(default=0, ge=0)
    action_assign_user_id: str | None = None
    action_assign_queue: str | None = None
    action_set_priority: TicketPriority | None = None


# ------------------------------------------------------------------ knowledge base
class KBArticle(BaseModel):
    id: str
    tenant_id: str
    title: str
    body: str
    category: str | None = None
    tags: list[str] = Field(default_factory=list)
    is_faq: bool = False
    status: ArticleStatus
    view_count: int = 0
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime


class KBArticleCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1)
    category: str | None = Field(default=None, max_length=100)
    tags: list[str] = Field(default_factory=list)
    is_faq: bool = False


# ------------------------------------------------------------------ macros + drafts
class Macro(BaseModel):
    id: str
    tenant_id: str
    name: str
    body: str                            # Jinja-lite: {{variable}} substitution
    shared: bool = True
    created_by: str | None = None
    created_at: datetime


class MacroCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1)
    shared: bool = True


class MacroApply(BaseModel):
    variables: dict[str, Any] = Field(default_factory=dict)


class DraftReply(BaseModel):
    ticket_id: str
    draft: str
    model: str | None = None
    note: str | None = None
