# GOVERNANCE.md — Azienda governance rail

> The governance rail is a cross-cutting layer (see `docs/adr/0008-jwt-rbac-tenant-rls.md`
> and `ARCHITECTURE.md`). Labels: CONFIRMED = verified 2026-09-16; otherwise as stated.

## The rule

Every action that is irreversible, financial, bulk, or externally visible MUST
pass through the governance rail before execution. The rail decides one of:
`ALLOW`, `DENY`, `REQUIRE_APPROVAL`, `LOG_AND_NOTIFY`. On doubt it denies and
escalates (fail closed).

## Components (CONFIRMED implemented, tests green)

| Component | Package | What it does |
|---|---|---|
| Policy engine | `backend/app/governance/policy/` | Evaluates rules against action + risk context. MVP: limited rule engine (CONFIRMED gap: full OPA/Rego not implemented); refuses production use via fail-closed guard. |
| Approvals | `backend/app/governance/approvals/` | Human-approval workflow; request → pending → approve/deny; **timeout == DENY** (expired approvals deny by default). |
| Audit ledger | `backend/app/governance/audit/` | Hash-chained, append-only records; `verify_chain` checks tampering. |
| Budgets | `backend/app/governance/budgets/` | Per-tenant budgets; `reserve` before expensive work; **kill switch** freezes agent spend. Budgets are a P0 test target (AGENTS.md §3). |
| Action grants | `backend/app/agents/` (scoped grants) | Agents/tools act with per-action scoped grants — never ambient authority. |

## Progressive autonomy (design intent — ASSUMED until policy rules land)

- **L0–L2:** agent suggests, human approves (all externally-visible actions today).
- **L3+:** policy-gated autonomous execution within budget caps and risk bands.

The escalation path is exercised in Demo 2: a refund request over the KB-stated
$500 manager-approval threshold is routed to the human queue instead of being
executed.

## Separation of powers

- The **policy engine decides**; the **approval queue** holds the human decision;
  the **audit ledger remembers**. No component can overrule another silently —
  every override is itself an audited action.
