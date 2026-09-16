# Azienda — API Contract

**Status:** normative skeleton for builders. **Date:** 2026-09-15.
OpenAPI is generated later from FastAPI; this document is the contract the routers implement.
Base path: **`/api/v1`**. All endpoints are tenant-scoped unless marked Platform.

## 1. Conventions

### Auth
- Humans: `Authorization: Bearer <access-jwt>` (5–15 min) + rotating refresh in HttpOnly
  Secure cookie. Login: `POST /auth/login` (email+password or OIDC — OIDC interface reserved).
- Agents/services: API keys with tenant scope + per-action grants; never ambient authority.
- Tenant is resolved from token claims. A client-supplied tenant id is ignored; cross-tenant
  access returns 403. Platform-admin endpoints (`/platform/*`, not in MVP scope) require a
  platform role.

### Error envelope (all errors)
```json
{ "error": { "code": "policy_denied", "message": "human-readable",
             "details": {}, "trace_id": "01J..." } }
```
`code` is a stable snake_case string (builders: define the enum in `core/errors.py`).

### Pagination
Cursor-based: `GET /x?page_size=50&page_token=...` →
`{ "items": [...], "next_page_token": "..." | null }`. `page_size` max 200, default 50.

### Idempotency
All mutating POSTs accept `Idempotency-Key: <uuid>`; replays within 24h return the original
response. Required for: task creation, workflow start, sends, billing mutations, approvals.

### Common status codes
`400` validation · `401` unauthenticated · `403` forbidden/cross-tenant · `404` not found ·
`409` conflict (state transition illegal, duplicate idempotency with different payload) ·
`422` schema validation · `429` rate limited · `451`-style `policy_denied` is a **200 with
`effect: deny`** on evaluate endpoints, but a **403** with code `policy_denied` when an action
was attempted and denied.

## 2. Auth — `/auth`
| Method | Path | Purpose |
|---|---|---|
| POST | `/auth/login` | email+password → access JWT + refresh cookie |
| POST | `/auth/refresh` | rotate refresh → new pair (reuse detection → revoke all) |
| POST | `/auth/logout` | revoke refresh (denylist) |
| GET | `/auth/me` | current user + tenant + roles |
| POST | `/auth/api-keys` | create scoped service key (RBAC: admin) |
| DELETE | `/auth/api-keys/{id}` | revoke key |

## 3. Tenants & users — `/tenants`
| Method | Path | Purpose |
|---|---|---|
| GET | `/tenants/me` | current tenant profile + settings |
| PATCH | `/tenants/me` | update tenant settings (RBAC: admin) |
| GET | `/tenants/me/users` | list users |
| POST | `/tenants/me/users` | invite user (role assignment) |
| PATCH | `/tenants/me/users/{id}` | change role / deactivate |
| GET | `/tenants/me/roles` | role definitions |

## 4. CRM — `/crm`
Orgs, contacts, leads, opportunities, pipelines, activities.
| Method | Path | Purpose |
|---|---|---|
| GET/POST | `/crm/organizations` | list / create |
| GET/PATCH/DELETE | `/crm/organizations/{id}` | read / update / archive |
| GET/POST | `/crm/contacts` | list (filter: org, tag, search) / create |
| GET/PATCH | `/crm/contacts/{id}` | read / update |
| GET/POST | `/crm/leads` | list (filter: status, owner) / create |
| POST | `/crm/leads/{id}/convert` | lead → org+contact+opportunity (policy-gated if bulk) |
| GET/POST | `/crm/opportunities` | list (filter: pipeline, stage) / create |
| POST | `/crm/opportunities/{id}/move` | stage transition (forward/backward validated) |
| GET/POST | `/crm/pipelines` | list / create pipeline + stages |
| GET/POST | `/crm/activities` | list (filter: subject type/id) / log activity |

## 5. Tasks — `/tasks`
State machine: `pending → planning → waiting_approval → executing → blocked → completed|failed`
(+ `cancelled` from any non-terminal).
| Method | Path | Purpose |
|---|---|---|
| GET/POST | `/tasks` | list (filter: status, assignee, project) / create (idempotent) |
| GET/PATCH | `/tasks/{id}` | read (incl. plan, cost-so-far) / update fields |
| POST | `/tasks/{id}/transition` | `{to: <state>, note}` — guarded by `can_transition` |
| POST | `/tasks/{id}/assign` | assign to user or agent |
| GET/POST | `/tasks/{id}/comments` | list / add |
| POST | `/tasks/{id}/delegate` | hand to agent workforce → creates agent run (policy-gated) |
| GET | `/tasks/{id}/transitions` | transition history |
| GET | `/tasks/{id}/outcome` | outcome record: plan, decisions, tool calls, retries, cost |

