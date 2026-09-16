# Azienda Console (frontend)

**Status:** functional SPA against the documented API contracts. Backend routers are
being implemented in parallel against the same `/API.md` — where an endpoint is not
live yet, pages render honest loading / error / empty states. No fake data, ever.

- React 19 + Vite + TypeScript (strict, no `any` in new code) + Tailwind CSS v4.
- Served **same-origin** by the FastAPI backend (`/api/v1` + SPA fallback) — see root `Dockerfile`.
- `src/api/client.ts` — typed client for every API.md §2–15 endpoint: Bearer access
  token (sessionStorage) + HttpOnly refresh cookie, transparent 401→refresh→retry,
  `Idempotency-Key` on mutating POSTs, error-envelope parsing.
- `src/api/hooks.ts` — `useApi` / `useMutation` data hooks.
- `src/components/ui.tsx` — shared library: buttons, inputs, modal, table, tabs,
  toasts, badges, loading/error/empty states, `PlannedBadge` (visibly labeled,
  disabled, with tooltip — the no-fake-functionality mechanism).
- `src/components/PlannedModule.tsx` — honest placeholder for modules with no API
  contract yet (support, marketing, scheduling, finance).
- Auth: `/login` (email+password). No self-service registration in the API contract —
  accounts are by invitation (`POST /tenants/me/users`); the login page says so.

## Pages

| Route | Module | API wiring |
|---|---|---|
| `/` | Dashboard | `GET /command-center/summary` KPIs + system status |
| `/command-center` | AI Command Center | summary/goals/outcomes/costs/policy-activity; NL box **Planned** (no intent endpoint in API.md) |
| `/agents` | Agent Console | `GET /agents`, pause/resume, run inspector (`/agents/runs/{id}`), task lifecycle states |
| `/crm` | CRM | orgs, contacts, leads (create/convert/filter), opportunities kanban (move), pipelines, activity log; “Start lead pipeline” drives signature demo 1 |
| `/tasks` | Tasks | list/board, create, guarded transitions, delegate to agents, comments, outcome record |
| `/workflows` | Workflows | definitions, publish version, start executions, detail with event stream, signal/cancel |
| `/approvals` | Approvals | pending queue, detail with risk + policy reasons, approve/deny with note, escalate |
| `/support` | Support | **Planned** — no contract in API.md yet |
| `/marketing` | Marketing | **Planned** — no contract in API.md yet |
| `/scheduling` | Scheduling | **Planned** — no contract in API.md yet |
| `/finance` | Finance | **Planned** — no contract in API.md yet (NEXORA seam) |
| `/billing` | Billing | plans, subscription, change plan, usage, invoices, spend caps |
| `/admin` | Admin | users (invite/deactivate), roles, budgets + ledger + kill switch + alerts, audit log viewer + chain verify, tool registry; policies **Planned** |
| `/onboarding` | Onboarding | 10-step checklist wired to live state where a contract exists, Planned where not; demo reset instructions |
| `/settings` | Settings | tenant profile/settings, API keys (create/revoke) |

## Demo mode

A persistent banner marks demo data. Seed it server-side:

```
cd backend && python -m app.cli.seed_demo --base-url http://localhost:8000 \
  --email admin@demo.azienda --password "$AZIENDA_SEED_PASSWORD"
```

Signature demos: (1) new-lead pipeline — CRM → “Start lead pipeline” → Workflows →
Approvals → Tasks; (2) support ticket flow — UI ready, waiting on the support API
contract; (3) executive briefing — Command Center Briefing tab.

## Dev

```
npm install
npm test -- --run   # vitest
npm run build       # tsc --noEmit && vite build
npm run dev         # vite, proxies /api → localhost:8000
```
