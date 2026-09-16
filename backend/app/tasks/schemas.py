"""Tasks HTTP schemas (Pydantic v2). No business logic."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

TaskStatus = Literal[
    "pending", "planning", "waiting_approval", "executing",
    "blocked", "completed", "failed", "cancelled",
]
TaskPriority = Literal["low", "medium", "high", "urgent"]


# ------------------------------------------------------------------ projects
class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    owner_id: str | None = None


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    status: Literal["active", "on_hold", "completed", "archived"] | None = None
    owner_id: str | None = None


class ProjectRead(BaseModel):
    id: UUID
    tenant_id: UUID
    name: str
    description: str | None
    status: str
    owner_id: str | None
    created_at: datetime
    updated_at: datetime


# ------------------------------------------------------------------ milestones
class MilestoneCreate(BaseModel):
    project_id: UUID
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    due_at: datetime | None = None


class MilestoneUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    due_at: datetime | None = None
    status: Literal["open", "done", "cancelled"] | None = None


class MilestoneRead(BaseModel):
    id: UUID
    tenant_id: UUID
    project_id: UUID
    name: str
    description: str | None
    due_at: datetime | None
    status: str
    created_at: datetime


# ------------------------------------------------------------------ tasks
class TaskCreate(BaseModel):
    project_id: UUID | None = None
    parent_id: UUID | None = None
    milestone_id: UUID | None = None
    title: str = Field(min_length=1, max_length=500)
    description: str | None = None
    priority: TaskPriority = "medium"
    assignee_user_id: str | None = None
    assignee_agent_id: str | None = Field(default=None, max_length=128)
    due_at: datetime | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = None
    priority: TaskPriority | None = None
    project_id: UUID | None = None
    milestone_id: UUID | None = None
    due_at: datetime | None = None


class TaskRead(BaseModel):
    id: UUID
    tenant_id: UUID
    project_id: UUID | None
    parent_id: UUID | None
    milestone_id: UUID | None
    title: str
    description: str | None
    status: str
    priority: str
    assignee_user_id: str | None
    assignee_agent_id: str | None
    plan: dict[str, Any]
    cost_usd: Decimal | None
    due_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class TaskTransitionRequest(BaseModel):
    to: TaskStatus
    note: str | None = None


class TaskTransitionRead(BaseModel):
    id: UUID
    from_status: str
    to_status: str
    actor: str
    note: str | None
    created_at: datetime


class TaskAssignRequest(BaseModel):
    assignee_user_id: str | None = None
    assignee_agent_id: str | None = Field(default=None, max_length=128)


class TaskCommentCreate(BaseModel):
    body: str = Field(min_length=1)
    author_type: Literal["user", "agent", "system"] = "user"


class TaskCommentRead(BaseModel):
    id: UUID
    task_id: UUID
    author_id: str | None
    author_type: str
    body: str
    created_at: datetime


class TaskDependencyCreate(BaseModel):
    depends_on_id: UUID


class TaskDependencyRead(BaseModel):
    id: UUID
    task_id: UUID
    depends_on_id: UUID


class TaskDelegateRequest(BaseModel):
    agent_id: str | None = Field(default=None, max_length=128)
    capability: str | None = Field(default=None, max_length=128)
    goal: str | None = None


class TaskOutcomeRead(BaseModel):
    task_id: UUID
    status: str
    plan: dict[str, Any]
    cost_usd: Decimal | None
    transitions: list[TaskTransitionRead]
    comments_count: int
    dependencies: list[TaskDependencyRead]
    subtasks: list[TaskRead]


class BulkOperation(BaseModel):
    op: Literal["create", "update", "transition", "assign"]
    idempotency_key: str = Field(min_length=1, max_length=128)
    task_id: UUID | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class BulkRequest(BaseModel):
    operations: list[BulkOperation] = Field(min_length=1, max_length=100)


class BulkItemResult(BaseModel):
    idempotency_key: str
    ok: bool
    task_id: UUID | None = None
    error: dict[str, Any] | None = None


class BulkResponse(BaseModel):
    results: list[BulkItemResult]
