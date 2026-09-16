# ADR-006: MCP via official SDK v1.x; MCP servers are untrusted tools

**Status:** accepted · **Date:** 2026-09-15 · **Source:** `docs/research/stack-decision.md` §2.6/§5

## Context

MCP is the tool-calling standard (registry ~31k servers, Sept 2026 — LIKELY). SDK v2 is
pre-release with expected breaking changes; **v1.x is the only production line** (CONFIRMED
from SDK README notices), with a ~6-month security-fix window after v2 ships.

## Decision

- Pin `mcp>=1.27,<2`; **FastMCP** to expose Azienda tools as MCP servers; Streamable HTTP
  transport for remote, stdio for local.
- **Trust boundaries (binding):**
  1. MCP servers are *untrusted tool providers*. Every MCP invocation passes the governance
     rail (policy eval + scoped credentials) exactly like any other tool call.
  2. No MCP server ever receives ambient credentials — per-action scoped grants only.
  3. Tool schemas are validated; outputs are treated as **untrusted data** (prompt-injection
     surface → DLP/injection detectors).
  4. MCP OAuth identifies the *server*, not the user; user authorization is ours.

## Consequences

- **+** Standards-based tool ecosystem (incl. NEXORA/GHL/Zoho via MCP) without lock-in.
- **−** v2 migration is tracked tech debt; trust-boundary rules must be re-verified against
  v2's new primitives (discovery, long-running tasks) at migration time.

## Step-up trigger

MCP SDK v2 reaches stable → planned migration as a tracked work item, inside the security window.
