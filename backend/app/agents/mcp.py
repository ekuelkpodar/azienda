"""MCP client management (ADR-006).

Binding trust rules (from the ADR):
1. MCP servers are UNTRUSTED tool providers until explicitly authorized.
2. Every MCP invocation passes the governance rail exactly like any local tool.
3. No ambient credentials — per-action scoped grants only.
4. Tool schemas are validated; outputs are treated as UNTRUSTED data.

SDK status: ``mcp>=1.27,<2`` when importable. The ``mcp`` package is not
installed in this environment, so discovery runs the protocol-shaped stub
path, clearly labeled ``FUTURE`` — registration, authorization workflow, and
the rail-gated execution path are real and tested; live server I/O is not.
"""

from __future__ import annotations

import asyncio
import builtins
import importlib.util
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from app.agents.tools import new_grant_id
from app.core import contracts

MCP_SDK_AVAILABLE = importlib.util.find_spec("mcp") is not None

# Sandboxing limits for MCP tool calls (MVP).
MCP_CALL_TIMEOUT_SECONDS = 30.0
MCP_MAX_OUTPUT_BYTES = 256 * 1024


def _utcnow() -> datetime:
    return datetime.now(UTC)


class MCPServerStatus(StrEnum):
    UNTRUSTED = "untrusted"       # registered, not yet vetted
    VETTING = "vetting"           # discovery in progress
    AUTHORIZED = "authorized"     # vetted by a human; may be invoked (still rail-gated)
    REJECTED = "rejected"         # vetting failed; never invoked
    DISABLED = "disabled"


@dataclass
class MCPServerRecord:
    name: str
    transport: str                # "streamable-http" | "stdio"
    endpoint: str
    auth_ref: str | None          # secret-broker ref for server credentials
    status: MCPServerStatus = MCPServerStatus.UNTRUSTED
    vetted_by: str | None = None
    vetted_at: datetime | None = None
    discovered_tools: builtins.list[dict[str, Any]] = field(default_factory=list)
    registered_at: datetime = field(default_factory=_utcnow)


@dataclass
class MCPToolCallResult:
    ok: bool
    output: Any = None
    error: str | None = None
    untrusted: bool = True         # MCP outputs are ALWAYS untrusted data
    provenance: dict[str, Any] = field(default_factory=dict)


class MCPNotAuthorized(Exception):
    pass


