"""Agent router: capability × authorization × cost × latency × reliability ×
context × risk.

Deliberately NOT model-quality-only: model benchmark quality is not an input
at all. Authorization is a hard gate (unauthorized candidates are excluded,
never merely down-ranked). Weights are constants below — ASSUMED, tune with
eval data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core import contracts

# Scoring weights (ASSUMED defaults; eval harness tunes them).
W_CAPABILITY = 0.35
W_COST = 0.15
W_LATENCY = 0.10
W_RELIABILITY = 0.15
W_CONTEXT = 0.10
W_RISK = 0.15


@dataclass
class RouteRequest:
    capability: str                       # required capability, e.g. "lead.research"
    required_permissions: tuple[str, ...] = ()
    required_tools: tuple[str, ...] = ()
    context_tokens: int = 0               # estimated context size needed
    max_cost_usd: float | None = None
    max_autonomy: int = 5                 # caller may cap autonomy for the task
    exclude_agent_ids: tuple[str, ...] = ()


@dataclass
class ScoredAgent:
    agent: contracts.AgentDefinition
    total: float
    breakdown: dict[str, float] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()


class NoRouteAvailable(Exception):
    """Raised when no authorized, capable agent exists — caller must escalate."""


class AgentRouter:
    """Scores candidate agents from the registry. Authorization gates; the rest ranks."""

    def __init__(self, *, registry: Any,
                 reliability: dict[str, float] | None = None) -> None:
        """
        registry: AgentRegistry (duck-typed to avoid import cycles).
        reliability: agent_id -> observed success rate 0..1 (from eval history;
                     defaults to 0.8 ASSUMED when unknown).
        """
        self._registry = registry
        self._reliability = reliability or {}

    async def score(self, tenant: contracts.TenantContext,
                    request: RouteRequest) -> list[ScoredAgent]:
        candidates = await self._registry.list(tenant)
        scored: list[ScoredAgent] = []
        for rec in candidates:
            if rec.agent_id in request.exclude_agent_ids:
                continue
            agent = rec.to_contract()
            verdict = self._authorize(rec, request)
            if not verdict[0]:
                continue  # hard gate: unauthorized agents never appear
            breakdown, reasons = self._rank(rec, request)
            total = (W_CAPABILITY * breakdown["capability"]
                     + W_COST * breakdown["cost"]
                     + W_LATENCY * breakdown["latency"]
                     + W_RELIABILITY * breakdown["reliability"]
                     + W_CONTEXT * breakdown["context"]
                     + W_RISK * breakdown["risk"])
            scored.append(ScoredAgent(agent=agent, total=round(total, 4),
                                     breakdown={k: round(v, 4)
                                                for k, v in breakdown.items()},
                                     reasons=tuple(reasons)))
        scored.sort(key=lambda s: s.total, reverse=True)
        return scored

    async def route(self, tenant: contracts.TenantContext,
                    request: RouteRequest) -> contracts.AgentDefinition:
        ranked = await self.score(tenant, request)
        if not ranked:
            raise NoRouteAvailable(
                f"no authorized agent with capability '{request.capability}'")
        return ranked[0].agent

    # -- internals ------------------------------------------------------------
    def _authorize(self, rec: Any, request: RouteRequest) -> tuple[bool, str]:
        """Hard gates. Returns (allowed, reason)."""
        if rec.status.value != "active":
            return False, f"agent {rec.agent_id} is {rec.status.value}"
        if request.capability not in rec.capabilities:
            return False, "missing capability"
        missing_tools = [t for t in request.required_tools
                         if t not in rec.allowed_tools]
        if missing_tools:
            return False, f"tools not granted: {missing_tools}"
        missing_perms = [p for p in request.required_permissions
                         if p not in rec.permissions]
        if missing_perms:
            return False, f"permissions not granted: {missing_perms}"
        if rec.autonomy_level > request.max_autonomy:
            return False, (f"autonomy L{rec.autonomy_level} exceeds "
                           f"task cap L{request.max_autonomy}")
        return True, "authorized"

    def _rank(self, rec: Any, request: RouteRequest) -> tuple[dict[str, float], list[str]]:
        reasons: list[str] = []
        # capability: exact match is 1.0 by construction here; related caps add a nudge
        capability = 1.0
        reasons.append(f"capability '{request.capability}' exact")

        # cost: cheaper declared operating cost scores higher (ASSUMED table)
        cost_per_task = _ASSUMED_COST.get(rec.agent_id, 0.05)
        cost = max(0.0, 1.0 - (cost_per_task / 0.50))
        if request.max_cost_usd is not None and cost_per_task > request.max_cost_usd:
            cost *= 0.25
            reasons.append(f"over cost cap ${request.max_cost_usd}")
        reasons.append(f"assumed cost/task ${cost_per_task:.3f}")

        # latency: declared p50 (ASSUMED)
        p50_ms = _ASSUMED_LATENCY_MS.get(rec.agent_id, 4000)
        latency = max(0.0, 1.0 - (p50_ms / 30000))
        reasons.append(f"assumed p50 {p50_ms}ms")

        # reliability: observed success rate, default 0.8 ASSUMED
        reliability = self._reliability.get(rec.agent_id, 0.8)
        reasons.append(f"reliability {reliability:.2f}")

        # context: does the agent's context window fit the need (ASSUMED sizes)
        window = _ASSUMED_CONTEXT_TOKENS.get(rec.agent_id, 128_000)
        need = max(request.context_tokens, 1)
        context = 1.0 if window >= need * 4 else max(0.0, window / (need * 4))
        reasons.append(f"context window {window} vs need {need}")

        # risk: lower autonomy relative to task risk scores higher; read-only safer
        read_only = all(t.split(".")[0] in ("knowledge", "memory")
                        for t in rec.allowed_tools) if rec.allowed_tools else False
        risk = 1.0 - (rec.autonomy_level / 5.0) * 0.5 + (0.25 if read_only else 0.0)
        risk = max(0.0, min(1.0, risk))
        reasons.append(f"autonomy L{rec.autonomy_level}" + (" read-only" if read_only else ""))

        return ({"capability": capability, "cost": cost, "latency": latency,
                 "reliability": reliability, "context": context, "risk": risk},
                reasons)


_ASSUMED_COST: dict[str, float] = {
    "executive-assistant": 0.08, "sales-agent": 0.10, "marketing-agent": 0.10,
    "support-agent": 0.07, "operations-agent": 0.06, "research-agent": 0.12,
    "scheduling-agent": 0.05, "finance-assistant": 0.09, "crm-agent": 0.06,
    "analytics-agent": 0.11,
}
_ASSUMED_LATENCY_MS: dict[str, int] = {
    "executive-assistant": 5000, "sales-agent": 6000, "marketing-agent": 6000,
    "support-agent": 4000, "operations-agent": 4000, "research-agent": 9000,
    "scheduling-agent": 3000, "finance-assistant": 5000, "crm-agent": 4000,
    "analytics-agent": 8000,
}
_ASSUMED_CONTEXT_TOKENS: dict[str, int] = {
    "executive-assistant": 128_000, "sales-agent": 128_000, "marketing-agent": 128_000,
    "support-agent": 128_000, "operations-agent": 128_000, "research-agent": 200_000,
    "scheduling-agent": 128_000, "finance-assistant": 128_000, "crm-agent": 128_000,
    "analytics-agent": 200_000,
}
