"""SQLAlchemy models for governance: policy, approvals, audit, budgets.

Tables owned by ``governance/`` (DATABASE.md §7) plus one documented addition:
``budget_reservations`` (reserve-before-spend needs durable reservations; the
schema lists the concept but no table — recorded here as an extension).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import GUID, Base, TimestampMixin, new_uuid


# ------------------------------------------------------------------ policy
class Policy(Base, TimestampMixin):
    __tablename__ = "policies"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Rego source is accepted for future OPA use; the Python engine ignores it.
    # rules: JSON list of {effect, priority, name, condition, obligations}.
    rules: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    rego_source: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    priority: Mapped[int] = mapped_column(nullable=False, default=100)
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    updated_by: Mapped[str | None] = mapped_column(GUID())


class PolicyVersion(Base):
    __tablename__ = "policy_versions"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    policy_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("policies.id", ondelete="CASCADE"),
        nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    version: Mapped[int] = mapped_column(nullable=False)
    rules: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 default=datetime.utcnow)


class RiskScoreRecord(Base):
    __tablename__ = "risk_scores"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    action_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    factors: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    reversible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    financial_impact_usd: Mapped[Decimal] = mapped_column(
        Numeric(19, 4), nullable=False, default=Decimal("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 default=datetime.utcnow)


# ------------------------------------------------------------------ approvals
class ApprovalRecord(Base, TimestampMixin):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(512), nullable=False)
    resource: Mapped[str | None] = mapped_column(String(1024))
    args: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    risk_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    risk_factors: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    policy_id: Mapped[str | None] = mapped_column(GUID())
    reasons: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending",
                                        index=True)
    requested_by: Mapped[str | None] = mapped_column(GUID())
    requested_by_kind: Mapped[str] = mapped_column(String(32), nullable=False,
                                                   default="user")
    decided_by: Mapped[str | None] = mapped_column(GUID())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))


class ApprovalEvent(Base):
    __tablename__ = "approval_events"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    approval_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("approvals.id", ondelete="CASCADE"),
        nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str | None] = mapped_column(String(255))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 default=datetime.utcnow)


# ------------------------------------------------------------------ audit
class AuditEntry(Base):
    """Append-only, hash-chained. App role gets INSERT+SELECT only (migration)."""

    __tablename__ = "audit_ledger"

    # BigInteger (bigserial on Postgres) for a ledger that must never wrap.
    # SQLite only auto-increments INTEGER PRIMARY KEY, so the sqlite variant
    # keeps tests honest without changing the Postgres DDL.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    hash: Mapped[str] = mapped_column(String(128), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                  default=datetime.utcnow)

    __table_args__ = (
        Index("ix_audit_tenant_seq", "tenant_id", "seq", unique=True),
    )


# ------------------------------------------------------------------ budgets
class Budget(Base, TimestampMixin):
    __tablename__ = "budgets"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    scope: Mapped[str] = mapped_column(String(32), nullable=False, default="tenant")
    scope_ref: Mapped[str | None] = mapped_column(String(255))
    credit_limit: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False)
    period: Mapped[str] = mapped_column(String(16), nullable=False, default="monthly")
    alert_thresholds: Mapped[list[str]] = mapped_column(JSON, nullable=False,
                                                   default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class BudgetPeriod(Base):
    __tablename__ = "budget_periods"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    budget_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("budgets.id", ondelete="CASCADE"),
        nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    credits_used: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False,
                                                  default=Decimal("0"))

    __table_args__ = (
        Index("ix_budget_periods_budget_window", "budget_id", "starts_at", "ends_at"),
    )


class BudgetReservation(Base):
    """Durable hold placed by reserve(); released or settled by settle()."""

    __tablename__ = "budget_reservations"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    budget_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("budgets.id", ondelete="CASCADE"), nullable=False)
    period_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("budget_periods.id", ondelete="CASCADE"), nullable=False)
    credits_reserved: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False)
    purpose: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="held",
                                        index=True)  # held | settled | released
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CostLedgerEntry(Base):
    __tablename__ = "cost_ledger"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    task_id: Mapped[str | None] = mapped_column(GUID(), index=True)
    run_id: Mapped[str | None] = mapped_column(GUID(), index=True)
    model: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False,
                                              default=Decimal("0"))
    credits_drawn: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False,
                                                    default=Decimal("0"))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                  default=datetime.utcnow)


class SpendAlert(Base):
    __tablename__ = "spend_alerts"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    budget_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("budgets.id", ondelete="CASCADE"),
        nullable=False, index=True)
    threshold: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    fired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                               default=datetime.utcnow)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
