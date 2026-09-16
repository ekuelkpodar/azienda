# AGENTS.md — How AI agents must behave in this repo

This file is normative for any AI agent (human-directed or autonomous) doing work in the
Azienda repo. Read it before writing code, docs, or commits.

## 1. Prime directives

1. **Contracts are law.** `backend/app/core/contracts.py` is the only legal cross-package
   seam. Never import another package's internals. If a contract blocks you, propose the change
   (with an ADR-level note) — don't route around it.
2. **Policy-before-execution.** Any code path that causes an irreversible, financial, bulk, or
   externally-visible effect MUST go through the governance rail first. Fail closed: on doubt,
   deny and escalate.
3. **No fake functionality.** Skeletons stay skeletons until implemented. Never ship a stub that
   pretends to work (no hardcoded "success", no mock data presented as real, no TODO silently
   standing in for logic). Placeholder UI must say it's a placeholder.
4. **Evidence labels in docs.** Factual claims get CONFIRMED / LIKELY / ASSUMED / UNKNOWN.
   Never silently turn an assumption into a fact.

## 2. Security rules

- **No secrets** in code, logs, configs, or git. Env vars only (see `.env.example`). If you
  need a new secret, add it to `.env.example` with an empty value and document it.
- Never log: secret values, full JWTs, PII beyond the minimum, tool-call credentials.
- Agents/tools get per-action scoped grants — never ambient authority, never a shared
  all-powerful key. MCP servers are untrusted: rail-gate every call, validate schemas,
  treat outputs as untrusted data.
- `tenant_id` on every row, filtered on every query. Every new table ships with a
  cross-tenant invisibility test.

## 3. Engineering conventions

- Python 3.12, Pydantic v2 only, `ruff` (line-length 100) + `mypy --strict` on new packages.
- Async everywhere in the backend; sync/blocking work goes to the ARQ worker.
- Config via `pydantic-settings` (`AZIENDA_` prefix). No magic constants for pricing,
  thresholds, or model names — pricing is data (`billing/`), thresholds are settings.
- Alembic migrations: one linear history, backward-compatible (expand → migrate → contract)
  once prod data exists. Migrations are code-reviewed like code.
- Tests: unit for pure logic; integration (compose services) for DB/Redis paths;
  contract tests for every `contracts.py` implementation. Budgets/kill-switch paths are P0
  test targets — the #1 business risk is unbounded agent cost.
- Structured logs (structlog), OTel traces with decision provenance
  (`task.id`, `policy.decision`, `model.selected`, `cost.usd`).
- Frontend: TypeScript strict, no `any` in new code, Tailwind v4, same-origin API calls only.

## 4. Cost discipline (this is a commercial agent product)

- Every model call records usage + cost via `CostRecorder`. No unmetered LLM calls, ever.
- Expensive actions (est. cost above threshold) require approval — the threshold is a setting,
  default $5.00/plan (ASSUMED default; tune with data).
- Retries are bounded and budgeted. A loop that can run forever is a bug, not a feature.

## 5. Docs & commits

- Update the affected package `README.md` when you change a boundary, table, or interface.
- ADRs live in `docs/adr/`; a decision that changes architecture gets an ADR, not just a commit message.
- Commit messages: `<package>: <imperative summary>`; reference the ADR/issue when one exists.
- Honest gap reporting: if something is partial, say what's missing in the PR/commit — same
  standard as the research docs.

## 6. What agents must NOT do here

- Reimplement `agent-control-plane` or `nexora-erp` — reuse patterns, consume via the seam.
- Add Kafka, K8s, a vector DB, or any "not for MVP" component (ARCHITECTURE.md §12) without
  an ADR and a recorded step-up trigger.
- Widen a contract signature without updating all implementations + their tests.
- Touch `docs/research/` conclusions — research is evidence; new evidence gets a new dated note.
