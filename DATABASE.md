# Azienda — Database Schema

**Status:** normative schema skeleton for builders (Alembic implements). **Date:** 2026-09-15.
Stack: PostgreSQL 16/17 + pgvector (ADR-002), SQLAlchemy 2.0 async, Alembic (ADR-001).
Conventions: every table has `tenant_id uuid NOT NULL`; PKs are `id uuid DEFAULT gen_random_uuid()`;
timestamps `created_at/updated_at timestamptz`; money is `numeric(19,4)`; all times UTC.

## 1. Tenant isolation strategy

1. **Primary gate (application):** every query filters `tenant_id = :current_tenant`, bound from
   the JWT-derived `TenantContext` in `core/tenancy.py`. Repository helpers make unscoped queries
   impossible by construction.
2. **Defense in depth (Postgres RLS):** `ENABLE ROW LEVEL SECURITY` + `FOR ALL TO app_role USING
   (tenant_id = current_setting('app.tenant_id')::uuid)` on every tenant table. The app sets
   `app.tenant_id` per session/transaction. Migrations run as owner (bypass).
3. **Tests:** every new table ships a cross-tenant invisibility test (insert as tenant A, assert
   invisible to tenant B at both the repository and RLS level).
4. **NOT schema-per-tenant** (ADR-008): revisit only for regulated enterprise tenants.

## 2. Tenants / users / roles

```sql
tenants(id, name, slug unique, settings jsonb, created_at)
users(id, tenant_id, email unique per tenant, password_hash nullable (OIDC users),
      display_name, is_active, created_at)
roles(id, tenant_id, name, permissions jsonb)              -- RBAC per tenant
user_roles(user_id, role_id)
api_keys(id, tenant_id, name, key_hash, scopes jsonb, expires_at, revoked_at, created_by)
refresh_tokens(id, tenant_id, user_id, token_hash, expires_at, revoked_at, replaced_by)
```

## 3. CRM

```sql
organizations(id, tenant_id, name, domain, industry, size_band, lifecycle_stage,
              owner_id -> users, custom jsonb, is_archived)
contacts(id, tenant_id, org_id -> organizations, first_name, last_name, email, phone,
         title, tags text[], custom jsonb)
leads(id, tenant_id, contact_id nullable, org_id nullable, source, status, score,
      owner_id -> users, custom jsonb)
pipelines(id, tenant_id, name, object_type)               -- 'opportunity' at MVP
pipeline_stages(id, pipeline_id, name, position, probability numeric, is_closed_won, is_closed_lost)
opportunities(id, tenant_id, pipeline_id, stage_id -> pipeline_stages, org_id, contact_id,
              name, amount numeric, currency char(3), close_date date, owner_id, custom jsonb)
activities(id, tenant_id, subject_type, subject_id, type, body text, occurred_at, author_id)
```

## 4. Tasks / projects

State machine (binding): `pending → planning → waiting_approval → executing → blocked →
completed | failed`, plus `cancelled` from any non-terminal state.

```sql
projects(id, tenant_id, name, description, status, owner_id)
tasks(id, tenant_id, project_id nullable, title, description, status, priority,
      assignee_user_id nullable, assignee_agent_id nullable,
      plan jsonb,               -- planner output: steps × tools × est. cost
      cost_usd numeric,         -- actual fully-loaded cost so far
      due_at, completed_at, idempotency_key unique per tenant)
task_transitions(id, task_id, from_status, to_status, actor, note, created_at)
task_comments(id, task_id, author_id, body text, created_at)
```

## 5. Workflows

```sql
workflow_definitions(id, tenant_id, name, description, autonomy_level int 0..5, is_active)
workflow_versions(id, definition_id, version int, dag jsonb, published_at, published_by)
  -- dag is immutable once published; executions pin a version
workflow_executions(id, tenant_id, definition_id, version_id, status, input jsonb,
                    state jsonb,          -- checkpointer state
                    cost_usd numeric, started_at, finished_at, idempotency_key unique per tenant)
execution_events(id, execution_id, seq, event_type, payload jsonb, created_at)
```

## 6. Agents / tools / models

