"""0002: governance tables (policy, approvals, audit, budgets).

Revision ID: 0002_governance
Revises: 0001_tenancy_auth
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.models import GUID
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

from helpers import apply_rls, drop_rls

revision: str = "0002_governance"
down_revision: str | Sequence[str] | None = "0001_tenancy_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = ["policies", "policy_versions", "risk_scores", "approvals",
                 "approval_events", "audit_ledger", "budgets", "budget_periods",
                 "budget_reservations", "cost_ledger", "spend_alerts"]


def upgrade() -> None:
    op.create_table(
        "policies",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("tenant_id", GUID(), nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("rules", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("rego_source", sa.Text),
        sa.Column("is_active", sa.Boolean, nullable=False,
                  server_default=sa.true()),
        sa.Column("priority", sa.Integer, nullable=False,
                  server_default="100"),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("updated_by", GUID()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_table(
        "policy_versions",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("policy_id", GUID(),
                  sa.ForeignKey("policies.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("tenant_id", GUID(), nullable=False, index=True),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("rules", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_table(
        "risk_scores",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("tenant_id", GUID(), nullable=False, index=True),
        sa.Column("action_ref", sa.String(512), nullable=False),
        sa.Column("score", sa.Numeric(5, 2), nullable=False),
        sa.Column("factors", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("reversible", sa.Boolean, nullable=False,
                  server_default=sa.true()),
        sa.Column("financial_impact_usd", sa.Numeric(19, 4), nullable=False,
                  server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_table(
        "approvals",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("tenant_id", GUID(), nullable=False, index=True),
        sa.Column("action", sa.String(512), nullable=False),
        sa.Column("resource", sa.String(1024)),
        sa.Column("args", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("risk_score", sa.Numeric(5, 2)),
        sa.Column("risk_factors", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("policy_id", GUID()),
        sa.Column("reasons", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("status", sa.String(32), nullable=False,
                  server_default="pending", index=True),
        sa.Column("requested_by", GUID()),
        sa.Column("requested_by_kind", sa.String(32), nullable=False,
                  server_default="user"),
        sa.Column("decided_by", GUID()),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text),
        sa.Column("idempotency_key", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_table(
        "approval_events",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("approval_id", GUID(),
                  sa.ForeignKey("approvals.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("tenant_id", GUID(), nullable=False, index=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("actor", sa.String(255)),
        sa.Column("payload", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_table(
        "audit_ledger",
        # bigserial on Postgres; INTEGER PK on SQLite (its only autoincrement).
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer, "sqlite"),
                  primary_key=True, autoincrement=True),
        sa.Column("tenant_id", GUID(), nullable=False, index=True),
        sa.Column("seq", sa.BigInteger, nullable=False),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("action", sa.String(255), nullable=False, index=True),
        sa.Column("payload", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("prev_hash", sa.String(128), nullable=False),
        sa.Column("hash", sa.String(128), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Index("ix_audit_tenant_seq", "tenant_id", "seq", unique=True),
    )
    op.create_table(
        "budgets",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("tenant_id", GUID(), nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("scope", sa.String(32), nullable=False,
                  server_default="tenant"),
        sa.Column("scope_ref", sa.String(255)),
        sa.Column("credit_limit", sa.Numeric(19, 4), nullable=False),
        sa.Column("period", sa.String(16), nullable=False,
                  server_default="monthly"),
        sa.Column("alert_thresholds", sa.JSON, nullable=False,
                  server_default="[]"),
        sa.Column("is_active", sa.Boolean, nullable=False,
                  server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_table(
        "budget_periods",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("budget_id", GUID(),
                  sa.ForeignKey("budgets.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("tenant_id", GUID(), nullable=False, index=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("credits_used", sa.Numeric(19, 4), nullable=False,
                  server_default="0"),
        sa.Index("ix_budget_periods_budget_window", "budget_id", "starts_at",
                 "ends_at"),
    )
    op.create_table(
        "budget_reservations",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("tenant_id", GUID(), nullable=False, index=True),
        sa.Column("budget_id", GUID(),
                  sa.ForeignKey("budgets.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("period_id", GUID(),
                  sa.ForeignKey("budget_periods.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("credits_reserved", sa.Numeric(19, 4), nullable=False),
        sa.Column("purpose", sa.String(512), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False,
                  server_default="held", index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "cost_ledger",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("tenant_id", GUID(), nullable=False, index=True),
        sa.Column("task_id", GUID(), index=True),
        sa.Column("run_id", GUID(), index=True),
        sa.Column("model", sa.String(255), nullable=False, server_default=""),
        sa.Column("input_tokens", sa.BigInteger, nullable=False,
                  server_default="0"),
        sa.Column("output_tokens", sa.BigInteger, nullable=False,
                  server_default="0"),
        sa.Column("cost_usd", sa.Numeric(19, 4), nullable=False,
                  server_default="0"),
        sa.Column("credits_drawn", sa.Numeric(19, 4), nullable=False,
                  server_default="0"),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_table(
        "spend_alerts",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("tenant_id", GUID(), nullable=False, index=True),
        sa.Column("budget_id", GUID(),
                  sa.ForeignKey("budgets.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("threshold", sa.Numeric(5, 4), nullable=False),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
    )
    for table in TENANT_TABLES:
        # audit_ledger is append-only: app role gets INSERT+SELECT, never
        # UPDATE/DELETE. The hash chain is verified in software too.
        apply_rls(table, append_only=(table == "audit_ledger"))


def downgrade() -> None:
    for table in reversed(TENANT_TABLES):
        drop_rls(table)
        op.drop_table(table)
