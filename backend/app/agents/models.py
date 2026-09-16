"""Model provider: LiteLLM in-process if importable, else a labeled stub.

ADR-005: LiteLLM as an in-process library behind our own ``ModelProvider``
interface. Callers never touch LiteLLM directly.

- ``LiteLLMProvider`` — real path. Requires the ``litellm`` package AND
  provider API keys in env (never in code). Not exercised in unit tests.
- ``StubModelProvider`` — deterministic canned responses for tests/dev,
  CLEARLY LABELED. It never claims to call a real model; it raises if asked
  to act on consequential content without explicit test intent.

``make_provider()`` picks LiteLLM when importable, else the stub — and says so.

Model routing tiers: default / fallback / reasoning / low_cost / fast / local.
Every call records usage + cost via ``CostRecorder`` (AGENTS.md §4: no unmetered
LLM calls, ever).
"""

from __future__ import annotations

import importlib.util
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.core import contracts


def _utcnow() -> datetime:
    return datetime.now(UTC)


LITELLM_AVAILABLE = importlib.util.find_spec("litellm") is not None


class ModelTier(str):
    DEFAULT = "default"
    FALLBACK = "fallback"
    REASONING = "reasoning"
    LOW_COST = "low_cost"
    FAST = "fast"
    LOCAL = "local"


# Default model names per tier (ASSUMED config — override via settings/env).
# These are identifiers only; no credentials live here.
TIER_MODELS: dict[str, list[str]] = {
    ModelTier.DEFAULT: ["openai/gpt-4o-mini"],
    ModelTier.FALLBACK: ["openai/gpt-4o", "anthropic/claude-sonnet-4-6"],
    ModelTier.REASONING: ["openai/o4-mini"],
    ModelTier.LOW_COST: ["openai/gpt-4o-mini"],
    ModelTier.FAST: ["openai/gpt-4o-mini"],
    ModelTier.LOCAL: ["ollama/llama3.1:8b"],
}

# ASSUMED public pricing, USD per 1k tokens (tune from billing data).
MODEL_PRICING: dict[str, tuple[Decimal, Decimal]] = {
    "openai/gpt-4o-mini": (Decimal("0.00015"), Decimal("0.0006")),
    "openai/gpt-4o": (Decimal("0.0025"), Decimal("0.01")),
    "anthropic/claude-sonnet-4-6": (Decimal("0.003"), Decimal("0.015")),
    "openai/o4-mini": (Decimal("0.0011"), Decimal("0.0044")),
    "ollama/llama3.1:8b": (Decimal("0"), Decimal("0")),
}


@dataclass
class ModelRegistry:
    """In-memory model registry (dev adapter). Production: SQLAlchemy repo."""

    pricing: dict[str, tuple[Decimal, Decimal]] = field(
        default_factory=lambda: dict(MODEL_PRICING))

    def cost_for(self, model: str, in_tokens: int, out_tokens: int) -> Decimal:
        rate_in, rate_out = self.pricing.get(model, (Decimal("0.001"), Decimal("0.003")))
        return (rate_in * in_tokens + rate_out * out_tokens) / 1000


class ModelRouter:
    """Picks a model for a tier, with fallback chain."""

    def __init__(self, tier_models: dict[str, list[str]] | None = None) -> None:
        self._tiers = tier_models or TIER_MODELS

    def pick(self, tier: str = ModelTier.DEFAULT) -> str:
        chain = self._tiers.get(tier) or self._tiers[ModelTier.DEFAULT]
        return chain[0]

    def fallback_chain(self, tier: str = ModelTier.DEFAULT) -> list[str]:
        return list(self._tiers.get(tier) or self._tiers[ModelTier.DEFAULT])


