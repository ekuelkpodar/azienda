import { useState } from "react";
import { api, asPage } from "../api/client";
import { useApi, useMutation } from "../api/hooks";
import {
  Badge,
  Button,
  Card,
  DataTable,
  EmptyState,
  Field,
  Input,
  Modal,
  PageHeader,
  PlannedBadge,
  QueryView,
  Select,
  Tabs,
  useToast,
} from "../components/ui";

type Tab = "users" | "roles" | "budgets" | "audit" | "agents";

function InviteUser({ onDone }: { onDone: () => void }) {
  const { push } = useToast();
  const roles = useApi(() => api.listRoles());
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState("member");
  const invite = useMutation((b: { email: string; display_name: string; role: string }) => api.inviteUser(b.email, b.display_name, b.role));
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const u = await invite.mutate({ email, display_name: name, role });
    if (u) { push("success", `Invited ${email}.`); onDone(); }
  }
  return (
    <form onSubmit={submit} className="space-y-4">
      <Field label="Email"><Input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
      <Field label="Display name"><Input required value={name} onChange={(e) => setName(e.target.value)} /></Field>
      <Field label="Role">
        <Select value={role} onChange={(e) => setRole(e.target.value)}>
          {roles.data?.map((r) => (
            <option key={r.id} value={r.name}>{r.name}</option>
          )) ?? (
            <>
              <option value="admin">admin</option>
              <option value="member">member</option>
              <option value="viewer">viewer</option>
            </>
          )}
        </Select>
      </Field>
      {invite.error && <p className="text-sm text-red-700" role="alert">{invite.error.body.message}</p>}
      <Button type="submit" variant="primary" disabled={invite.loading}>{invite.loading ? "Inviting…" : "Send invite"}</Button>
    </form>
  );
}

function Users() {
  const [showInvite, setShowInvite] = useState(false);
  const { push } = useToast();
  const users = useApi(() => api.listUsers().then(asPage));
  const update = useMutation(({ id, patch }: { id: string; patch: Parameters<typeof api.updateUser>[1] }) => api.updateUser(id, patch));

  async function toggleActive(id: string, isActive: boolean) {
    const u = await update.mutate({ id, patch: { is_active: !isActive } });
    if (u) { push("success", isActive ? "User deactivated." : "User reactivated."); users.reload(); }
    else push("error", update.error?.body.message ?? "Update failed.");
  }

  return (
    <div>
      <div className="mb-4 flex justify-end">
        <Button variant="primary" size="sm" onClick={() => setShowInvite(true)}>Invite user</Button>
      </div>
      <QueryView query={users} context="GET /tenants/me/users">
        {(page) => (
          <DataTable
            columns={[
              { key: "name", header: "User", render: (u) => <span className="font-medium">{u.display_name}</span> },
              { key: "email", header: "Email", render: (u) => u.email },
              { key: "roles", header: "Roles", render: (u) => u.roles.map((r) => <Badge key={r}>{r}</Badge>) },
              { key: "active", header: "Active", render: (u) => <Badge tone={u.is_active ? "green" : "neutral"}>{u.is_active ? "yes" : "no"}</Badge> },
              {
                key: "actions",
                header: "Actions",
                render: (u) => (
                  <span onClick={(e) => e.stopPropagation()}>
                    <Button size="sm" variant={u.is_active ? "danger" : "secondary"} onClick={() => void toggleActive(u.id, u.is_active)} disabled={update.loading}>
                      {u.is_active ? "Deactivate" : "Reactivate"}
                    </Button>
                  </span>
                ),
              },
            ]}
            rows={page.items}
            caption="Tenant users"
          />
        )}
      </QueryView>
      {showInvite && (
        <Modal title="Invite user" onClose={() => setShowInvite(false)}>
          <InviteUser onDone={() => { setShowInvite(false); users.reload(); }} />
        </Modal>
      )}
    </div>
  );
}

function Roles() {
  const roles = useApi(() => api.listRoles());
  return (
    <QueryView query={roles} context="GET /tenants/me/roles">
      {(list) => (
        <DataTable
          columns={[
            { key: "name", header: "Role", render: (r) => <span className="font-medium">{r.name}</span> },
            { key: "perms", header: "Permissions", render: (r) => <pre className="max-w-md overflow-auto text-xs">{JSON.stringify(r.permissions)}</pre> },
          ]}
          rows={list.map((r) => ({ ...r }))}
          caption="Role definitions"
        />
      )}
    </QueryView>
  );
}

