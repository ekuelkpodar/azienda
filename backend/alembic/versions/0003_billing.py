"""0003: billing tables (own SaaS billing; pricing is data).

Revision ID: 0003_billing
Revises: 0002_governance
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

revision: str = "0003_billing"
down_revision: str | Sequence[str] | None = "0002_governance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# billing_plans / plan_dimensions are global catalogue tables (no tenant_id);
# everything else is tenant-scoped.
TENANT_TABLES = ["subscriptions", "credit_pools", "credit_transactions",
                 "usage_meters", "billing_invoices"]


def upgrade() -> None:
    op.create_table(
        "billing_plans",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("slug", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean, nullable=False,
                  server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_table(
        "plan_dimensions",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("plan_id", GUID(),
                  sa.ForeignKey("billing_plans.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("dimension", sa.String(64), nullable=False),
        sa.Column("value", sa.JSON, nullable=False),
        sa.UniqueConstraint("plan_id", "dimension", name="uq_plan_dimension"),
    )
    op.create_table(
        "subscriptions",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("tenant_id", GUID(), nullable=False, unique=True, index=True),
        sa.Column("plan_id", GUID(),
                  sa.ForeignKey("billing_plans.id"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False,
                  server_default="trialing"),
        sa.Column("current_period_start", sa.DateTime(timezone=True),
                  nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True),
                  nullable=False),
        sa.Column("spend_cap_usd", sa.Numeric(19, 4)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_table(
        "credit_pools",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("tenant_id", GUID(), nullable=False, index=True),
        sa.Column("subscription_id", GUID(),
                  sa.ForeignKey("subscriptions.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("granted", sa.Numeric(19, 4), nullable=False,
                  server_default="0"),
        sa.Column("drawn", sa.Numeric(19, 4), nullable=False, server_default="0"),
        sa.UniqueConstraint("subscription_id", "period_start",
                            name="uq_pool_sub_period"),
    )
    op.create_table(
        "credit_transactions",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("tenant_id", GUID(), nullable=False, index=True),
        sa.Column("pool_id", GUID(),
                  sa.ForeignKey("credit_pools.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("task_id", GUID(), index=True),
        sa.Column("delta", sa.Numeric(19, 4), nullable=False),
        sa.Column("reason", sa.String(512), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_table(
        "usage_meters",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("tenant_id", GUID(), nullable=False, index=True),
        sa.Column("period", sa.Date, nullable=False),
        sa.Column("dimension", sa.String(64), nullable=False),
        sa.Column("quantity", sa.Numeric(19, 4), nullable=False,
                  server_default="0"),
        sa.Column("cost_usd", sa.Numeric(19, 4), nullable=False,
                  server_default="0"),
        sa.UniqueConstraint("tenant_id", "period", "dimension",
                            name="uq_usage_tenant_period_dim"),
    )
    op.create_table(
        "billing_invoices",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("tenant_id", GUID(), nullable=False, index=True),
        sa.Column("subscription_id", GUID(),
                  sa.ForeignKey("subscriptions.id"), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lines", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("total_usd", sa.Numeric(19, 4), nullable=False,
                  server_default="0"),
        sa.Column("status", sa.String(32), nullable=False,
                  server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    for table in TENANT_TABLES:
        apply_rls(table)


def downgrade() -> None:
    for table in reversed(TENANT_TABLES):
        drop_rls(table)
        op.drop_table(table)
    op.drop_table("plan_dimensions")
    op.drop_table("billing_plans")
