# ADR-005: LiteLLM as in-process model gateway behind a ModelProvider interface

**Status:** accepted · **Date:** 2026-09-15 · **Source:** `docs/research/stack-decision.md` §2.5/§5

## Context

Multi-provider routing, fallbacks, and cost control are risk management, not fashion: a June
2026 provider outage took models offline for ~3 weeks (CONFIRMED from 2026 gateway research),
and 2026 surveys show 87% of AI engineers use multiple models (LIKELY). A separate gateway
process is unjustified for a single service.

## Decision

- **LiteLLM as an in-process library** (`Router` for fallbacks), behind our own `ModelProvider`
  adapter interface (`core/contracts.py`) — callers never touch LiteLLM directly.
- OpenRouter as an *optional* configured provider; Ollama for local dev.
- LiteLLM **Proxy / Envoy AI Gateway deferred** until a multi-service or multi-team gateway need exists.
- Measured LiteLLM overhead ~6.5ms (independent July 2026 harness — LIKELY) is negligible next
  to model latency.

## Consequences

- **+** One call signature across providers; built-in retries, fallbacks, spend tracking day one.
- **+** Provider swap or LiteLLM removal touches only the adapter.
- **−** LiteLLM release churn — pin in `uv.lock`.

## Step-up trigger

Multiple services/teams need one gateway → LiteLLM Proxy, then Envoy AI Gateway (CNCF v1.0,
June 2026 — CONFIRMED) as the K8s-native data plane.
