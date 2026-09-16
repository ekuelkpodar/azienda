# DEVELOPMENT.md — Azienda developer guide

## Repo layout

- `backend/` — FastAPI modular monolith (Python 3.12, Pydantic v2)
- `frontend/` — React 19 + Vite + TypeScript SPA
- `scripts/demos/` — 3 signature demos (`demo1_new_lead.py`, `demo2_support.py`,
  `demo3_exec_briefing.py`); run from `backend/` with `.venv/bin/python`
- `docs/` — ADRs, Mermaid diagrams, research notes
- `backend/tests/` — 269 backend tests (2026-09-16)

## Rules (non-negotiable)

1. Modular monolith, clean package boundaries — cross-package imports go
   through `backend/app/core/contracts.py` only.
2. **Policy-before-execution, fail closed** — writes are policy-gated; when in
   doubt, deny.
3. **No fake functionality** — label stubs honestly in the package README and
   in code comments.
4. **No test weakening** — never weaken a test to get green; fix the code.
5. Factual documentation claims need evidence labels (CONFIRMED / LIKELY /
   ASSUMED / UNKNOWN).

## Backend workflow

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e .
.venv/bin/python -m pytest tests/ -q      # full suite
ruff check app tests                      # lint (check pyproject for config)
mypy --strict app                         # typecheck (foundation set)
```

Alembic migrations live in `backend/alembic/versions/` — linear chain
`0001 → 0002 → 0003 → e4f7a2b91c5d → 0004 → 2fd0cc725f7b (head)`.
Round-trip verified on SQLite (base→head 91 tables, head→base 0 leftover).
Migrations use Postgres dialect-aware fallbacks for SQLite tests.

## Frontend workflow

```bash
cd frontend
npm install
npm test -- --run
npm run build
```

## Adding a new package

1. Create `backend/app/<pkg>/` with `models.py`, `schemas.py`, `service.py`,
   `router.py`, `README.md` (with an honest gaps section).
2. Add migrations; keep the chain linear.
3. Add tests under `backend/tests/`; target the full-suite green.
4. Document the REST surface in `API.md`.
