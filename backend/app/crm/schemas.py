"""CRM HTTP schemas (Pydantic v2). No business logic."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


# ------------------------------------------------------------------ shared
class Page(BaseModel):
    items: list[Any]
    next_page_token: str | None = None


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = {}
    trace_id: str


class ErrorEnvelope(BaseModel):
    error: ErrorDetail


OBJECT_TYPES = ("organization", "contact", "lead", "opportunity")
CUSTOM_FIELD_TYPES = ("string", "number", "boolean", "date", "enum")
SIZE_BANDS = ("startup", "smb", "mid", "enterprise")
LEAD_STATUSES = ("new", "contacted", "qualified", "unqualified", "converted", "nurturing")


def _as_str_id(v: Any) -> str:
    return str(v)


# ------------------------------------------------------------------ organizations
class OrganizationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    domain: str | None = Field(default=None, max_length=255)
    industry: str | None = Field(default=None, max_length=128)
    size_band: Literal["startup", "smb", "mid", "enterprise"] | None = None
    lifecycle_stage: str = Field(default="prospect", max_length=64)
    owner_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    custom: dict[str, Any] = Field(default_factory=dict)


class OrganizationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    domain: str | None = Field(default=None, max_length=255)
    industry: str | None = Field(default=None, max_length=128)
    size_band: Literal["startup", "smb", "mid", "enterprise"] | None = None
    lifecycle_stage: str | None = Field(default=None, max_length=64)
    owner_id: str | None = None
    tags: list[str] | None = None
    custom: dict[str, Any] | None = None


class OrganizationRead(BaseModel):
    id: UUID
    tenant_id: UUID
    name: str
    domain: str | None
    industry: str | None
    size_band: str | None
    lifecycle_stage: str
    owner_id: str | None
    tags: list[str]
    custom: dict[str, Any]
    is_archived: bool
    created_at: datetime
    updated_at: datetime


# ------------------------------------------------------------------ contacts
class ContactCreate(BaseModel):
    org_id: UUID | None = None
    first_name: str = Field(min_length=1, max_length=128)
    last_name: str | None = Field(default=None, max_length=128)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=64)
    title: str | None = Field(default=None, max_length=255)
    tags: list[str] = Field(default_factory=list)
    custom: dict[str, Any] = Field(default_factory=dict)

    @field_validator("email")
    @classmethod
    def _norm_email(cls, v: str | None) -> str | None:
        return v.strip().lower() if v else v


class ContactUpdate(BaseModel):
    org_id: UUID | None = None
    first_name: str | None = Field(default=None, min_length=1, max_length=128)
    last_name: str | None = Field(default=None, max_length=128)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=64)
    title: str | None = Field(default=None, max_length=255)
    tags: list[str] | None = None
    custom: dict[str, Any] | None = None

    @field_validator("email")
    @classmethod
    def _norm_email(cls, v: str | None) -> str | None:
        return v.strip().lower() if v else v


class ContactRead(BaseModel):
    id: UUID
    tenant_id: UUID
    org_id: UUID | None
    first_name: str
    last_name: str | None
    email: str | None
    phone: str | None
    title: str | None
    tags: list[str]
    custom: dict[str, Any]
    is_archived: bool
    created_at: datetime
    updated_at: datetime


# ------------------------------------------------------------------ leads
class LeadCreate(BaseModel):
    contact_id: UUID | None = None
    org_id: UUID | None = None
    source: str | None = Field(default=None, max_length=64)
    status: str = Field(default="new", max_length=64)
    owner_id: str | None = None
    custom: dict[str, Any] = Field(default_factory=dict)


class LeadUpdate(BaseModel):
    contact_id: UUID | None = None
    org_id: UUID | None = None
    source: str | None = Field(default=None, max_length=64)
    status: str | None = Field(default=None, max_length=64)
    owner_id: str | None = None
    custom: dict[str, Any] | None = None


class LeadRead(BaseModel):
    id: UUID
    tenant_id: UUID
    contact_id: UUID | None
    org_id: UUID | None
    source: str | None
    status: str
    score: int | None
    score_breakdown: dict[str, Any] | None
    owner_id: str | None
    custom: dict[str, Any]
    converted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class LeadScoreResponse(BaseModel):
    lead_id: UUID
    score: int
    breakdown: dict[str, Any]


class LeadConvertResponse(BaseModel):
    lead_id: UUID
    organization_id: UUID
    contact_id: UUID
    opportunity_id: UUID


# ------------------------------------------------------------------ pipelines / opportunities
class PipelineStageCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    position: int = 0
    probability: Decimal | None = Field(default=None, ge=0, le=100)
    is_closed_won: bool = False
    is_closed_lost: bool = False


class PipelineCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    object_type: str = Field(default="opportunity", max_length=64)
    stages: list[PipelineStageCreate] = Field(min_length=1)


class PipelineStageRead(BaseModel):
    id: UUID
    name: str
    position: int
    probability: Decimal | None
    is_closed_won: bool
    is_closed_lost: bool


class PipelineRead(BaseModel):
    id: UUID
    tenant_id: UUID
    name: str
    object_type: str
    is_active: bool
    stages: list[PipelineStageRead] = []


class OpportunityCreate(BaseModel):
    pipeline_id: UUID
    stage_id: UUID | None = None  # defaults to the pipeline's first stage
    org_id: UUID | None = None
    contact_id: UUID | None = None
    name: str = Field(min_length=1, max_length=255)
    amount: Decimal | None = Field(default=None, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    close_date: date | None = None
    owner_id: str | None = None
    custom: dict[str, Any] = Field(default_factory=dict)


class OpportunityUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    amount: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    close_date: date | None = None
    org_id: UUID | None = None
    contact_id: UUID | None = None
    owner_id: str | None = None
    custom: dict[str, Any] | None = None


class OpportunityRead(BaseModel):
    id: UUID
    tenant_id: UUID
    pipeline_id: UUID
    stage_id: UUID
    org_id: UUID | None
    contact_id: UUID | None
    name: str
    amount: Decimal | None
    currency: str
    close_date: date | None
    owner_id: str | None
    custom: dict[str, Any]
    is_archived: bool
    created_at: datetime
    updated_at: datetime


class OpportunityMove(BaseModel):
    stage_id: UUID


# ------------------------------------------------------------------ activities
class ActivityCreate(BaseModel):
    subject_type: Literal["organization", "contact", "lead", "opportunity"]
    subject_id: UUID
    type: str = Field(min_length=1, max_length=64)  # call|email|meeting|note|...
    # also: outreach_draft, outreach_sent
    body: str | None = None
    occurred_at: datetime | None = None


class ActivityRead(BaseModel):
    id: UUID
    tenant_id: UUID
    subject_type: str
    subject_id: UUID
    type: str
    body: str | None
    occurred_at: datetime
    author_id: str | None
    created_at: datetime


# ------------------------------------------------------------------ custom fields
class CustomFieldDefinitionCreate(BaseModel):
    object_type: Literal["organization", "contact", "lead", "opportunity"]
    name: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_]*$")
    field_type: Literal["string", "number", "boolean", "date", "enum"]
    required: bool = False
    options: list[str] | None = None


class CustomFieldDefinitionRead(BaseModel):
    id: UUID
    tenant_id: UUID
    object_type: str
    name: str
    field_type: str
    required: bool
    options: list[str] | None


# ------------------------------------------------------------------ relationships
class RelationshipCreate(BaseModel):
    from_type: Literal["organization", "contact", "lead", "opportunity"]
    from_id: UUID
    to_type: Literal["organization", "contact", "lead", "opportunity"]
    to_id: UUID
    relation: str = Field(min_length=1, max_length=64)  # reports_to|works_at|referred|...


class RelationshipRead(BaseModel):
    id: UUID
    from_type: str
    from_id: UUID
    to_type: str
    to_id: UUID
    relation: str
