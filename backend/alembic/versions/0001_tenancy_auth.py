"""0001: tenancy + auth tables (core/).

Revision ID: 0001_tenancy_auth
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

revision: str = "0001_tenancy_auth"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# tenants has no tenant_id (it IS the tenant) -> RLS policy on id instead.
TENANT_TABLES = ["users", "roles", "api_keys", "refresh_tokens",
                 "idempotency_keys"]


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), nullable=False, unique=True),
        sa.Column("settings", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("spend_frozen", sa.Boolean, nullable=False,
                  server_default=sa.false()),
        sa.Column("spend_frozen_at", sa.DateTime(timezone=True)),
        sa.Column("spend_frozen_reason", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_table(
        "users",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("tenant_id", GUID(),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(255)),
        sa.Column("display_name", sa.String(255), nullable=False,
                  server_default=""),
        sa.Column("is_active", sa.Boolean, nullable=False,
                  server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
        sa.Index("ix_users_tenant", "tenant_id"),
    )
    op.create_table(
        "roles",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("tenant_id", GUID(),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("permissions", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("is_system", sa.Boolean, nullable=False,
                  server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "name", name="uq_roles_tenant_name"),
    )
    op.create_table(
        "user_roles",
        sa.Column("user_id", GUID(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("role_id", GUID(),
                  sa.ForeignKey("roles.id", ondelete="CASCADE"),
                  primary_key=True),
    )
    op.create_table(
        "api_keys",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("tenant_id", GUID(),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("key_hash", sa.String(128), nullable=False, unique=True,
                  index=True),
        sa.Column("key_prefix", sa.String(16), nullable=False, server_default=""),
        sa.Column("scopes", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", GUID()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_table(
        "refresh_tokens",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("tenant_id", GUID(),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("user_id", GUID(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("token_hash", sa.String(128), nullable=False, unique=True,
                  index=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("replaced_by", GUID()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_table(
        "idempotency_keys",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("tenant_id", GUID(), nullable=False, index=True),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("path", sa.String(1024), nullable=False),
        sa.Column("request_hash", sa.String(128), nullable=False),
        sa.Column("status_code", sa.Integer, nullable=False),
        sa.Column("response_body", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "key", name="uq_idempotency_tenant_key"),
    )
    # Defense in depth (ADR-008): RLS on every tenant table. Root table
    # `tenants` is policed on its own id.
    apply_rls("tenants", tenant_col="id")
    for table in TENANT_TABLES:
        apply_rls(table)


def downgrade() -> None:
    for table in TENANT_TABLES:
        drop_rls(table)
    drop_rls("tenants")
    op.drop_table("idempotency_keys")
    op.drop_table("refresh_tokens")
    op.drop_table("api_keys")
    op.drop_table("user_roles")
    op.drop_table("roles")
    op.drop_table("users")
    op.drop_table("tenants")
