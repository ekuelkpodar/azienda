"""Workflow HTTP schemas (Pydantic v2). No business logic."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from .dsl import DagSpec


class DefinitionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    autonomy_level: int = Field(default=1, ge=0, le=5)
    dag: dict[str, Any]  # validated against the DSL; stored as draft


class DefinitionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    autonomy_level: int | None = Field(default=None, ge=0, le=5)
    is_active: bool | None = None
    dag: dict[str, Any] | None = None


class DefinitionRead(BaseModel):
    id: UUID
    tenant_id: UUID
    name: str
    description: str | None
    autonomy_level: int
    is_active: bool
    latest_version: int | None
    created_at: datetime
    updated_at: datetime


class VersionRead(BaseModel):
    id: UUID
    definition_id: UUID
    version: int
    dag: dict[str, Any]
    published_at: datetime
    published_by: str | None


class ExecutionStart(BaseModel):
    definition_id: UUID | None = None
    definition_name: str | None = None  # alternative to id: resolves latest version
    version: int | None = None  # pins a version; default = latest
    input: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, max_length=128)


class ExecutionRead(BaseModel):
    id: UUID
    tenant_id: UUID
    definition_id: UUID
    version: int
    status: str
    input: dict[str, Any]
    current_node: str | None
    cost_usd: Decimal | None
    error: str | None
    started_at: datetime
    finished_at: datetime | None


class ExecutionEventRead(BaseModel):
    seq: int
    event_type: str
    payload: dict[str, Any]
    created_at: datetime


class SignalRequest(BaseModel):
    signal: str = Field(min_length=1)  # "approval" for HITL decisions
    payload: dict[str, Any] = Field(default_factory=dict)


class CancelRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


# re-exported so routers can validate drafts without importing dsl internals
__all__ = [
    "DagSpec",
    "DefinitionCreate",
    "DefinitionUpdate",
    "DefinitionRead",
    "VersionRead",
    "ExecutionStart",
    "ExecutionRead",
    "ExecutionEventRead",
    "SignalRequest",
    "CancelRequest",
]
