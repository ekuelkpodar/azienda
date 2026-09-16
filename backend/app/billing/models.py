"""SQLAlchemy models for billing: Azienda's own SaaS billing (DATABASE.md §11).

Pricing is DATA: plans and dimensions live in these tables, never in code.
``plans.py`` holds the seed dataset; the service layer only reads the DB.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import GUID, Base, TimestampMixin, new_uuid


class BillingPlan(Base, TimestampMixin):
    __tablename__ = "billing_plans"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)


class PlanDimension(Base):
    __tablename__ = "plan_dimensions"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    plan_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("billing_plans.id", ondelete="CASCADE"),
        nullable=False, index=True)
    dimension: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    __table_args__ = (
        UniqueConstraint("plan_id", "dimension", name="uq_plan_dimension"),
    )


class Subscription(Base, TimestampMixin):
    __tablename__ = "subscriptions"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, unique=True,
                                           index=True)
    plan_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("billing_plans.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False,
                                        default="trialing")
    current_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                           nullable=False)
    current_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                         nullable=False)
    spend_cap_usd: Mapped[Decimal | None] = mapped_column(Numeric(19, 4))


class CreditPool(Base):
    __tablename__ = "credit_pools"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    # Denormalized tenant_id: repo rule is tenant_id on EVERY row so RLS and
    # every query filter on the table itself (no join needed).
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    subscription_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("subscriptions.id", ondelete="CASCADE"),
        nullable=False, index=True)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                   nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    granted: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False,
                                             default=Decimal("0"))
    drawn: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False,
                                           default=Decimal("0"))

    __table_args__ = (
        UniqueConstraint("subscription_id", "period_start",
                         name="uq_pool_sub_period"),
    )


class CreditTransaction(Base):
    __tablename__ = "credit_transactions"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    # Denormalized tenant_id: repo rule is tenant_id on EVERY row so RLS and
    # every query filter on the table itself (no join needed).
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    pool_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("credit_pools.id", ondelete="CASCADE"),
        nullable=False, index=True)
    task_id: Mapped[str | None] = mapped_column(GUID(), index=True)
    delta: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False)
    reason: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                  default=datetime.utcnow)


class UsageMeter(Base):
    __tablename__ = "usage_meters"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    period: Mapped[date] = mapped_column(Date, nullable=False)
    dimension: Mapped[str] = mapped_column(String(64), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False,
                                              default=Decimal("0"))
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False,
                                              default=Decimal("0"))

    __table_args__ = (
        UniqueConstraint("tenant_id", "period", "dimension",
                         name="uq_usage_tenant_period_dim"),
    )


class BillingInvoice(Base, TimestampMixin):
    __tablename__ = "billing_invoices"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    subscription_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("subscriptions.id"), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                   nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lines: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False,
                                                   default=list)
    total_usd: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False,
                                               default=Decimal("0"))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
