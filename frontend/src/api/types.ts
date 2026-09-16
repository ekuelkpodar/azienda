/**
 * API types — mirrors the contracts in `/API.md` (base path `/api/v1`).
 * Builders: if the backend schema drifts, update these alongside the router.
 */

export interface ApiErrorBody {
  code: string;
  message: string;
  details?: Record<string, unknown>;
  trace_id?: string;
}

export interface Page<T> {
  items: T[];
  next_page_token: string | null;
}

/* ---------- Auth ---------- */
export interface AuthUser {
  id: string;
  email: string;
  display_name: string;
  is_active: boolean;
}
export interface AuthTenant {
  id: string;
  name: string;
  slug: string;
  settings?: Record<string, unknown>;
}
export interface AuthMe {
  user: AuthUser;
  tenant: AuthTenant;
  roles: string[];
}
export interface LoginResponse {
  access_token: string;
  token_type: "bearer";
  expires_in: number;
}

/* ---------- Tenants ---------- */
export interface TenantUser {
  id: string;
  email: string;
  display_name: string;
  is_active: boolean;
  roles: string[];
}
export interface Role {
  id: string;
  name: string;
  permissions: Record<string, unknown>;
}

/* ---------- CRM ---------- */
export interface Organization {
  id: string;
  name: string;
  domain?: string | null;
  industry?: string | null;
  size_band?: string | null;
  lifecycle_stage?: string | null;
  owner_id?: string | null;
  is_archived: boolean;
  created_at: string;
}
export interface Contact {
  id: string;
  org_id?: string | null;
  first_name: string;
  last_name: string;
  email?: string | null;
  phone?: string | null;
  title?: string | null;
  tags: string[];
}
export type LeadStatus =
  | "new"
  | "contacted"
  | "qualified"
  | "unqualified"
  | "converted";
export interface Lead {
  id: string;
  contact_id?: string | null;
  org_id?: string | null;
  source?: string | null;
  status: LeadStatus;
  score?: number | null;
  owner_id?: string | null;
  created_at: string;
}
export interface PipelineStage {
  id: string;
  name: string;
  position: number;
  probability: number;
  is_closed_won: boolean;
  is_closed_lost: boolean;
}
export interface Pipeline {
  id: string;
  name: string;
  object_type: string;
  stages: PipelineStage[];
}
export interface Opportunity {
  id: string;
  pipeline_id: string;
  stage_id: string;
  org_id?: string | null;
  contact_id?: string | null;
  name: string;
  amount?: number | null;
  currency: string;
  close_date?: string | null;
  owner_id?: string | null;
  created_at: string;
}
export interface Activity {
  id: string;
  subject_type: string;
  subject_id: string;
  type: string;
  body?: string | null;
  occurred_at: string;
  author_id?: string | null;
}

/* ---------- Tasks ---------- */
export type TaskStatus =
  | "pending"
  | "planning"
  | "waiting_approval"
  | "executing"
  | "blocked"
  | "completed"
  | "failed"
  | "cancelled";
export interface Task {
  id: string;
  project_id?: string | null;
  title: string;
  description?: string | null;
  status: TaskStatus;
  priority: string;
  assignee_user_id?: string | null;
  assignee_agent_id?: string | null;
  plan?: unknown;
  cost_usd?: number | null;
  due_at?: string | null;
  completed_at?: string | null;
  created_at: string;
}
export interface TaskTransition {
  id: string;
  from_status: TaskStatus;
  to_status: TaskStatus;
  actor?: string | null;
  note?: string | null;
  created_at: string;
}
export interface TaskComment {
  id: string;
  author_id?: string | null;
  body: string;
  created_at: string;
}
export interface TaskOutcome {
  task_id: string;
  plan?: unknown;
  policy_decisions?: unknown[];
  tool_calls?: unknown[];
  retries: number;
  human_interventions: number;
  cost_usd?: number | null;
}

/* ---------- Workflows ---------- */
export interface WorkflowDefinition {
  id: string;
  name: string;
  description?: string | null;
  autonomy_level: number;
  is_active: boolean;
}
export interface WorkflowVersion {
  id: string;
  version: number;
  dag: unknown;
  published_at?: string | null;
}
export type WorkflowExecutionStatus =
  | "pending"
  | "running"
  | "waiting_approval"
  | "succeeded"
  | "failed"
  | "cancelled";
