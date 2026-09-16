# ADR-001: Modular monolith on FastAPI + Python 3.12

**Status:** accepted · **Date:** 2026-09-15 · **Source:** `docs/research/stack-decision.md` §2.1/§5

## Context

The MVP must ship fast with clean extraction paths. The team has a proven FastAPI
modular-monolith pattern (`agent-control-plane` — CONFIRMED from workspace read), and every
AI library the system needs (LangGraph, LiteLLM, official MCP SDK) is Python-first (CONFIRMED).

## Decision

- Single deployable (API + worker processes), strict package boundaries:
  `backend/app/<domain>/` with public-interface-only imports.
- FastAPI ≥0.126, Python 3.12 via `uv`, **Pydantic v2 only** (FastAPI 0.126+ requires v2 —
  CONFIRMED from the FastAPI migration guide).
- Boundary enforcement: import-lint in CI; violations fail the build.

## Consequences

- **+** Fastest path to a working MVP; whole Python AI ecosystem available.
- **+** Stateless API tier scales horizontally.
- **−** GIL-bound CPU work must move to the ARQ worker, never the request path.
- **−** Service extraction later requires no rewrite *only if* boundaries hold — hence the lint gate.

## Step-up trigger

A package shows sustained independent scaling or team-ownership pressure AND its interface is
stable — then extract that one package as a service. Not before.
