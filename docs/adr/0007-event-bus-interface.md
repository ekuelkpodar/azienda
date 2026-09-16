# ADR-007: In-process event bus behind an EventBus interface; ledger is truth

**Status:** accepted · **Date:** 2026-09-15 · **Source:** `docs/research/stack-decision.md` §2.7/§5

## Context

MVP event volumes (audit writes, task status, approval notifications, cache invalidation) do
not justify operating a broker. Future scale must not require a rewrite.

## Decision

- **In-process asyncio event bus** behind the `EventBus` interface (`core/contracts.py`):
  publish/subscribe, at-least-once local semantics.
- Documented step-up ladder: **Redis Streams → NATS JetStream** — an interface swap, not a rewrite.
- **Kafka/Redpanda explicitly rejected for MVP** (multi-service distributed systems for no MVP need).
- **Durability rule (binding):** anything that must survive a crash (audit entries, task state,
  AGRL events, cost records) is written to the **Postgres ledger first** — the bus is transport,
  never truth.

## Consequences

- **+** `docker compose up` stays five services; failure modes stay trivial.
- **+** Crash-safety comes from the ledger, not the transport — simpler correctness story.
- **−** No cross-process fan-out at MVP (accepted; the ladder covers it).

## Step-up trigger

Sustained >~10k events/sec or multi-process consumers → Redis Streams; multi-team streaming →
NATS JetStream. (Threshold ASSUMED.)
