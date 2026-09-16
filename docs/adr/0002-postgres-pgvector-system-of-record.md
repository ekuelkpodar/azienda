# ADR-002: PostgreSQL (+ pgvector) as the single system of record

**Status:** accepted · **Date:** 2026-09-15 · **Source:** `docs/research/stack-decision.md` §2.3/§5

## Context

Ledgers, audit hash chains, tenant data, embeddings, and full-text search all need one
transactional home. Both workspace repos (`agent-control-plane`, `nexora-erp`) independently
chose Postgres (CONFIRMED).

## Decision

- PostgreSQL 16/17 + **pgvector ≥0.8.2** (HNSW; 0.8.2 fixed a parallel-HNSW CVE — CONFIRMED
  from pgvector release notes) for relational + JSONB + vector + full-text.
- SQLAlchemy 2.0 async; Alembic migrations.
- **No dedicated vector DB or graph DB at MVP.** Retrieval goes behind interfaces
  (`KnowledgeStore`); entity/edge tables in Postgres cover MVP GraphRAG.
- Tenant-filtered hybrid search runs in **one query** (tenant predicate before ANN ranking).

## Consequences

- **+** One backup story, one transaction model, one engine to operate.
- **+** Audit/AGRL hash chains get real ACID append semantics.
- **−** ANN scale ceiling (~millions of vectors) accepted and monitored.

## Step-up triggers

- Dedicated vector DB: >~1–10M vectors or ANN QPS beyond a single Postgres (ASSUMED threshold).
- Graph server: multi-hop traversal latency proves Postgres-bound → embedded **Kuzu** first
  (zero-ops), still not a server, behind a `GraphStore` interface.