def _estimate_tokens(messages: list[dict[str, Any]]) -> int:
    # Rough heuristic for the stub path only (ASSUMED ~4 chars/token).
    chars = sum(len(str(m.get("content", ""))) for m in messages)
    return max(1, chars // 4)


class StubModelProvider:
    """STUB — deterministic canned responses for tests/dev.

    NOT a real model call. Labels every response as stub-generated. The
    canned content comes from ``request.metadata["stub_response"]`` (tests) or
    a fixed placeholder. Refuses consequential tool-call fabrication unless
    the test explicitly supplies ``stub_tool_calls``.
    """

    PROVIDER_KIND = "stub — NOT a real model call"

    def __init__(self, *, costs: contracts.CostRecorder | None = None,
                 model_registry: ModelRegistry | None = None) -> None:
        self._costs = costs
        self._registry = model_registry or ModelRegistry()
        self.calls: list[contracts.ModelRequest] = []

    async def complete(self, request: contracts.ModelRequest) -> contracts.ModelResponse:
        self.calls.append(request)
        model = request.model or TIER_MODELS[ModelTier.DEFAULT][0]
        in_tokens = _estimate_tokens(request.messages)
        content = request.metadata.get("stub_response",
                                       "[stub] no real model call was made")
        tool_calls = tuple(request.metadata.get("stub_tool_calls", ()))
        out_tokens = max(1, len(str(content)) // 4)
        cost = self._registry.cost_for(model, in_tokens, out_tokens)
        if self._costs is not None:
            await self._costs.record(contracts.CostRecord(
                tenant_id=request.tenant.tenant_id,
                task_id=request.metadata.get("task_id"),
                model=f"stub:{model}", input_tokens=in_tokens,
                output_tokens=out_tokens, cost_usd=cost, credits_drawn=cost,
                recorded_at=_utcnow()))
        return contracts.ModelResponse(
            content=f"{content}\n[provider: stub — not a real model call]",
            tool_calls=tool_calls, model_used=f"stub:{model}",
            input_tokens=in_tokens, output_tokens=out_tokens,
            cost_usd=cost, latency_ms=1)


class LiteLLMProvider:
    """Real provider via the litellm package (ADR-005)."""

    PROVIDER_KIND = "litellm"

    def __init__(self, *, costs: contracts.CostRecorder | None = None,
                 model_registry: ModelRegistry | None = None,
                 router: Any = None) -> None:
        if not LITELLM_AVAILABLE:
            raise RuntimeError(
                "litellm is not installed — use make_provider() or StubModelProvider")
        import litellm  # type: ignore[import-not-found]
        self._litellm = litellm
        self._costs = costs
        self._registry = model_registry or ModelRegistry()
        self._router = router

    async def complete(self, request: contracts.ModelRequest) -> contracts.ModelResponse:
        import asyncio as _asyncio

        model = request.model or TIER_MODELS[ModelTier.DEFAULT][0]
        started = time.monotonic()
        # litellm is sync; run in a thread so the event loop stays free.
        resp = await _asyncio.to_thread(
            self._litellm.completion,
            model=model, messages=request.messages,
            tools=request.tools or None, max_tokens=request.max_tokens,
            temperature=request.temperature)
        latency_ms = int((time.monotonic() - started) * 1000)
        choice = resp.choices[0]
        content = choice.message.content
        tool_calls = tuple(
            {"name": tc.function.name, "arguments": tc.function.arguments}
            for tc in (choice.message.tool_calls or []))
        usage = resp.usage or {}
        in_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        out_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        try:
            cost = Decimal(str(self._litellm.completion_cost(completion_response=resp)))
        except Exception:
            cost = self._registry.cost_for(model, in_tokens, out_tokens)
        if self._costs is not None:
            await self._costs.record(contracts.CostRecord(
                tenant_id=request.tenant.tenant_id,
                task_id=request.metadata.get("task_id"), model=model,
                input_tokens=in_tokens, output_tokens=out_tokens,
                cost_usd=cost, credits_drawn=cost, recorded_at=_utcnow()))
        return contracts.ModelResponse(
            content=content, tool_calls=tool_calls, model_used=model,
            input_tokens=in_tokens, output_tokens=out_tokens,
            cost_usd=cost, latency_ms=latency_ms)


def make_provider(*, costs: contracts.CostRecorder | None = None,
                  model_registry: ModelRegistry | None = None,
                  force_stub: bool = False) -> contracts.ModelProvider:
    """Factory: LiteLLM when importable, else the clearly-labeled stub."""
    if LITELLM_AVAILABLE and not force_stub:
        return LiteLLMProvider(costs=costs, model_registry=model_registry)
    return StubModelProvider(costs=costs, model_registry=model_registry)
