# Azienda — the AI Business OS

**Business applications, run by agents, governed by policy.**

> Status (2026-09-16, integration/verification wave — CONFIRMED): backend 269
> tests pass / 0 fail, frontend 18 tests pass + production build green, Alembic
> chain linear (6 revisions, SQLite round-trip: 91 tables up / 0 left on
> downgrade), 3 signature demos all PASS (12/12, 9/9, 11/11 steps). Live
> Postgres RLS, pgvector, Redis, OPA, OIDC/MFA, and all external providers are
> NOT yet live (UNKNOWN/unimplemented — see SECURITY.md, ROADMAP.md). This is
> an MVP: do not deploy to production.

## The product

Azienda is a commercial AI Business OS: modular business applications (CRM, tasks,
workflows, marketing, support, scheduling, finance, comms) operated by an AI agent
workforce under an Agent Control Plane, with a governance/security rail enforcing
policy at action time, an event-sourced goal/resource ledger (AGRL), and an outcome
ledger with transparent unit economics.

The loop is the product: **GOAL → CONTEXT → PLAN → POLICY CHECK → TOOL SELECTION →
EXECUTION → OBSERVATION → FEEDBACK → LEARNING** — across departments, with policy
enforced before the tool call executes.

## Docs

- `PRODUCT.md` — product thesis + positioning (one page)
- `ARCHITECTURE.md` — spine, package map, lifecycles, tenancy, security, AGRL
- `AI_ARCHITECTURE.md` — how AI fits into each layer; what is rule-based vs model
- `API.md` — REST contract for all MVP surfaces
- `DATABASE.md` — full schema + tenant isolation + migration plan
- `GOVERNANCE.md` — policy engine, approvals, audit, budgets, kill switch
- `SECURITY.md` — enforced controls + honest gaps (read before deploying)
- `THREAT_MODEL.md` — assets, actors, top-10 threats
- `EVALUATION.md` — eval philosophy, demo harness, missing eval suites
- `MCP.md` — MCP posture (untrusted, rail-gated)
- `DEPLOYMENT.md` — quickstart + production readiness checklist
- `DEVELOPMENT.md` — dev workflow, conventions, adding a package
- `PRICING.md` — platform fee + credits + overages model (from research)
- `ROADMAP.md` — sequenced plan: production hardening, then revenue
- `CHANGELOG.md` — what changed and when
- `docs/adr/` — the 9 adopted architecture decision records
- `docs/diagrams/` — Mermaid: spine, pipeline, lifecycle, ER, topology, AGRL flow
- `docs/research/` — competitive analysis, stack decision, commercialization
- `AGENTS.md` — how AI agents must behave in this repo

## Quickstart (executable)

```bash
cp .env.example .env
docker compose up --build
# API: http://localhost:8000  ·  docs: http://localhost:8000/docs
```

Backend only:

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e .
.venv/bin/python -m pytest tests/ -q
```

Frontend only:

```bash
cd frontend
npm install
npm test -- --run
npm run build
```

Signature demos (deterministic, no LLM, fresh temp DB per run):

```bash
cd backend
.venv/bin/python ../scripts/demos/demo1_new_lead.py        # 12/12 PASS
.venv/bin/python ../scripts/demos/demo2_support.py          # 9/9 PASS
.venv/bin/python ../scripts/demos/demo3_exec_briefing.py   # 11/11 PASS
```

## Layout

- `backend/` — FastAPI modular monolith (Python 3.12, Pydantic v2)
- `frontend/` — React 19 + Vite + TS SPA, served same-origin by the API
- `scripts/demos/` — 3 signature demos with PASS/FAIL verdicts
- `docs/` — ADRs, diagrams, research

## Rules (non-negotiable)

1. Modular monolith, clean package boundaries — cross-package imports go through
   `backend/app/core/contracts.py` only. CI enforces this.
2. `tenant_id` on every row; Postgres RLS as defense-in-depth.
3. Policy-before-execution for every consequential action. Fail closed.
4. No secrets in code, logs, or git. Env only.
5. Budgets + kill switches are P0 — unbounded agent cost is the #1 risk.
6. Evidence labels (CONFIRMED/LIKELY/ASSUMED/UNKNOWN) on factual claims in docs.
