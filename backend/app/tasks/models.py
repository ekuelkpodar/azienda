"""Tasks/projects persistence models (SQLAlchemy 2.0, async).

Owned tables: projects, milestones, tasks, task_dependencies, task_transitions,
task_comments, task_bulk_op_records.

Repo convention (per alembic/env.py): all models share ``app.core.models.Base``
and use string ``GUID()`` PKs — the same pattern billing/ and finance/ follow.
``tenant_id`` is the opaque tenant string from ``TenantContext``; every query
in service.py filters by it.

Task state machine (binding, ARCHITECTURE.md §6):
  pending -> planning -> waiting_approval -> executing -> blocked -> completed|failed
  plus cancelled from any non-terminal state.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint
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


class Project(Base, TenantMixin):
    __tablename__ = "projects"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    owner_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # opaque user id


class Milestone(Base, TenantMixin):
    __tablename__ = "milestones"

    project_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    # open|done|cancelled


class Task(Base, TenantMixin):
    __tablename__ = "tasks"

    project_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("projects.id"), nullable=True, index=True
    )
    parent_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True, index=True
    )  # subtasks
    milestone_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("milestones.id"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="medium")
    assignee_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    assignee_agent_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    plan: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(19, 4), nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_tasks_tenant_idem"),
    )


class TaskDependency(Base):
    """task_id is blocked by depends_on_id. Cycles are rejected at write time."""

    __tablename__ = "task_dependencies"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    task_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    depends_on_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "task_id", "depends_on_id", name="uq_task_dep"),
    )


class TaskTransition(Base):
    __tablename__ = "task_transitions"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    task_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_status: Mapped[str] = mapped_column(String(32), nullable=False)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class TaskComment(Base, TenantMixin):
    __tablename__ = "task_comments"

    task_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    author_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    author_type: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    body: Mapped[str] = mapped_column(Text, nullable=False)


class TaskBulkOpRecord(Base):
    """Durable per-operation idempotency for bulk().

    Every bulk op carries an idempotency_key; the stored result is replayed on
    retry so non-create ops (update/transition/assign) are idempotent too.
    """

    __tablename__ = "task_bulk_op_records"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    op: Mapped[str] = mapped_column(String(32), nullable=False)
    task_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    result: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_bulkop_tenant_key"),
    )
