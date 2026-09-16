# DEPLOYMENT.md — Azienda deployment guide

> Status 2026-09-16: **the quickstart below boots locally (ASSUMED — compose
> boot smoke has not been run in this session). Production deployment is
> NOT ready** — see the production blockers in SECURITY.md (JWT tenancy debt,
> in-memory stores, untested Postgres RLS).

## Local quickstart (executable)

```bash
cp .env.example .env
docker compose up --build
# API: http://localhost:8000 · docs: http://localhost:8000/docs
```

Backend alone (verified 2026-09-16):

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e .
.venv/bin/python -m pytest tests/ -q        # 269 passed, 0 failed
uvicorn app.main:app --reload                # check app/main.py for entrypoint
```

Frontend alone (verified 2026-09-16):

```bash
cd frontend
npm install && npm test -- --run            # 18 tests pass
npm run build                                # tsc + vite green
```

## Environment variables

See `.env.example` (check the repo root). Key variables:

- `AZIENDA_ENVIRONMENT` — `development` | `production`. In `production`, the
  permissive policy engine refuses to run and the `X-Tenant-Id` header seam
  returns 401 (fail-closed guards, CONFIRMED).
- Database URL — Postgres for production; SQLite only for tests/demos.
- Secrets (JWT signing key, API keys) — must come from a secrets manager in
  production (CONFIRMED absent: no Vault integration; pass via env for now).

## Production readiness checklist (NOT done)

- [ ] Replace `_common.py` header tenancy with JWT-derived tenant on all 8
      business-app routers
- [ ] Postgres + pgvector live, RLS enforcement tested, Alembic head applied
- [ ] Redis/Valkey rate limiter and distributed locks
- [ ] Persistent AGRL + knowledge stores
- [ ] OPA/Rego policy engine
- [ ] OIDC/SSO + MFA
- [ ] Secrets management (Vault or equivalent)
- [ ] TLS termination, WAF, egress controls
- [ ] Observability: structured logs, metrics, traces wired to an aggregator
- [ ] Backup/restore tested, runbooks written