```sql
agents(id, tenant_id, name, description, status)          -- registered workforce
agent_versions(id, agent_id, version int, capabilities text[], allowed_tools text[],
               autonomy_level int 0..5, model_prefs jsonb, published_at)
agent_runs(id, tenant_id, agent_id, version_id, task_id nullable, status,
           policy_decisions jsonb, cost_usd numeric, started_at, finished_at)
tool_registry(id, tenant_id, name unique per tenant, description, input_schema jsonb,
              risk_tier, idempotent bool, source)        -- 'local' | 'mcp:<server>'
tool_calls(id, tenant_id, run_id nullable, task_id nullable, tool_name, arguments jsonb,
           grant_id, policy_decision jsonb, result jsonb, cost_usd numeric, created_at)
model_registry(id, tenant_id, name, provider, cost_per_1k_in numeric, cost_per_1k_out numeric,
               is_active)
mcp_servers(id, tenant_id, name, transport, endpoint, auth_ref, status, vetted_at, vetted_by)
```

## 7. Governance: policy / approvals / audit / budgets

```sql
policies(id, tenant_id, name, description, rego_source text nullable, is_active,
         priority int, updated_by, version int)
policy_versions(id, policy_id, version int, source text, created_at)
risk_scores(id, tenant_id, action_ref, score numeric, factors jsonb, reversible bool,
            financial_impact_usd numeric, created_at)

approvals(id, tenant_id, action text, resource nullable, args jsonb, risk_score_id nullable,
          policy_id nullable, reasons jsonb, status, requested_by, decided_by nullable,
          decided_at nullable, expires_at, note text)
approval_events(id, approval_id, event_type, actor, payload jsonb, created_at)

audit_ledger(id bigserial, tenant_id, seq bigint, actor, action, payload jsonb,
            prev_hash text, hash text, occurred_at)
  -- append-only (REVOKE UPDATE/DELETE from app_role), hash = sha256(prev_hash || canonical(payload))

budgets(id, tenant_id, name, scope,           -- 'tenant' | 'workflow' | 'agent'
        scope_ref nullable, credit_limit numeric, period, -- 'daily'|'weekly'|'monthly'
        alert_thresholds numeric[], is_active)
budget_periods(id, budget_id, starts_at, ends_at, credits_used numeric)
cost_ledger(id, tenant_id, task_id nullable, run_id nullable, model,
            input_tokens bigint, output_tokens bigint, cost_usd numeric,
            credits_drawn numeric, recorded_at)
spend_alerts(id, tenant_id, budget_id, threshold numeric, fired_at, acknowledged_at nullable)
```

Kill switch: `budgets.is_active=false` (or a tenant-level freeze flag) blocks
`BudgetEnforcer.reserve` → new agent work cannot start. Audited, reversible, RBAC: admin.

## 8. Memory / knowledge

```sql
memory_namespaces(id, tenant_id, name unique per tenant, ttl_default_seconds nullable)
memory_items(id, tenant_id, namespace_id, key, value jsonb, expires_at nullable, updated_at)

knowledge_sources(id, tenant_id, kind, uri, config jsonb, last_ingested_at, status)
documents(id, tenant_id, source_id, title, uri, metadata jsonb, checksum)
document_chunks(id, document_id, tenant_id, ordinal int, text, embedding vector(1536),
                metadata jsonb)
  -- HNSW index: CREATE INDEX ... USING hnsw (embedding vector_cosine_ops);
  -- retrieval filters tenant_id BEFORE ANN ranking (one query)
knowledge_edges(id, tenant_id, from_chunk_id, to_chunk_id, relation, weight numeric)
```

## 9. AGRL — event ledger + projections (one ledger, not three)

```sql
agrl_events(id bigserial, tenant_id, seq bigint, event_type text, aggregate_id uuid,
            payload jsonb, causation_id nullable, correlation_id nullable,
            actor, prev_hash text, hash text, occurred_at)
  -- append-only, hash-chained like audit_ledger; event_type examples:
  -- goal.created/updated/reprioritized/suspended/resumed/completed,
  -- resource.registered/allocated/released,
  -- plan.proposed/approved, outcome.recorded, learning.applied

agrl_projections(id, tenant_id, projection text, aggregate_id, state jsonb, at_seq bigint,
                 updated_at)
  -- projections: 'goal' | 'resource' | 'plan' | 'allocation' | 'outcome_summary'
  -- derived ONLY by folding agrl_events; never written directly by domain code

goals(id, tenant_id, aggregate_id unique, title, description, status, priority numeric,
      owner_id, parent_goal_id nullable, constraints jsonb, target_date)
goal_links(id, tenant_id, from_goal_id, to_goal_id, relation,   -- decomposes|blocks|depends_on|conflicts_with
           resolution jsonb nullable, created_at)
resource_allocations(id, tenant_id, aggregate_id unique, resource_type, resource_ref,
                     goal_id, amount numeric, unit, status, valid_from, valid_to)
```