export interface WorkflowExecution {
  id: string;
  definition_id: string;
  version_id?: string | null;
  status: WorkflowExecutionStatus;
  input?: unknown;
  state?: unknown;
  cost_usd?: number | null;
  started_at?: string | null;
  finished_at?: string | null;
}
export interface ExecutionEvent {
  id: string;
  seq: number;
  event_type: string;
  payload?: unknown;
  created_at: string;
}

/* ---------- Agents ---------- */
export type AgentStatus = "active" | "paused" | "draft";
export interface Agent {
  id: string;
  name: string;
  description?: string | null;
  status: AgentStatus;
}
export interface AgentVersion {
  id: string;
  version: number;
  capabilities: string[];
  allowed_tools: string[];
  autonomy_level?: number | null;
  model_prefs?: Record<string, unknown> | null;
}
export interface AgentRunStep {
  id?: string;
  name: string;
  tool?: string | null;
  status: string;
  input?: unknown;
  output?: unknown;
  cost_usd?: number | null;
  started_at?: string | null;
  finished_at?: string | null;
}
export interface AgentRun {
  id: string;
  agent_id: string;
  task_id?: string | null;
  status: string;
  steps: AgentRunStep[];
  tool_calls?: unknown[];
  policy_decisions?: unknown[];
  cost_usd?: number | null;
  started_at?: string | null;
  finished_at?: string | null;
}

/* ---------- Tools ---------- */
export interface Tool {
  name: string;
  description?: string | null;
  capability?: string | null;
  risk_tier?: string | null;
  schema?: unknown;
  idempotent?: boolean;
}

/* ---------- Approvals ---------- */
export type ApprovalStatus = "pending" | "approved" | "denied" | "expired" | "escalated";
export interface Approval {
  id: string;
  action: string;
  args?: Record<string, unknown>;
  risk_score?: number | null;
  policy_reasons?: string[];
  requester?: string | null;
  status: ApprovalStatus;
  created_at: string;
  decided_at?: string | null;
}

/* ---------- Budgets ---------- */
export interface Budget {
  id: string;
  name: string;
  credits: number;
  period: string;
  burn?: number | null;
  alerts?: { threshold: number; triggered: boolean }[];
}
export interface BudgetLedgerEntry {
  id: string;
  task_id?: string | null;
  model?: string | null;
  amount_usd: number;
  reason?: string | null;
  created_at: string;
}
export interface BudgetAlert {
  id: string;
  budget_id: string;
  threshold: number;
  message: string;
  created_at: string;
}

/* ---------- Audit ---------- */
export interface AuditEntry {
  seq: number;
  actor?: string | null;
  action: string;
  payload?: unknown;
  prev_hash?: string | null;
  hash?: string | null;
  created_at: string;
}

/* ---------- Command Center ---------- */
export interface CommandSummary {
  active_goals: number;
  running_tasks: number;
  pending_approvals: number;
  burn_today_usd: number;
  outcome_counts: Record<string, number>;
}
export interface Goal {
  id: string;
  title: string;
  status: string;
  progress?: number | null;
  owner_id?: string | null;
  updated_at: string;
}
export interface Outcome {
  id: string;
  type: string;
  title?: string | null;
  status: string;
  cost_usd?: number | null;
  created_at: string;
}
export interface CostBreakdownItem {
  key: string;
  label: string;
  cost_usd: number;
}
export interface PolicyActivityItem {
  decision: "allow" | "deny" | "require_approval";
  action: string;
  count: number;
}

/* ---------- Billing ---------- */
export interface BillingPlan {
  id: string;
  name: string;
  price_usd: number;
  included_credits: number;
  dimensions?: Record<string, unknown>;
}
export interface Subscription {
  plan_id: string;
  plan_name: string;
  status: string;
  credit_balance: number;
  renews_at?: string | null;
}
export interface UsageItem {
  dimension: string;
  used: number;
  unit: string;
}
export interface BillingInvoice {
  id: string;
  period: string;
  total_usd: number;
  status: string;
  line_items?: { description: string; amount_usd: number }[];
}

/* ---------- Onboarding ---------- */
export interface OnboardingState {
  org_created: boolean;
  user_invited: boolean;
  profile_completed: boolean;
  integration_connected: boolean;
  data_imported: boolean;
  ai_configured: boolean;
  agent_registered: boolean;
  permissions_set: boolean;
  approvals_configured: boolean;
  first_workflow_run: boolean;
}