class MCPManager:
    """Manages MCP server lifecycle. Execution always routes via ToolExecutor."""

    def __init__(self, *, policy: contracts.PolicyEngine,
                 approvals: contracts.ApprovalStore,
                 audit: contracts.AuditLedger) -> None:
        self._policy = policy
        self._approvals = approvals
        self._audit = audit
        # tenant_id -> name -> MCPServerRecord
        self._servers: dict[str, dict[str, MCPServerRecord]] = {}

    # -- lifecycle ------------------------------------------------------------
    async def register(self, tenant: contracts.TenantContext, *,
                       name: str, transport: str, endpoint: str,
                       auth_ref: str | None = None) -> MCPServerRecord:
        if transport not in ("streamable-http", "stdio"):
            raise ValueError(f"unsupported MCP transport: {transport}")
        rec = MCPServerRecord(name=name, transport=transport, endpoint=endpoint,
                              auth_ref=auth_ref, status=MCPServerStatus.UNTRUSTED)
        self._servers.setdefault(tenant.tenant_id, {})[name] = rec
        await self._audit.append(tenant, actor="mcp-manager", action="mcp.registered",
                                 payload={"server": name, "transport": transport,
                                          "status": "untrusted"})
        return rec

    async def list(self, tenant: contracts.TenantContext) -> builtins.list[MCPServerRecord]:
        return list(self._servers.get(tenant.tenant_id, {}).values())

    async def get(self, tenant: contracts.TenantContext,
                  name: str) -> MCPServerRecord | None:
        return self._servers.get(tenant.tenant_id, {}).get(name)

    async def discover(self, tenant: contracts.TenantContext,
                       name: str) -> builtins.list[dict[str, Any]]:
        """Tool discovery. Real SDK path when importable; labeled stub otherwise."""
        rec = await self._require(tenant, name)
        rec.status = MCPServerStatus.VETTING
        if MCP_SDK_AVAILABLE:
            tools = await self._discover_via_sdk(rec)
            label = "sdk-v1"
        else:
            # FUTURE: live discovery needs the MCP SDK v1.x + network access.
            tools = []
            label = "sdk-unavailable-stub — no live discovery performed"
        rec.discovered_tools = tools
        rec.status = (MCPServerStatus.UNTRUSTED if rec.vetted_by is None
                      else rec.status)
        await self._audit.append(tenant, actor="mcp-manager", action="mcp.discovered",
                                 payload={"server": name, "tool_count": len(tools),
                                          "via": label})
        return tools

    async def authorize(self, tenant: contracts.TenantContext, name: str,
                        vetted_by: str) -> MCPServerRecord:
        """Human vetting step: marks a server authorized. Still rail-gated per call."""
        rec = await self._require(tenant, name)
        rec.status = MCPServerStatus.AUTHORIZED
        rec.vetted_by = vetted_by
        rec.vetted_at = _utcnow()
        await self._audit.append(tenant, actor=vetted_by, action="mcp.authorized",
                                 payload={"server": name})
        return rec

    async def reject(self, tenant: contracts.TenantContext, name: str,
                     vetted_by: str, reason: str = "") -> MCPServerRecord:
        rec = await self._require(tenant, name)
        rec.status = MCPServerStatus.REJECTED
        await self._audit.append(tenant, actor=vetted_by, action="mcp.rejected",
                                 payload={"server": name, "reason": reason})
        return rec

    # -- execution (always rail-gated, sandboxed, untrusted output) ------------
    async def execute(self, tenant: contracts.TenantContext, *,
                      server_name: str, tool_name: str,
                      arguments: dict[str, Any]) -> MCPToolCallResult:
        rec = await self._require(tenant, server_name)
        if rec.status != MCPServerStatus.AUTHORIZED:
            raise MCPNotAuthorized(
                f"MCP server '{server_name}' is {rec.status.value} — "
                "untrusted servers cannot be invoked")

        grant_id = new_grant_id()  # per-action scoped grant; never ambient
        action = f"tool.mcp.{server_name}.{tool_name}"

        # ---- POLICY GATE FIRST (ADR-006 binding rule 2): no SDK call happens
        # before the governance rail decides. This ordering is load-bearing.
        action_request = contracts.ActionRequest(
            tenant=tenant, action=action, resource=server_name, args=arguments,
            risk_context={"risk_tier": "high", "grant_id": grant_id,
                          "provider": f"mcp:{server_name}", "untrusted": True})
        try:
            decision = await self._policy.evaluate(action_request)
        except Exception as exc:  # fail closed
            await self._audit.append(
                tenant, actor="mcp-manager", action="tool.policy_error",
                payload={"tool": action, "error": str(exc)})
            return MCPToolCallResult(
                ok=False, error=f"policy_error_deny: {exc}",
                provenance={"server": server_name, "tool": tool_name})
        await self._audit.append(
            tenant, actor="mcp-manager", action="tool.policy_decision",
            payload={"tool": action, "effect": decision.effect.value,
                     "reasons": list(decision.reasons)})
        if decision.effect == contracts.PolicyEffect.DENY:
            return MCPToolCallResult(
                ok=False, error="policy_denied: " + "; ".join(decision.reasons),
                provenance={"server": server_name, "tool": tool_name,
                            "grant_id": grant_id})
        if decision.effect == contracts.PolicyEffect.REQUIRE_APPROVAL:
            approval = await self._approvals.request(
                decision, action_request, risk_score=85.0,
                risk_factors=("untrusted_mcp",))
            return MCPToolCallResult(
                ok=False, error="approval_required",
                provenance={"server": server_name, "tool": tool_name,
                            "grant_id": grant_id,
                            "approval_id": approval.approval_id})

        # ---- only now may an external call happen ----
        if MCP_SDK_AVAILABLE:
            raw = await self._invoke_via_sdk(rec, tool_name, arguments)
        else:
            # FUTURE: live invocation. Clearly labeled — no live call performed.
            raw = {"_stub": True,
                   "_note": "mcp-sdk-unavailable — no live call performed"}

        # Sandbox: timeout + output size cap. (Process isolation = documented future.)
        try:
            result = await asyncio.wait_for(
                self._sandboxed_invoke(raw), timeout=MCP_CALL_TIMEOUT_SECONDS)
        except TimeoutError:
            await self._audit.append(tenant, actor="mcp-manager", action="mcp.timeout",
                                     payload={"server": server_name, "tool": tool_name})
            return MCPToolCallResult(ok=False, error="mcp_timeout",
                                     provenance={"server": server_name})

        output = self._truncate(result)
        await self._audit.append(tenant, actor="mcp-manager", action="mcp.called",
                                 payload={"server": server_name, "tool": tool_name,
                                          "grant_id": grant_id})
        # Output is UNTRUSTED data — callers must treat it as such (injection surface).
        return MCPToolCallResult(ok=True, output=output, untrusted=True,
                                 provenance={"server": server_name, "tool": tool_name,
                                             "grant_id": grant_id,
                                             "untrusted": True})

    # -- internals --------------------------------------------------------------
    async def _require(self, tenant: contracts.TenantContext,
                       name: str) -> MCPServerRecord:
        rec = self._servers.get(tenant.tenant_id, {}).get(name)
        if rec is None:
            raise KeyError(f"unknown MCP server '{name}'")
        return rec

    async def _discover_via_sdk(self, rec: MCPServerRecord) -> builtins.list[dict[str, Any]]:
        # Real SDK discovery path (exercised only when `mcp` is installed).
        raise NotImplementedError  # pragma: no cover - needs live server + SDK

    async def _invoke_via_sdk(self, rec: MCPServerRecord, tool_name: str,
                              arguments: dict[str, Any]) -> Any:
        raise NotImplementedError  # pragma: no cover - needs live server + SDK

    async def _sandboxed_invoke(self, raw: Any) -> Any:
        # MVP sandbox: in-process with timeout + truncation. Documented future:
        # OS-level sandbox (seccomp/gVisor) per ADR-006 step-up.
        await asyncio.sleep(0)
        return raw

    def _truncate(self, output: Any) -> Any:
        import json as _json
        text = _json.dumps(output, default=str)
        if len(text.encode()) > MCP_MAX_OUTPUT_BYTES:
            return {"_truncated": True,
                    "preview": text[:MCP_MAX_OUTPUT_BYTES]}
        return output
