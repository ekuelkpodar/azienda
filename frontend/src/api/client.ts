/**
 * Typed API client for the Azienda API (`/api/v1`), per `/API.md`.
 *
 * - Same-origin: the FastAPI backend serves this SPA, so all calls are relative.
 * - Auth: short-lived Bearer access token (sessionStorage) + rotating refresh in
 *   an HttpOnly Secure cookie. On 401 the client transparently attempts one
 *   refresh and retries the original request; refresh failure clears the session.
 * - All mutating POSTs accept an `idempotencyKey` (generated automatically when
 *   omitted) sent as the `Idempotency-Key` header per API.md §1.
 *
 * Contract status (2026-09-15): backend builders implement routers in parallel
 * against the same API.md. Endpoints for support, marketing, scheduling, finance
 * and the command-center NL intent are NOT in API.md §1–15 and are therefore NOT
 * implemented here — pages for those modules render honest "Planned" states.
 */

import type {
  Activity,
  Agent,
  AgentRun,
  AgentRunStep,
  AgentStatus,
  AgentVersion,
  ApiErrorBody,
  Approval,
  ApprovalStatus,
  AuditEntry,
  AuthMe,
  AuthTenant,
  Budget,
  BudgetAlert,
  BudgetLedgerEntry,
  BillingInvoice,
  BillingPlan,
  CommandSummary,
  Contact,
  CostBreakdownItem,
  Goal,
  Lead,
  LeadStatus,
  LoginResponse,
  Opportunity,
  Organization,
  Outcome,
  Page,
  Pipeline,
  PolicyActivityItem,
  Role,
  Subscription,
  Task,
  TaskComment,
  TaskOutcome,
  TaskStatus,
  TaskTransition,
  TenantUser,
  Tool,
  UsageItem,
  WorkflowDefinition,
  WorkflowExecution,
  WorkflowVersion,
  ExecutionEvent,
} from "./types";

export type { ApiErrorBody };

export const API_BASE = "/api/v1";
const TOKEN_KEY = "azienda.access_token";

export class ApiClientError extends Error {
  constructor(
    public status: number,
    public body: ApiErrorBody,
  ) {
    super(body.message || `Request failed (${status})`);
    this.name = "ApiClientError";
  }
  get code(): string {
    return this.body.code;
  }
}

function newIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `key-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function encodeQuery(params: Record<string, string | number | boolean | undefined | null>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}

interface RequestOptions {
  query?: Record<string, string | number | boolean | undefined | null>;
  body?: unknown;
  idempotencyKey?: string | null;
  /** skip the 401→refresh→retry cycle (used by the refresh call itself) */
  noAuthRetry?: boolean;
}

export class AziendaClient {
  private token: string | null = null;

  constructor() {
    try {
      this.token = sessionStorage.getItem(TOKEN_KEY);
    } catch {
      this.token = null;
    }
  }

  get accessToken(): string | null {
    return this.token;
  }

  get isAuthenticated(): boolean {
    return this.token !== null;
  }

  setToken(token: string | null): void {
    this.token = token;
    try {
      if (token) sessionStorage.setItem(TOKEN_KEY, token);
      else sessionStorage.removeItem(TOKEN_KEY);
    } catch {
      /* storage unavailable (e.g. tests) — keep in memory */
    }
  }

  private async refreshAccessToken(): Promise<boolean> {
    try {
      const res = await fetch(`${API_BASE}/auth/refresh`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
      });
      if (!res.ok) return false;
      const data = (await res.json()) as LoginResponse;
      if (data.access_token) {
        this.setToken(data.access_token);
        return true;
      }
      return false;
    } catch {
      return false;
    }
  }

  async request<T>(method: string, path: string, opts: RequestOptions = {}): Promise<T> {
    const url = `${API_BASE}${path}${encodeQuery(opts.query ?? {})}`;
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (this.token) headers["Authorization"] = `Bearer ${this.token}`;
    if (method === "POST" && opts.idempotencyKey !== null) {
      headers["Idempotency-Key"] = opts.idempotencyKey ?? newIdempotencyKey();
    }

    let res = await fetch(url, {
      method,
      headers,
      credentials: "include",
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
    });

    if (res.status === 401 && !opts.noAuthRetry && this.token) {
      const refreshed = await this.refreshAccessToken();
      if (refreshed) {
        const retryHeaders = { ...headers, Authorization: `Bearer ${this.token}` };
        res = await fetch(url, {
          method,
          headers: retryHeaders,
          credentials: "include",
          body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
        });
      } else {
        this.setToken(null);
      }
    }

    if (!res.ok) {
      let body: { error?: ApiErrorBody } = {};
      try {
        body = (await res.json()) as { error?: ApiErrorBody };
      } catch {
        /* non-JSON error body */
      }
      throw new ApiClientError(
        res.status,
        body.error ?? { code: "http_error", message: res.statusText || `HTTP ${res.status}` },
      );
    }
    if (res.status === 204) return undefined as T;
    return (await res.json()) as T;
  }

  /* ---------- auth ---------- */
  async login(email: string, password: string): Promise<LoginResponse> {
    const data = await this.request<LoginResponse>("POST", "/auth/login", {
      body: { email, password },
      noAuthRetry: true,
      idempotencyKey: null,
    });
    if (data.access_token) this.setToken(data.access_token);
    return data;
  }

  async logout(): Promise<void> {
    try {
      await this.request<void>("POST", "/auth/logout", { idempotencyKey: null });
    } finally {
      this.setToken(null);
    }
  }

  me(): Promise<AuthMe> {
    return this.request<AuthMe>("GET", "/auth/me");
  }

  createApiKey(name: string, scopes: string[]): Promise<{ id: string; key: string }> {
    return this.request("POST", "/auth/api-keys", { body: { name, scopes } });
  }

  revokeApiKey(id: string): Promise<void> {
    return this.request("DELETE", `/auth/api-keys/${encodeURIComponent(id)}`);
  }

  /* ---------- tenants ---------- */
  getTenant(): Promise<AuthTenant> {
    return this.request("GET", "/tenants/me");
  }

  updateTenant(settings: Record<string, unknown>): Promise<AuthTenant> {
    return this.request("PATCH", "/tenants/me", { body: { settings } });
  }

  listUsers(): Promise<Page<TenantUser>> {
    return this.request("GET", "/tenants/me/users");
  }

  inviteUser(email: string, display_name: string, role: string): Promise<TenantUser> {
    return this.request("POST", "/tenants/me/users", { body: { email, display_name, role } });
  }

  updateUser(id: string, patch: { role?: string; is_active?: boolean }): Promise<TenantUser> {
    return this.request("PATCH", `/tenants/me/users/${encodeURIComponent(id)}`, { body: patch });
  }

  listRoles(): Promise<Role[]> {
    return this.request<Role[]>("GET", "/tenants/me/roles").catch(() =>
      this.request<Page<Role>>("GET", "/tenants/me/roles").then((p) => p.items),
    );
  }

  /* ---------- CRM ---------- */
  listOrganizations(params?: { search?: string; page_size?: number; page_token?: string }): Promise<Page<Organization>> {
    return this.request("GET", "/crm/organizations", { query: params });
  }
  createOrganization(org: Partial<Organization>): Promise<Organization> {
    return this.request("POST", "/crm/organizations", { body: org });
  }
  updateOrganization(id: string, patch: Partial<Organization>): Promise<Organization> {
    return this.request("PATCH", `/crm/organizations/${encodeURIComponent(id)}`, { body: patch });
  }

  listContacts(params?: { org?: string; tag?: string; search?: string; page_size?: number; page_token?: string }): Promise<Page<Contact>> {
    return this.request("GET", "/crm/contacts", { query: params });
  }
  createContact(contact: Partial<Contact>): Promise<Contact> {
    return this.request("POST", "/crm/contacts", { body: contact });
  }
  getContact(id: string): Promise<Contact> {
    return this.request("GET", `/crm/contacts/${encodeURIComponent(id)}`);
  }
  updateContact(id: string, patch: Partial<Contact>): Promise<Contact> {
    return this.request("PATCH", `/crm/contacts/${encodeURIComponent(id)}`, { body: patch });
  }

  listLeads(params?: { status?: LeadStatus | string; owner?: string; page_size?: number; page_token?: string }): Promise<Page<Lead>> {
    return this.request("GET", "/crm/leads", { query: params });
  }
  createLead(lead: Partial<Lead>): Promise<Lead> {
    return this.request("POST", "/crm/leads", { body: lead });
  }
  convertLead(id: string): Promise<{ organization: Organization; contact: Contact; opportunity: Opportunity }> {
    return this.request("POST", `/crm/leads/${encodeURIComponent(id)}/convert`, {});
  }

  listOpportunities(params?: { pipeline?: string; stage?: string; page_size?: number; page_token?: string }): Promise<Page<Opportunity>> {
    return this.request("GET", "/crm/opportunities", { query: params });
  }
  createOpportunity(opp: Partial<Opportunity>): Promise<Opportunity> {
    return this.request("POST", "/crm/opportunities", { body: opp });
  }
  moveOpportunity(id: string, stage_id: string): Promise<Opportunity> {
    return this.request("POST", `/crm/opportunities/${encodeURIComponent(id)}/move`, { body: { stage_id } });
  }

  listPipelines(): Promise<Page<Pipeline>> {
    return this.request("GET", "/crm/pipelines");
  }
  createPipeline(name: string, stages: { name: string; probability: number }[]): Promise<Pipeline> {
    return this.request("POST", "/crm/pipelines", { body: { name, object_type: "opportunity", stages } });
  }

  listActivities(params?: { subject_type?: string; subject_id?: string }): Promise<Page<Activity>> {
    return this.request("GET", "/crm/activities", { query: params });
  }
  logActivity(activity: Partial<Activity>): Promise<Activity> {
    return this.request("POST", "/crm/activities", { body: activity });
  }

  /* ---------- tasks ---------- */
  listTasks(params?: { status?: TaskStatus | string; assignee?: string; project?: string; page_size?: number; page_token?: string }): Promise<Page<Task>> {
    return this.request("GET", "/tasks", { query: params });
  }
  createTask(task: Partial<Task>, idempotencyKey?: string): Promise<Task> {
    return this.request("POST", "/tasks", { body: task, idempotencyKey });
  }
  getTask(id: string): Promise<Task> {
    return this.request("GET", `/tasks/${encodeURIComponent(id)}`);
  }
  updateTask(id: string, patch: Partial<Task>): Promise<Task> {
    return this.request("PATCH", `/tasks/${encodeURIComponent(id)}`, { body: patch });
  }
  transitionTask(id: string, to: TaskStatus, note?: string): Promise<Task> {
    return this.request("POST", `/tasks/${encodeURIComponent(id)}/transition`, { body: { to, note } });
  }
  assignTask(id: string, assignee: { user_id?: string; agent_id?: string }): Promise<Task> {
    return this.request("POST", `/tasks/${encodeURIComponent(id)}/assign`, { body: assignee });
  }
  listTaskComments(id: string): Promise<Page<TaskComment>> {
    return this.request("GET", `/tasks/${encodeURIComponent(id)}/comments`);
  }
  addTaskComment(id: string, body: string): Promise<TaskComment> {
    return this.request("POST", `/tasks/${encodeURIComponent(id)}/comments`, { body: { body } });
  }
  delegateTask(id: string, agent_id?: string): Promise<{ run_id: string }> {
    return this.request("POST", `/tasks/${encodeURIComponent(id)}/delegate`, { body: agent_id ? { agent_id } : {} });
  }
  listTaskTransitions(id: string): Promise<TaskTransition[]> {
    return this.request<TaskTransition[]>("GET", `/tasks/${encodeURIComponent(id)}/transitions`).catch(() =>
      this.request<Page<TaskTransition>>("GET", `/tasks/${encodeURIComponent(id)}/transitions`).then((p) => p.items),
    );
  }
  getTaskOutcome(id: string): Promise<TaskOutcome> {
    return this.request("GET", `/tasks/${encodeURIComponent(id)}/outcome`);
  }

  /* ---------- workflows ---------- */
  listWorkflowDefinitions(): Promise<Page<WorkflowDefinition>> {
    return this.request("GET", "/workflows/definitions");
  }
  createWorkflowDefinition(def: Partial<WorkflowDefinition>): Promise<WorkflowDefinition> {
    return this.request("POST", "/workflows/definitions", { body: def });
  }
  publishWorkflowVersion(definitionId: string, dag: unknown): Promise<WorkflowVersion> {
    return this.request("POST", `/workflows/definitions/${encodeURIComponent(definitionId)}/versions`, { body: { dag } });
  }
  getWorkflowVersion(definitionId: string, version: number | string): Promise<WorkflowVersion> {
    return this.request("GET", `/workflows/definitions/${encodeURIComponent(definitionId)}/versions/${version}`);
  }
  startWorkflowExecution(definition_id: string, version: number | string | undefined, input: unknown, idempotencyKey?: string): Promise<WorkflowExecution> {
    return this.request("POST", "/workflows/executions", {
      body: { definition_id, version, input },
      idempotencyKey,
    });
  }
  getWorkflowExecution(id: string): Promise<WorkflowExecution> {
    return this.request("GET", `/workflows/executions/${encodeURIComponent(id)}`);
  }
  signalWorkflowExecution(id: string, signal: unknown): Promise<WorkflowExecution> {
    return this.request("POST", `/workflows/executions/${encodeURIComponent(id)}/signal`, { body: { signal } });
  }
  cancelWorkflowExecution(id: string, reason?: string): Promise<WorkflowExecution> {
    return this.request("POST", `/workflows/executions/${encodeURIComponent(id)}/cancel`, { body: { reason } });
  }
  listWorkflowExecutionEvents(id: string): Promise<Page<ExecutionEvent> | ExecutionEvent[]> {
    return this.request("GET", `/workflows/executions/${encodeURIComponent(id)}/events`);
  }
  /** Executions are started via POST; listing executions is not in API.md §6 — see note. */
  listWorkflowExecutions(params?: { definition_id?: string; status?: string; page_size?: number; page_token?: string }): Promise<Page<WorkflowExecution>> {
    // API.md §6 documents no list-executions endpoint; callers must handle 404 honestly.
    return this.request("GET", "/workflows/executions", { query: params });
  }

  /* ---------- agents ---------- */
  listAgents(params?: { capability?: string; status?: AgentStatus | string }): Promise<Page<Agent>> {
    return this.request("GET", "/agents", { query: params });
  }
  registerAgent(agent: { name: string; description?: string }): Promise<Agent> {
    return this.request("POST", "/agents", { body: agent });
  }
  getAgent(id: string): Promise<Agent & { versions?: AgentVersion[] }> {
    return this.request("GET", `/agents/${encodeURIComponent(id)}`);
  }
  publishAgentVersion(id: string, version: Partial<AgentVersion>): Promise<AgentVersion> {
    return this.request("POST", `/agents/${encodeURIComponent(id)}/versions`, { body: version });
  }
  pauseAgent(id: string): Promise<Agent> {
    return this.request("POST", `/agents/${encodeURIComponent(id)}/pause`, {});
  }
  resumeAgent(id: string): Promise<Agent> {
    return this.request("POST", `/agents/${encodeURIComponent(id)}/resume`, {});
  }
  getAgentRun(runId: string): Promise<AgentRun> {
    return this.request("GET", `/agents/runs/${encodeURIComponent(runId)}`);
  }
  cancelAgentRun(runId: string): Promise<void> {
    return this.request("POST", `/agents/runs/${encodeURIComponent(runId)}/cancel`, {});
  }

  /* ---------- tools ---------- */
  listTools(params?: { capability?: string; risk_tier?: string }): Promise<Page<Tool>> {
    return this.request("GET", "/tools", { query: params });
  }
  getTool(name: string): Promise<Tool> {
    return this.request("GET", `/tools/${encodeURIComponent(name)}`);
  }
  invokeTool(name: string, args: unknown): Promise<unknown> {
    return this.request("POST", `/tools/${encodeURIComponent(name)}/invoke`, { body: { args } });
  }
  listToolCalls(params?: { task?: string; agent?: string; tool?: string }): Promise<Page<unknown>> {
    return this.request("GET", "/tools/calls", { query: params });
  }

  /* ---------- approvals ---------- */
  listApprovals(params?: { requester?: string; action?: string; status?: ApprovalStatus | string; page_size?: number; page_token?: string }): Promise<Page<Approval>> {
    return this.request("GET", "/approvals", { query: params });
  }
  getApproval(id: string): Promise<Approval> {
    return this.request("GET", `/approvals/${encodeURIComponent(id)}`);
  }
  decideApproval(id: string, approved: boolean, note?: string): Promise<Approval> {
    return this.request("POST", `/approvals/${encodeURIComponent(id)}/decide`, { body: { approved, note } });
  }
  escalateApproval(id: string, to?: string): Promise<Approval> {
    return this.request("POST", `/approvals/${encodeURIComponent(id)}/escalate`, { body: to ? { to } : {} });
  }

  /* ---------- budgets ---------- */
  listBudgets(): Promise<Page<Budget> | Budget[]> {
    return this.request("GET", "/budgets");
  }
  upsertBudget(budget: Partial<Budget>): Promise<Budget> {
    return this.request("POST", "/budgets", { body: budget });
  }
  getBudgetLedger(id: string, params?: { task?: string; model?: string; date?: string }): Promise<Page<BudgetLedgerEntry>> {
    return this.request("GET", `/budgets/${encodeURIComponent(id)}/ledger`, { query: params });
  }
  killSwitch(): Promise<void> {
    return this.request("POST", "/budgets/kill-switch", {});
  }
  releaseKillSwitch(): Promise<void> {
    return this.request("POST", "/budgets/kill-switch/release", {});
  }
  listBudgetAlerts(): Promise<Page<BudgetAlert> | BudgetAlert[]> {
    return this.request("GET", "/budgets/alerts");
  }

  /* ---------- audit ---------- */
  listAuditEntries(params?: { actor?: string; action?: string; date?: string; page_size?: number; page_token?: string }): Promise<Page<AuditEntry>> {
    return this.request("GET", "/audit/entries", { query: params });
  }
  getAuditEntry(seq: number): Promise<AuditEntry> {
    return this.request("GET", `/audit/entries/${seq}`);
  }
  verifyAuditChain(from_seq?: number): Promise<{ ok: boolean; checked: number; first_bad_seq?: number | null }> {
    return this.request("POST", "/audit/verify", { body: from_seq !== undefined ? { from_seq } : {} });
  }

  /* ---------- command center ---------- */
  getCommandSummary(): Promise<CommandSummary> {
    return this.request("GET", "/command-center/summary");
  }
  listGoals(params?: { status?: string }): Promise<Page<Goal> | Goal[]> {
    return this.request("GET", "/command-center/goals", { query: params });
  }
  listOutcomes(params?: { type?: string; date?: string }): Promise<Page<Outcome> | Outcome[]> {
    return this.request("GET", "/command-center/outcomes", { query: params });
  }
  getCostBreakdown(): Promise<CostBreakdownItem[] | { items: CostBreakdownItem[] }> {
    return this.request("GET", "/command-center/costs");
  }
  getPolicyActivity(): Promise<PolicyActivityItem[]> {
    return this.request("GET", "/command-center/policy-activity");
  }
  /**
   * NL intent query — NOT in API.md (2026-09-15). Command Center page renders
   * this as visibly Planned/disabled until the contract lands.
   */
  intentQuery(_query: string): Promise<never> {
    return Promise.reject(
      new ApiClientError(501, {
        code: "not_implemented",
        message: "NL intent query is not in the API contract yet (API.md has no /command-center intent endpoint).",
      }),
    );
  }

  /* ---------- billing ---------- */
  listBillingPlans(): Promise<Page<BillingPlan> | BillingPlan[]> {
    return this.request("GET", "/billing/plans");
  }
  getSubscription(): Promise<Subscription> {
    return this.request("GET", "/billing/subscription");
  }
  changePlan(plan_id: string): Promise<Subscription> {
    return this.request("POST", "/billing/subscription", { body: { plan_id } });
  }
  getUsage(): Promise<Page<UsageItem> | UsageItem[]> {
    return this.request("GET", "/billing/usage");
  }
  listBillingInvoices(): Promise<Page<BillingInvoice> | BillingInvoice[]> {
    return this.request("GET", "/billing/invoices");
  }
  setSpendCap(cap_usd: number): Promise<{ cap_usd: number }> {
    return this.request("POST", "/billing/spend-caps", { body: { cap_usd } });
  }
}

function asPage<T>(v: Page<T> | T[]): Page<T> {
  return Array.isArray(v) ? { items: v, next_page_token: null } : v;
}

export const api = new AziendaClient();
export { asPage };
export type { AgentRunStep };