function Budgets() {
  const { push } = useToast();
  const budgets = useApi(() => api.listBudgets().then(asPage));
  const [showNew, setShowNew] = useState(false);
  const [name, setName] = useState("");
  const [credits, setCredits] = useState("100");
  const [period, setPeriod] = useState("monthly");
  const upsert = useMutation((b: Parameters<typeof api.upsertBudget>[0]) => api.upsertBudget(b));
  const kill = useMutation(() => api.killSwitch());
  const release = useMutation(() => api.releaseKillSwitch());
  const alerts = useApi(() => api.listBudgetAlerts().then(asPage));
  const [ledgerFor, setLedgerFor] = useState<string | null>(null);
  const ledger = useApi(() => (ledgerFor ? api.getBudgetLedger(ledgerFor).then(asPage) : Promise.resolve(null)), [ledgerFor ?? ""]);

  async function onCreate(e: React.FormEvent) {
    e.preventDefault();
    const b = await upsert.mutate({ name, credits: Number(credits), period });
    if (b) { push("success", "Budget saved."); setShowNew(false); setName(""); budgets.reload(); }
    else push("error", upsert.error?.body.message ?? "Failed to save budget.");
  }
  async function onKill() {
    if (!window.confirm("Freeze ALL new agent spend immediately? This is audited.")) return;
    const r = await kill.mutate(undefined);
    if (r !== null || !kill.error) { push("success", "Kill switch engaged — new agent spend frozen."); }
    else push("error", kill.error?.body.message ?? "Kill switch failed.");
  }
  async function onRelease() {
    const r = await release.mutate(undefined);
    if (r !== null || !release.error) push("success", "Kill switch released.");
    else push("error", release.error?.body.message ?? "Release failed.");
  }

  return (
    <div className="space-y-6">
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500">Kill switch</h2>
            <p className="mt-1 text-xs text-neutral-500">Freezes new agent spend immediately (admin, audited). Fail closed on budget exhaustion.</p>
          </div>
          <div className="flex gap-2">
            <Button variant="danger" size="sm" onClick={() => void onKill()} disabled={kill.loading}>Engage kill switch</Button>
            <Button size="sm" onClick={() => void onRelease()} disabled={release.loading}>Release</Button>
          </div>
        </div>
      </Card>

      <div>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500">Budgets</h2>
          <Button variant="primary" size="sm" onClick={() => setShowNew((s) => !s)}>New budget</Button>
        </div>
        {showNew && (
          <Card className="mb-4">
            <form onSubmit={(e) => void onCreate(e)} className="flex flex-wrap items-end gap-3">
              <Field label="Name"><Input required value={name} onChange={(e) => setName(e.target.value)} placeholder="Agent execution" /></Field>
              <Field label="Credits"><Input type="number" min="0" value={credits} onChange={(e) => setCredits(e.target.value)} /></Field>
              <Field label="Period">
                <Select value={period} onChange={(e) => setPeriod(e.target.value)}>
                  <option value="daily">Daily</option>
                  <option value="weekly">Weekly</option>
                  <option value="monthly">Monthly</option>
                </Select>
              </Field>
              <Button type="submit" variant="primary" size="sm" disabled={upsert.loading}>Save</Button>
            </form>
          </Card>
        )}
        <QueryView query={budgets} context="GET /budgets">
          {(page) =>
            page.items.length === 0 ? (
              <EmptyState title="No budgets yet" hint="Create one above — every model call is metered against these." />
            ) : (
              <DataTable
                columns={[
                  { key: "name", header: "Budget", render: (b) => <span className="font-medium">{b.name}</span> },
                  { key: "period", header: "Period", render: (b) => b.period },
                  { key: "credits", header: "Credits", render: (b) => <span className="tabular-nums">{b.credits}</span> },
                  { key: "burn", header: "Burn", render: (b) => <span className="tabular-nums">{b.burn ?? "—"}</span> },
                  {
                    key: "actions",
                    header: "Ledger",
                    render: (b) => (
                      <span onClick={(e) => e.stopPropagation()}>
                        <Button size="sm" variant="ghost" onClick={() => setLedgerFor(ledgerFor === b.id ? null : b.id)}>View ledger</Button>
                      </span>
                    ),
                  },
                ]}
                rows={page.items}
                caption="Tenant budgets"
              />
            )
          }
        </QueryView>
        {ledgerFor && (
          <Card className="mt-4">
            <h3 className="mb-2 text-sm font-semibold">Cost ledger</h3>
            {ledger.loading ? <p className="text-sm text-neutral-500">Loading…</p> :
              ledger.error ? <p className="text-sm text-red-700">Unavailable ({ledger.error.code}).</p> :
              !ledger.data || ledger.data.items.length === 0 ? <p className="text-sm text-neutral-500">No entries.</p> :
              (
                <DataTable
                  columns={[
                    { key: "at", header: "When", render: (e) => new Date(e.created_at).toLocaleString() },
                    { key: "task", header: "Task", render: (e) => (e.task_id ? <code className="text-xs">{e.task_id.slice(0, 8)}…</code> : "—") },
                    { key: "model", header: "Model", render: (e) => e.model ?? "—" },
                    { key: "amount", header: "Amount", render: (e) => <span className="tabular-nums">${Number(e.amount_usd).toFixed(4)}</span> },
                    { key: "reason", header: "Reason", render: (e) => e.reason ?? "—" },
                  ]}
                  rows={ledger.data.items}
                  caption="Budget ledger entries"
                />
              )}
          </Card>
        )}
      </div>

      <div>
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-neutral-500">Spend alerts</h2>
        <QueryView query={alerts} context="GET /budgets/alerts">
          {(page) =>
            page.items.length === 0 ? (
              <p className="text-sm text-neutral-500">No alerts — spend is within thresholds.</p>
            ) : (
              <ul className="space-y-2">
                {page.items.map((a) => (
                  <li key={a.id} className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
                    {a.message} <span className="text-xs">({a.threshold}% · {new Date(a.created_at).toLocaleString()})</span>
                  </li>
                ))}
              </ul>
            )
          }
        </QueryView>
      </div>
    </div>
  );
}

