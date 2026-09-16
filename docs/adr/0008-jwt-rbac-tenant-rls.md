# ADR-008: Auth = JWT access + rotating refresh, RBAC, tenant_id + RLS

**Status:** accepted · **Date:** 2026-09-15 · **Source:** `docs/research/stack-decision.md` §2.8/§5

## Context

Stateless API tier, multi-tenant SaaS, and agents as a principal class alongside humans.
Sessions-in-Redis as the *only* auth fights horizontal scaling (ASSUMED judgment).

## Decision

- **Humans:** OIDC-ready login; **short-lived JWT access tokens (5–15 min) + rotating refresh
  tokens with reuse detection** (reuse → revoke the whole chain); HttpOnly Secure cookies for
  the SPA. RBAC roles per tenant; the policy engine (governance rail) handles fine-grained
  action checks.
- **Agents/tools:** no ambient authority — per-action scoped grants with expiry; secret
  brokering, never secret passing (anti-confused-deputy).
- **Tenant isolation:** `tenant_id` on every row, **application-enforced as the primary gate**
  (query filters + cross-tenant invisibility tests); **Postgres RLS as defense-in-depth**.
  Hierarchy: Platform → Tenant → {users, agents, tools, policies, knowledge, budgets, audit}.
- **Never roll custom crypto.** JWT via PyJWT; passwords via bcrypt/argon2.
- OIDC interface reserved for SSO later; schema-per-tenant / DB-per-tenant rejected for MVP
  (revisit for regulated enterprise tenants).

## Consequences

- **+** Horizontal scale of the API tier; fail-closed tenancy with two independent gates.
- **−** Token theft window is bounded by rotation + short lifetimes + denylist, not eliminated.

## Step-up trigger

Regulated enterprise tenant → dedicated schema/DB + SSO (OIDC) + customer-managed keys, as a
deployment-model option — not a rewrite of the default path.