## 6. Workflows — `/workflows`
| Method | Path | Purpose |
|---|---|---|
| GET/POST | `/workflows/definitions` | list / create draft |
| POST | `/workflows/definitions/{id}/versions` | publish new version (immutable once published) |
| GET | `/workflows/definitions/{id}/versions/{v}` | read version |
| POST | `/workflows/executions` | start execution (idempotent; body: definition, version, input) |
| GET | `/workflows/executions/{id}` | state + current step + cost |
| POST | `/workflows/executions/{id}/signal` | send signal (incl. approval decisions for HITL steps) |
| POST | `/workflows/executions/{id}/cancel` | cancel with reason |
| GET | `/workflows/executions/{id}/events` | execution event stream (SSE optional) |

## 7. Agents — `/agents`
| Method | Path | Purpose |
|---|---|---|
| GET | `/agents` | registry list (filter: capability, status) |
| POST | `/agents` | register new agent (RBAC: admin; version 1 created) |
| GET | `/agents/{id}` | definition + versions |
| POST | `/agents/{id}/versions` | publish new version (autonomy level, tools, model prefs) |
| POST | `/agents/{id}/pause` · `/agents/{id}/resume` | lifecycle control |
| GET | `/agents/runs/{run_id}` | run detail: steps, tool calls, policy decisions, cost |
| POST | `/agents/runs/{run_id}/cancel` | cancel a run |

## 8. Tools — `/tools`
| Method | Path | Purpose |
|---|---|---|
| GET | `/tools` | registry list (filter: capability, risk_tier) |
| POST | `/tools` | register tool incl. MCP server tools (RBAC: admin; vetting required) |
| GET | `/tools/{name}` | spec: schema, risk tier, idempotency |
| POST | `/tools/{name}/invoke` | invoke (policy-gated; returns 403 code `policy_denied` if denied) |
| GET | `/tools/calls` | invocation log (filter: task, agent, tool) |

## 9. Approvals — `/approvals`
| Method | Path | Purpose |
|---|---|---|
| GET | `/approvals` | list pending (filter: requester, action) — the human queue |
| GET | `/approvals/{id}` | detail: action, args, risk score, policy reasons |
| POST | `/approvals/{id}/decide` | `{approved: bool, note}` — timeout elsewhere → DENY |
| POST | `/approvals/{id}/escalate` | route to another approver |

## 10. Budgets — `/budgets`
| Method | Path | Purpose |
|---|---|---|
| GET | `/budgets` | tenant budgets + current period burn |
| POST | `/budgets` | create/update budget (credits, period, alerts) |
| GET | `/budgets/{id}/ledger` | cost ledger entries (filter: task, model, date) |
| POST | `/budgets/kill-switch` | freeze new agent spend immediately (RBAC: admin; audited) |
| POST | `/budgets/kill-switch/release` | release freeze (RBAC: admin; audited) |
| GET | `/budgets/alerts` | spend alerts (50/80/95% default thresholds) |

## 11. Audit — `/audit`
| Method | Path | Purpose |
|---|---|---|
| GET | `/audit/entries` | append-only entries (filter: actor, action, date) — immutable |
| GET | `/audit/entries/{seq}` | single entry with hash chain links |
| POST | `/audit/verify` | verify chain integrity from seq → report |

## 12. Command Center — `/command-center`
Aggregates for the ops dashboard. Read-only; all values tenant-scoped.
| Method | Path | Purpose |
|---|---|---|
| GET | `/command-center/summary` | KPIs: active goals, running tasks, pending approvals, burn today, outcome counts |
| GET | `/command-center/goals` | goal states from AGRL projections (filter: status) |
| GET | `/command-center/outcomes` | outcome ledger (filter: type, date) with fully-loaded cost |
| GET | `/command-center/costs` | cost breakdown by model/agent/workflow/outcome |
| GET | `/command-center/policy-activity` | recent policy decisions (allow/deny/approval rates) |
| GET | `/command-center/attention` | unified attention queue: failures, pending approvals, budget alerts, anomalies |
| POST | `/command-center/intent` | parse-only natural-language intent → structured command (no execution) |

## 13. Billing (own SaaS) — `/billing`
| Method | Path | Purpose |
|---|---|---|
| GET | `/billing/plans` | available plans + dimensions (pricing is data) |
| GET | `/billing/subscription` | current subscription + credit pool balance |
| POST | `/billing/subscription` | change plan (idempotent) |
| GET | `/billing/usage` | metered usage: credits drawn, comms pass-through, storage |
| GET | `/billing/invoices` | invoices + line items |
| POST | `/billing/spend-caps` | set/adjust customer spend cap (overage requires explicit cap) |

## 14. Events / streaming
- `GET /events/stream` — SSE: task transitions, approval requests, budget alerts, workflow events.
  Auth via Bearer; heartbeat 15s; replay via `Last-Event-ID`.
- Webhooks (outbound, tenant-configured): `task.completed`, `approval.requested`,
  `billing.credit.low`, `billing.spend_cap.hit` — signed with per-tenant secret, retries with
  backoff, delivery log.

## 15. Versioning
URL versioning (`/api/v1`). Breaking changes → `/api/v2`; v1 supported ≥12 months after v2 GA.
Schemas are additive-compatible within a major version.
