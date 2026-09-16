# ADR-003: Redis for cache/queues/rate-limits — never the system of record

**Status:** accepted · **Date:** 2026-09-15 · **Source:** `docs/research/stack-decision.md` §2.3/§5

## Context

The system needs ephemeral shared state (cache, locks, rate limits, job queue, refresh-token
denylist) without operating a second database. Postgres is the system of record (ADR-002).

## Decision

- **Redis 7** for: short-TTL registry/policy lookups, per-tenant token-bucket rate limits,
  refresh-token denylist, distributed locks (leader election/singletons), ARQ job-queue backend,
  Redis Streams as the documented event-bus step-up.
- **ARQ** (asyncio-native) for background jobs behind the `TaskQueue` interface. Celery is
  explicitly rejected (sync-first, heavy — ASSUMED judgment, recorded).
- **Valkey** documented as the license-safe drop-in: Redis moved to RSAL/SSPL in 2024
  (CONFIRMED — widely reported; exact legal reading UNKNOWN). **No Redis-proprietary modules**
  — the code must run on Valkey unmodified.

## Consequences

- **+** Tiny ops footprint; proven pattern from `agent-control-plane`.
- **−** Licensing noted for commercial use; mitigated by the Valkey constraint.

## Step-up trigger

Sustained event throughput where the in-process bus is insufficient → Redis Streams, then
NATS JetStream (ADR-007). Redis itself stays ephemeral-only.