Invariants: projections are rebuilt by replay (CLI: `azienda agrl rebuild`); the writer is the
only mutator of goals; consumers read projections.

## 10. Comms / marketing / support / scheduling

```sql
channels(id, tenant_id, kind, provider, config jsonb, is_active)   -- sms|voice|email|chat
conversations(id, tenant_id, channel_id, subject_type, subject_id, status, started_at)
messages(id, tenant_id, conversation_id, direction, body text, provider_msg_id,
         cost_usd numeric, status, sent_at)
message_templates(id, tenant_id, name, kind, body text, variables jsonb, approved_at nullable)
comms_usage(id, tenant_id, period date, kind, units bigint, cost_usd numeric)  -- pass-through billing

campaigns(id, tenant_id, name, status, audience_id, scheduled_at)
campaign_steps(id, campaign_id, position int, action, template_id nullable, delay jsonb)
audiences(id, tenant_id, name, filter jsonb)
audience_members(audience_id, contact_id)
content_assets(id, tenant_id, kind, title, body text, status, brand_check jsonb nullable)

tickets(id, tenant_id, subject, status, priority, requester_contact_id, assignee_user_id,
        assignee_agent_id nullable, sla_policy_id, opened_at, resolved_at, resolution text)
ticket_messages(id, ticket_id, author_type, author_id, body text, created_at)
sla_policies(id, tenant_id, name, first_response_minutes int, resolution_hours int, priority)

calendars(id, tenant_id, name, owner_id, timezone)
calendar_events(id, tenant_id, calendar_id, title, starts_at, ends_at, location, metadata jsonb)
bookings(id, tenant_id, event_id, contact_id, status, reminders jsonb)
availability_rules(id, calendar_id, weekday int, start_time, end_time)
```

## 11. Finance (foundational + NEXORA seam) / Billing (own SaaS)

```sql
-- finance/: operational cache; NEXORA ERP is the system of record (ARCHITECTURE.md §3)
invoices(id, tenant_id, number unique per tenant, org_id, amount numeric, currency,
         status, issued_at, due_at, nexora_ref nullable)
payments(id, tenant_id, invoice_id, amount numeric, method, received_at, nexora_ref nullable)
expenses(id, tenant_id, category, amount numeric, vendor, incurred_at, receipt_ref nullable)

-- billing/: Azienda's own SaaS billing. Pricing is DATA (commercialization §3).
billing_plans(id, slug unique, name, is_active)
plan_dimensions(id, plan_id, dimension, value jsonb)
  -- dimensions: platform_fee, seats_included, seat_price, credits_included,
  --             credit_overage_rate, agents_included, comms_markup_bps, storage_gb, ...
subscriptions(id, tenant_id, plan_id, status, current_period_start, current_period_end,
              spend_cap_usd nullable)
credit_pools(id, subscription_id, period_start, period_end, granted numeric, drawn numeric)
credit_transactions(id, pool_id, task_id nullable, delta numeric, reason, created_at)
usage_meters(id, tenant_id, period date, dimension, quantity numeric, cost_usd numeric)
billing_invoices(id, tenant_id, subscription_id, period_start, period_end,
                 lines jsonb, total_usd numeric, status)
```

## 12. Alembic migration plan

- `alembic/` at `backend/` root; one linear history; `alembic.ini` with async URL.
- Migration numbering: `0001_tenancy_auth.py`, `0002_crm.py`, `0003_tasks.py`,
  `0004_workflows.py`, `0005_agents_tools.py`, `0006_governance.py`, `0007_memory_knowledge.py`,
  `0008_agrl.py`, `0009_comms_marketing.py`, `0010_support_scheduling.py`,
  `0011_finance_billing.py`, `0012_rls_policies.py` (RLS last, after tables exist).
- CI check: fresh DB must upgrade `base → head` cleanly; `alembic check` on every PR.
- Backward-compatible discipline (expand → migrate → contract) once production data exists.
- Seed: `python -m app.cli seed dev` — demo tenant, roles, a sample pipeline, one workflow
  definition; never in prod.
