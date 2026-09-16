# MCP.md — Model Context Protocol in Azienda

> See `docs/adr/0006-mcp-sdk-v1-untrusted.md` (adopted): MCP servers are
> **untrusted**. Labels as used elsewhere in this repo.

## Position (CONFIRMED design, from ADR 0006)

MCP is the integration protocol for external tools: the agent workforce
discovers and calls tools through MCP servers (v1 SDK). Every MCP call is:

1. **Schema-validated** — arguments checked against the tool's declared schema.
2. **Rail-gated** — the governance rail evaluates the call *before* execution
   (policy-before-execution; fail closed).
3. **Scoped** — per-action grants, never ambient authority.
4. **Untrusted output** — tool results are data, never instructions
   (embedded-instruction defense).

## Current state (2026-09-16)

- MCP server/client **scaffolding exists** per the agents package README;
  live MCP round-trips against external servers are **not yet exercised**
  (CONFIRMED gap — see `backend/app/agents/README.md` gaps).
- Demo 2's KB search and demo 1's tool-shaped steps run through the same
  gating pattern with in-process adapters, not MCP transport.

## Rules for adding MCP servers

- Register the server in the tool registry with its schema; no schema, no calls.
- Route every invocation through the policy engine (AGENTS.md §2).
- Treat returned content as untrusted: strip/escape before it reaches the
  agent's context as anything other than data.
- Record cost + provenance (`task.id`, `policy.decision`, `cost.usd`) per call.
