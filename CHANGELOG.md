# CHANGELOG.md — Azienda

## 2026-09-16 — Integration & verification wave (MVP)

**Integration lead verification (CONFIRMED):**

- Backend: 269 tests pass, 0 fail, 0 skipped (full suite re-run after fixes).
- Frontend: 18 tests pass; `tsc --noEmit` + Vite production build green
  (63 modules, 372.06 kB JS / 109.55 kB gzip).
- Alembic: linear chain of 6 revisions; SQLite upgrade base→head creates
  91 tables; downgrade head→base leaves 0 tables.
- 3 signature demos, all PASS: demo1 (12/12), demo2 (9/9), demo3 (11/11).

**Fixes:**

- Added missing `jinja2>=3.1` dependency (imported by comms/marketing templates).
- Fixed CRM lead-scoring naive/aware datetime crash on SQLite (naive treated as UTC).
- Fixed `0002_governance.py` omitting `audit_ledger` from `TENANT_TABLES` downgrade list.
- Fixed ACP migration for SQLite (TEXT[]/VECTOR/pg defaults → JSON fallbacks).
- Fixed 4 migrations to import `helpers` with `backend/alembic` on `sys.path`.

**Hardening:**

- Production fail-closed guard: `X-Tenant-Id` header seam returns 401 when
  `AZIENDA_ENVIRONMENT=production`; permissive policy engine refuses production use.

**Docs added:** SECURITY.md, DEPLOYMENT.md, DEVELOPMENT.md, CONTRIBUTING.md,
GOVERNANCE.md, THREAT_MODEL.md, EVALUATION.md, MCP.md, AI_ARCHITECTURE.md,
ROADMAP.md, PRICING.md, CHANGELOG.md (this file).

**Honest gaps carried forward** (see SECURITY.md / ROADMAP.md): `_common.py`
JWT tenancy debt on 8 routers; in-memory AGRL/knowledge; unwired agent graph,
command-center, tools routers; no OIDC/MFA/OPA/Redis; no live providers;
Postgres RLS untested live.

## 2026-09-15 — Implementation wave (5 builders)

- 15 backend packages behind `core/contracts.py`; 9 ADRs; research docs
  (commercialization, competitive analysis, stack decision); Mermaid diagrams;
  per-package READMEs with gap sections; React 19/Vite SPA skeleton.
