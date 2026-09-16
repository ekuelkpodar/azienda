# marketing — campaign orchestration and measurement

**Status:** implemented (MVP). In-memory repository backs tests; Postgres
persistence is modeled (`models.py` + Alembic migration) and lands when the
core builder's `core/db` session factory exists.

## Boundary
Campaigns and audiences: campaign definitions, journey steps, rule-based
audience segments, content assets, launch orchestration, send log, analytics
rollups. Raw message transport is `comms/`; marketing sends through its local
`CommsSenderPort` protocol via dependency injection (structural twin of the
comms send capability — marketing imports nothing from `comms/`).

## Owned tables
`campaigns`, `campaign_steps`, `audiences`, `audience_members`,
`content_assets`, `campaign_sends`.

## Public interfaces
- `MarketingService` — campaign/step/audience/asset CRUD, `launch_campaign`
  (policy-gated, bulk-aware), `campaign_analytics`.
- `evaluate_filter` / `segment_contacts` (`segmentation.py`) — pure rule-based
  segmentation over caller-supplied contact dicts. Operators: eq/ne/gt/gte/
  lt/lte/in/not_in/contains/starts_with/ends_with/is_set/is_empty; combinators
  all/any/not; dotted field paths.
- `render_campaign_content` (`templates.py`) — Jinja-sandboxed rendering
  (`SandboxedEnvironment` + `StrictUndefined`).

## Consumes
`core` (contracts) only — the comms send capability is consumed through the
locally defined `CommsSenderPort` structural protocol (injected, never
imported). `governance` policy engine via `core/contracts.py` `PolicyEngine`.
Emits: `marketing.campaign.launched`, `marketing.campaign.send_completed`,
`marketing.campaign.approval_requested`.

## Launch policy (binding)
`launch_campaign` evaluates `marketing.campaign.launch` with bulk risk context
(`audience_size`, `bulk` flag at the `bulk_approval_threshold`, default 100 —
ASSUMED default until tenant policy data exists). DENY → 403; REQUIRE_APPROVAL
→ campaign parked in `awaiting_approval`; ALLOW → sends proceed. Fail closed:
no policy engine configured = refuse to send.

## Attribution
**Documented stub.** `attribution_report` raises
`AttributionNotImplementedError` (router: 501) rather than inventing numbers.
Real attribution needs touchpoint tracking + CRM opportunity linkage — neither
exists yet. Build-out plan: record touchpoints in `campaign_sends` (done) →
join to CRM opportunities on contact identity (needs `crm/`) → multi-touch
model in a follow-up ADR.

## AI hooks
NOT implemented here. Content generation and send-time optimization belong to
the agents package, consumed via protocols (per mandate §6).

## Rules for builders
1. Import other packages ONLY through `core/contracts.py` protocols or a
   package's public interface.
2. `tenant_id` on every row; every query filters by it.
3. Bulk/externally-visible sends go through the governance rail BEFORE
   execution. Fail closed.
