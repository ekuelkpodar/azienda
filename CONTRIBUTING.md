# CONTRIBUTING.md — Azienda

## How to contribute

1. Fork / branch from `main`. Keep branches short-lived and focused.
2. Follow the rules in `DEVELOPMENT.md` (contracts seam, policy-before-execution,
   no fake functionality, no test weakening).
3. Evidence-label any factual claim you add to docs (CONFIRMED / LIKELY /
   ASSUMED / UNKNOWN) — see `AGENTS.md` §5.
4. Add tests for new behavior; keep the full backend suite green
   (`cd backend && .venv/bin/python -m pytest tests/ -q`).
5. Keep Alembic history linear; one head at all times.
6. Update the per-package README gaps section when you add or remove a stub.

## Pull requests

- Describe what changed, why, and what you verified (exact test counts).
- Name any new gaps honestly; a PR that adds a stub without labeling it will
  be sent back.
- Do not push directly to `main` from integration sessions unless the
  coordinator asks.