function AuditLog() {
  const [actor, setActor] = useState("");
  const [action, setAction] = useState("");
  const entries = useApi(() => api.listAuditEntries({ actor: actor || undefined, action: action || undefined }).then(asPage), []);
  const { push } = useToast();
  const verify = useMutation(() => api.verifyAuditChain());

  async function onVerify() {
    const r = await verify.mutate(undefined);
    if (r) push(r.ok ? "success" : "error", r.ok ? `Chain verified — ${r.checked} entries.` : `Chain BROKEN at seq ${r.first_bad_seq}.`);
    else push("error", verify.error?.body.message ?? "Verification failed.");
  }

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-end gap-2">
        <Field label="Actor filter"><Input value={actor} onChange={(e) => setActor(e.target.value)} placeholder="user / agent id" /></Field>
        <Field label="Action filter"><Input value={action} onChange={(e) => setAction(e.target.value)} placeholder="task.created" /></Field>
        <Button size="sm" onClick={() => entries.reload()}>Filter</Button>
        <Button size="sm" variant="secondary" onClick={() => void onVerify()} disabled={verify.loading}>Verify chain</Button>
      </div>
      <QueryView query={entries} context="GET /audit/entries">
        {(page) =>
          page.items.length === 0 ? (
            <EmptyState title="No entries match" hint="Try clearing the filters." />
          ) : (
            <DataTable
              columns={[
                { key: "seq", header: "Seq", render: (e) => <span className="font-mono tabular-nums">{e.seq}</span> },
                { key: "at", header: "When", render: (e) => new Date(e.created_at).toLocaleString() },
                { key: "actor", header: "Actor", render: (e) => e.actor ?? "—" },
                { key: "action", header: "Action", render: (e) => <code className="text-xs">{e.action}</code> },
                { key: "hash", header: "Hash", render: (e) => (e.hash ? <code className="text-xs" title={e.hash}>{e.hash.slice(0, 10)}…</code> : "—") },
              ]}
              rows={page.items.map((e) => ({ ...e, id: e.seq }))}
              caption="Audit ledger (append-only)"
            />
          )
        }
      </QueryView>
      <p className="mt-2 text-xs text-neutral-500">Entries are immutable — the ledger is the source of truth for every consequential action.</p>
    </div>
  );
}

function AgentConfig() {
  const tools = useApi(() => api.listTools().then(asPage));
  return (
    <div>
      <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-neutral-500">Tool registry</h2>
      <p className="mb-3 text-xs text-neutral-500">
        Every MCP/server tool the workforce can call — rail-gated, risk-tiered, no ambient authority.
      </p>
      <QueryView query={tools} context="GET /tools">
        {(page) =>
          page.items.length === 0 ? (
            <EmptyState title="No tools registered" hint="" />
          ) : (
            <DataTable
              columns={[
                { key: "name", header: "Tool", render: (t) => <code className="text-xs font-medium">{t.name}</code> },
                { key: "desc", header: "Description", render: (t) => <span className="text-neutral-500">{t.description ?? "—"}</span> },
                { key: "cap", header: "Capability", render: (t) => t.capability ?? "—" },
                {
                  key: "risk",
                  header: "Risk tier",
                  render: (t) => (
                    <Badge tone={t.risk_tier === "high" ? "red" : t.risk_tier === "medium" ? "amber" : "neutral"}>
                      {t.risk_tier ?? "n/a"}
                    </Badge>
                  ),
                },
              ]}
              rows={page.items.map((t) => ({ ...t, id: t.name }))}
              caption="Tool registry"
            />
          )
        }
      </QueryView>
    </div>
  );
}

export default function Admin() {
  const [tab, setTab] = useState<Tab>("users");
  return (
    <div>
      <PageHeader
        title="Admin"
        subtitle="Tenant administration: users, roles, budgets, audit, and agent configuration."
        actions={
          <span className="flex items-center gap-2">
            <PlannedBadge reason="Policy authoring UI is not in the API contract yet — policies are managed server-side." />
            <span className="text-xs text-neutral-500">Policies</span>
          </span>
        }
      />
      <Tabs
        tabs={[
          { key: "users", label: "Users" },
          { key: "roles", label: "Roles" },
          { key: "budgets", label: "Budgets" },
          { key: "audit", label: "Audit log" },
          { key: "agents", label: "Agent config" },
        ]}
        active={tab}
        onChange={setTab}
      />
      {tab === "users" && <Users />}
      {tab === "roles" && <Roles />}
      {tab === "budgets" && <Budgets />}
      {tab === "audit" && <AuditLog />}
      {tab === "agents" && <AgentConfig />}
    </div>
  );
}
