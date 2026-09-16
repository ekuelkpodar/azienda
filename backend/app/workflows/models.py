"""Workflow persistence models (SQLAlchemy 2.0, async).

Owned tables: workflow_definitions, workflow_versions, workflow_executions,
execution_events.

Repo convention (per alembic/env.py): all models share ``app.core.models.Base``
and use string ``GUID()`` PKs — the same pattern billing/ and finance/ follow.
``tenant_id`` is the opaque tenant string from ``TenantContext``.

Durability model: an execution's resumable state lives in
workflow_executions.state (JSON); every step appends to execution_events.
The runner (runner.py) is re-entrant: crash -> resume from stored state.
Versions are immutable once published; executions pin a version.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
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


class WorkflowDefinition(Base, TimestampMixin):
    __tablename__ = "workflow_definitions"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    autonomy_level: Mapped[int] = mapped_column(nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    draft_dag: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_wf_def_tenant_name"),
    )


class WorkflowVersion(Base):
    __tablename__ = "workflow_versions"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    definition_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("workflow_definitions.id", ondelete="CASCADE"),
        nullable=False, index=True)
    version: Mapped[int] = mapped_column(nullable=False)
    dag: Mapped[dict] = mapped_column(JSON, nullable=False)  # immutable once published
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow)
    published_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        UniqueConstraint("definition_id", "version", name="uq_wf_ver_def_version"),
    )


class WorkflowExecution(Base):
    __tablename__ = "workflow_executions"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    definition_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("workflow_definitions.id"), nullable=False, index=True)
    version_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("workflow_versions.id"), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running", index=True)
    input: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    state: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(19, 4), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_wf_exec_tenant_idem"),
    )


class ExecutionEvent(Base):
    __tablename__ = "execution_events"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    execution_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("workflow_executions.id", ondelete="CASCADE"),
        nullable=False, index=True)
    seq: Mapped[int] = mapped_column(nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow)

    __table_args__ = (
        UniqueConstraint("execution_id", "seq", name="uq_exec_event_seq"),
    )
