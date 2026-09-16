# support — tickets, SLA, escalation, knowledge base

**Status:** implemented (MVP). In-memory repository backs tests; Postgres
persistence is modeled (`models.py` + Alembic migration) and lands when the
core builder's `core/db` session factory exists.

## Boundary
Customer support: ticket lifecycle with SLA timers + breach detection,
conversation threading across channels, KB articles + FAQs, escalation rules
engine, assignment (manual + least-loaded auto-assign), macros, AI draft
replies via protocol.

## Owned tables
`tickets`, `ticket_messages`, `sla_policies`, `escalation_rules`,
`kb_articles`, `macros`.

## Public interfaces
- `SupportService` — ticket CRUD/lifecycle, `reply`, `transition` (guarded by
  `can_transition`), `assign`/`auto_assign`, `resolve`, SLA policy CRUD,
  `sla_status`, `scan_breaches` (emits `support.sla.breached` once per breach),
  escalation rule CRUD + `evaluate_escalations`, KB CRUD + `search_articles`,
  macro CRUD + `apply_macro`, `draft_reply`.
- `DraftAssistant` (protocol) — AI draft seam. Default
  `UnavailableDraftAssistant` raises `DraftAssistantUnavailable` honestly;
  the agents package provides the real implementation. No fake drafts, ever.

## Consumes
`core` (contracts only: `TenantContext`, `EventBus`). Emits:
`support.ticket.created/replied/transitioned/assigned/resolved/escalated`,
`support.sla.breached`.

## Ticket state machine (binding)
`open → pending → resolved → closed`, plus `open → resolved|closed`,
`pending → open`, `resolved → open`, `closed → open` (reopen). Everything
else is illegal (409 via `TicketStateError`).

## SLA notes
- The active policy matching the ticket's priority applies at creation.
- First-response clock stops on the first non-internal human/agent reply.
- `scan_breaches` is designed for a scheduled worker (ARQ); breach flags on
  the ticket guarantee exactly-once events.
- KB search is keyword/substring scoring at MVP; pgvector semantic search is
  the documented step-up (knowledge package, ADR-002).

## Rules for builders
1. Import other packages ONLY through `core/contracts.py` protocols or a
   package's public interface.
2. `tenant_id` on every row; every query filters by it.
3. Replies that send externally go through `comms/` (policy-gated) — this
   package never sends directly.
