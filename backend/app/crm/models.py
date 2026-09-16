"""CRM persistence models (SQLAlchemy 2.0, async).

Owned tables: organizations, contacts, leads, pipelines, pipeline_stages,
opportunities, activities, custom_field_definitions, crm_relationships.

Repo convention (per alembic/env.py): all models share ``app.core.models.Base``
and use string ``GUID()`` PKs — the same pattern billing/ and finance/ follow.
``tenant_id`` is the opaque tenant string from ``TenantContext``; every query
in service.py filters by it.

HTTP idempotency uses the foundation's ``core.idempotency.IdempotencyStore``
(``idempotency_keys`` table, owned by core) — not a table here.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import GUID, Base, TimestampMixin, new_uuid


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TenantMixin(TimestampMixin):
    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class Organization(Base, TenantMixin):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_band: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # startup|smb|mid|enterprise
    lifecycle_stage: Mapped[str] = mapped_column(String(64), nullable=False, default="prospect")
    owner_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # opaque user id
    tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    custom: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Contact(Base, TenantMixin):
    __tablename__ = "contacts"

    org_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("organizations.id"), nullable=True, index=True
    )
    first_name: Mapped[str] = mapped_column(String(128), nullable=False)
    last_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    custom: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Lead(Base, TenantMixin):
    __tablename__ = "leads"

    contact_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("contacts.id"), nullable=True
    )
    org_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("organizations.id"), nullable=True
    )
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="new", index=True)
    score: Mapped[int | None] = mapped_column(nullable=True)
    score_breakdown: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    owner_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    custom: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    converted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Pipeline(Base, TenantMixin):
    __tablename__ = "pipelines"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    object_type: Mapped[str] = mapped_column(String(64), nullable=False, default="opportunity")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_pipelines_tenant_name"),)


class PipelineStage(Base, TenantMixin):
    __tablename__ = "pipeline_stages"

    pipeline_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("pipelines.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    position: Mapped[int] = mapped_column(nullable=False, default=0)
    probability: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    is_closed_won: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_closed_lost: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Opportunity(Base, TenantMixin):
    __tablename__ = "opportunities"

    pipeline_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("pipelines.id"), nullable=False, index=True
    )
    stage_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("pipeline_stages.id"), nullable=False, index=True
    )
    org_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("organizations.id"), nullable=True, index=True
    )
    contact_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("contacts.id"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    amount: Mapped[float | None] = mapped_column(Numeric(19, 4), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    close_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    owner_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    custom: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Activity(Base, TenantMixin):
    __tablename__ = "activities"

    subject_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    subject_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    author_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class CustomFieldDefinition(Base, TenantMixin):
    """Per-tenant schema for the free-form `custom` JSON on CRM objects."""

    __tablename__ = "custom_field_definitions"

    object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # organization|contact|lead|opportunity
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    field_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # string|number|boolean|date|enum
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    options: Mapped[list | None] = mapped_column(JSON, nullable=True)  # allowed values for enum

    __table_args__ = (
        UniqueConstraint("tenant_id", "object_type", "name", name="uq_cfd_tenant_obj_name"),
    )


class Relationship(Base, TenantMixin):
    """Typed link between any two CRM records (e.g. contact reports_to contact)."""

    __tablename__ = "crm_relationships"

    from_type: Mapped[str] = mapped_column(String(64), nullable=False)
    from_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    to_type: Mapped[str] = mapped_column(String(64), nullable=False)
    to_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    relation: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "from_type", "from_id", "to_type", "to_id", "relation",
            name="uq_crm_rel",
        ),
    )
