"""SQLAlchemy models for core: tenancy, auth, idempotency.

Conventions (DATABASE.md): every tenant table has ``tenant_id uuid NOT NULL``;
PKs are UUIDs; timestamps are tz-aware UTC; money is Numeric(19,4).
``tenants`` itself is the root of the hierarchy (no tenant_id — it IS the tenant).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import CHAR, TypeDecorator, TypeEngine


class GUID(TypeDecorator[str]):
    """UUID that works on Postgres (native) and SQLite (CHAR(36)) for tests."""

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[str]:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID[Any](as_uuid=False))
        return CHAR(36)

    def process_bind_param(self, value: str | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        return str(value)

    def process_result_value(self, value: str | None, dialect: Dialect) -> str | None:
        return None if value is None else str(value)


def new_uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class Tenant(Base, TimestampMixin):
    """Root of the tenancy hierarchy. No tenant_id column — it IS the tenant."""

    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    spend_frozen: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    spend_frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    spend_frozen_reason: Mapped[str | None] = mapped_column(Text)


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255))  # null => OIDC-only
    display_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
        Index("ix_users_tenant", "tenant_id"),
    )


class Role(Base, TimestampMixin):
    """RBAC role definitions per tenant. ``permissions`` is a JSON list of strings."""

    __tablename__ = "roles"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    permissions: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_roles_tenant_name"),
    )


class UserRole(Base):
    __tablename__ = "user_roles"

    user_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)


class ApiKey(Base, TimestampMixin):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True,
                                          index=True)
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(GUID())


class RefreshToken(Base):
    """Opaque refresh tokens. Only SHA-256 hashes stored; rotation via replaced_by."""

    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True,
                                            index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by: Mapped[str | None] = mapped_column(GUID())  # id of the successor token
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 default=datetime.utcnow)


class IdempotencyKey(Base):
    """Replay guard for mutating POSTs (API.md §1). Response replayed within TTL."""

    __tablename__ = "idempotency_keys"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    status_code: Mapped[int] = mapped_column(nullable=False)
    response_body: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "key", name="uq_idempotency_tenant_key"),
    )
