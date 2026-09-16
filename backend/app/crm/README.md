# crm

**Status:** implemented (service, models, schemas, scoring, router, migration, tests).

## Boundary

Customer relationship data and sales pipeline: organizations, contacts, leads,
opportunities, pipelines/stages, activities, custom fields, relationships.
Business-application layer. Every row is tenant-scoped; every consequential
write is policy-gated before execution and emits a domain event after.

## Owned tables (CONFIRMED — in migration `0004_crm_tasks_workflows`)

`organizations`, `contacts`, `leads`, `pipelines`, `pipeline_stages`,
`opportunities`, `activities`, `custom_field_definitions`, `crm_relationships`.

## Public interfaces

- `CRMService` (`app/crm/service.py`): org/contact/lead/opportunity/pipeline
  CRUD, lead scoring (`score_lead`), lead conversion (lead → org + contact +
  opportunity), opportunity stage moves with terminal-stage guard, activity
  timeline, custom-field definitions with JSON validation, CRM relationships,
  ownership, tags.
- `app/crm/scoring.py`: pure, deterministic, rule-based lead scoring
  (`score_lead(LeadFacts) -> (score, breakdown)`). Transparent by design: every
  point is attributed to a named rule. No ML model, no external calls
  (CONFIRMED).
- `app/crm/schemas.py`: Pydantic v2 request/response schemas.
- Router: `app/api/routers/crm.py` (prefix `/api/v1/crm`).
- Domain events (via injected `EventBus`): `crm.organization.created`,
  `crm.contact.created`, `crm.lead.created`, `crm.lead.scored`,
  `crm.lead.converted`, `crm.opportunity.stage_changed`, `crm.activity.logged`,
  `crm.custom_field.created`, … (see service `_emit` calls — CONFIRMED).

## Key behaviors (CONFIRMED by `tests/test_crm.py`, 20 tests)

- **Tenant isolation:** every query filters `tenant_id`; cross-tenant reads
  raise `CRMNotFound` / HTTP 404. Covered at service and HTTP level.
- **Policy-before-write:** `create/update/convert/score/move` evaluate the
  injected `PolicyEngine` first; denial raises `PolicyDeniedError` (HTTP 403
  `policy_denied`) before any DB write.
- **Idempotency:** all mutating HTTP routes require `Idempotency-Key`
  (missing → 422 `missing_idempotency_key`); same key + same body replays the
  stored response; same key + different body → 409 `idempotency_conflict`.
- **Dedupe:** contacts dedupe on email (case-insensitive) and phone
  (exact match).
- **Lead conversion** creates org + contact + opportunity atomically and emits
  `crm.lead.converted`.
- **Opportunity stage moves** validate against the pipeline's stage order;
  terminal stages reject backward moves.
- **Custom fields:** definitions validated per `field_type` (enum requires
  options); values validated against definitions.
- **Pagination:** cursor-based (`page_token`); standard error envelope
  `{error: {code, message, details, trace_id}}` on all failures.

## Consumes

`core/contracts.py` protocols only (`TenantContext`, `PolicyEngine`,
`EventBus`, `ApprovalStore`). Never imports sibling packages (CONFIRMED —
`app/crm` imports only `app.core.*` plus stdlib/third-party).

## Gaps / honest notes

- **Phone dedupe is exact-match** — no normalization (`+1-555-0100` vs
  `+1 (555) 0100` are treated as different). ASSUMED acceptable for MVP;
  normalize (E.164) before prod.
- Concurrency: two simultaneous creates with the same email can both pass the
  dedupe check; the DB unique constraint is the backstop (LIKELY — unique
  indexes exist; race behavior untested).
- Email/phone verification, bounce handling, and marketing automation are not
  in this package (ASSUMED owned by `comms/` / `marketing/`; confirm with
  those builders).
