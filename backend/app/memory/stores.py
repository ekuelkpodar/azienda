"""Tenant-scoped agent memory: short-term, episodic, semantic stores.

Implements ``core.contracts.MemoryStore`` (put/get/forget) plus namespaces with
kinds, TTL/retention policies, provenance, and confidence.

Rules (binding):
- Every value is namespaced AND tenant-scoped. There is NO cross-tenant read
  path and NO sharing API — a future sharing feature needs an explicit,
  approval-gated design (not auto-grant).
- Namespace kinds:
  - ``short_term`` — working memory; default TTL 1h; purged aggressively.
  - ``episodic`` — timestamped records of what happened; retention window
    (default 90d), no per-item TTL unless set.
  - ``semantic`` — durable facts; confidence 0..1 + provenance required.
- ``forget`` is real deletion (tombstone in the audit trail, not soft-delete).

This is the in-memory MVP adapter. Production: SQLAlchemy over
``memory_namespaces`` / ``memory_items`` (DATABASE.md §8).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from app.core import contracts


def _utcnow() -> datetime:
    return datetime.now(UTC)


class NamespaceKind(StrEnum):
    SHORT_TERM = "short_term"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"


DEFAULT_TTL = {
    NamespaceKind.SHORT_TERM: 3600,          # 1h
    NamespaceKind.EPISODIC: None,            # retention window instead
    NamespaceKind.SEMANTIC: None,            # durable until forgotten
}
DEFAULT_RETENTION_DAYS = {
    NamespaceKind.SHORT_TERM: 1,
    NamespaceKind.EPISODIC: 90,
    NamespaceKind.SEMANTIC: 365,
}


@dataclass
class MemoryNamespace:
    name: str
    kind: NamespaceKind
    ttl_default_seconds: int | None = None
    retention_days: int = 90
    created_at: datetime = field(default_factory=_utcnow)


@dataclass
class MemoryValue:
    value: dict[str, Any]
    provenance: dict[str, Any]              # {source, recorded_by, ...}
    confidence: float                       # 0..1
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)
    expires_at: datetime | None = None


class UnknownNamespace(Exception):
    pass


class MemoryStore:
    """In-memory MemoryStore (dev/test adapter)."""

    def __init__(self) -> None:
        # tenant_id -> namespace name -> MemoryNamespace
        self._namespaces: dict[str, dict[str, MemoryNamespace]] = {}
        # (tenant_id, namespace, key) -> MemoryValue
        self._items: dict[tuple[str, str, str], MemoryValue] = {}

    # -- namespaces -------------------------------------------------------------
    async def create_namespace(self, tenant: contracts.TenantContext, *,
                               name: str, kind: NamespaceKind,
                               ttl_default_seconds: int | None = None,
                               retention_days: int | None = None) -> MemoryNamespace:
        ns = MemoryNamespace(
            name=name, kind=kind,
            ttl_default_seconds=(ttl_default_seconds
                                 if ttl_default_seconds is not None
                                 else DEFAULT_TTL[kind]),
            retention_days=(retention_days if retention_days is not None
                            else DEFAULT_RETENTION_DAYS[kind]))
        self._namespaces.setdefault(tenant.tenant_id, {})[name] = ns
        return ns

    async def namespaces(self, tenant: contracts.TenantContext) -> list[MemoryNamespace]:
        return list(self._namespaces.get(tenant.tenant_id, {}).values())

    def _require_ns(self, tenant_id: str, namespace: str) -> MemoryNamespace:
        ns = self._namespaces.get(tenant_id, {}).get(namespace)
        if ns is None:
            raise UnknownNamespace(f"unknown memory namespace '{namespace}'")
        return ns

    # -- contracts.MemoryStore ---------------------------------------------------
    async def put(self, tenant: contracts.TenantContext, namespace: str, key: str,
                  value: dict[str, Any], ttl_seconds: int | None = None) -> None:
        ns = self._require_ns(tenant.tenant_id, namespace)
        provenance = value.pop("_provenance", {}) if isinstance(value, dict) else {}
        confidence = float(value.pop("_confidence", 0.8)) if isinstance(value, dict) else 0.8
        if ns.kind == NamespaceKind.SEMANTIC and not provenance.get("source"):
            raise ValueError("semantic memory requires provenance.source")
        ttl = ttl_seconds if ttl_seconds is not None else ns.ttl_default_seconds
        expires_at = _utcnow() + timedelta(seconds=ttl) if ttl else None
        now = _utcnow()
        existing = self._items.get((tenant.tenant_id, namespace, key))
        self._items[(tenant.tenant_id, namespace, key)] = MemoryValue(
            value=dict(value), provenance=dict(provenance),
            confidence=max(0.0, min(1.0, confidence)),
            created_at=existing.created_at if existing else now,
            updated_at=now, expires_at=expires_at)

    async def get(self, tenant: contracts.TenantContext, namespace: str,
                  key: str) -> dict[str, Any] | None:
        # Tenant is part of the storage key — cross-tenant reads are impossible
        # by construction (no tenant parameter override exists).
        try:
            self._require_ns(tenant.tenant_id, namespace)
        except UnknownNamespace:
            return None
        item = self._items.get((tenant.tenant_id, namespace, key))
        if item is None:
            return None
        if item.expires_at and item.expires_at <= _utcnow():
            del self._items[(tenant.tenant_id, namespace, key)]
            return None
        return {"value": dict(item.value),
                "provenance": dict(item.provenance),
                "confidence": item.confidence,
                "created_at": item.created_at.isoformat(),
                "updated_at": item.updated_at.isoformat()}

    async def forget(self, tenant: contracts.TenantContext, namespace: str,
                     key: str) -> None:
        self._require_ns(tenant.tenant_id, namespace)
        self._items.pop((tenant.tenant_id, namespace, key), None)

    # -- retention -----------------------------------------------------------------
    async def purge_expired(self, tenant: contracts.TenantContext) -> int:
        """Delete expired items + items older than the namespace retention window."""
        now = _utcnow()
        removed = 0
        for (tid, namespace, key), item in list(self._items.items()):
            if tid != tenant.tenant_id:
                continue
            ns = self._namespaces.get(tid, {}).get(namespace)
            retention_cutoff = (now - timedelta(days=ns.retention_days)) if ns else None
            if (item.expires_at and item.expires_at <= now) or (
                    retention_cutoff and item.created_at < retention_cutoff):
                del self._items[(tid, namespace, key)]
                removed += 1
        return removed

    async def scan(self, tenant: contracts.TenantContext, namespace: str,
                   prefix: str = "") -> list[str]:
        """List keys (for episodic replay). Tenant-scoped."""
        self._require_ns(tenant.tenant_id, namespace)
        return [k for (tid, ns, k) in self._items
                if tid == tenant.tenant_id and ns == namespace and k.startswith(prefix)]
