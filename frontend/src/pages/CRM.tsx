import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, asPage } from "../api/client";
import { FIRST_RUN_FLAG } from "./Onboarding";
import { useApi, useMutation } from "../api/hooks";
import type { Lead, Opportunity, Pipeline } from "../api/types";
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
  QueryView,
  Select,
  Tabs,
  TextArea,
  useToast,
} from "../components/ui";

type Tab = "leads" | "deals" | "contacts" | "organizations";

const LEAD_TONE: Record<string, "green" | "amber" | "neutral" | "blue" | "red" | "purple"> = {
  new: "blue",
  contacted: "amber",
  qualified: "green",
  unqualified: "neutral",
  converted: "purple",
};

/* ---------------- Leads ---------------- */

function LeadForm({ onDone }: { onDone: () => void }) {
  const { push } = useToast();
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [email, setEmail] = useState("");
  const [source, setSource] = useState("website");
  const create = useMutation((b: Parameters<typeof api.createLead>[0]) => api.createLead(b));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const lead = await create.mutate({
      source,
      status: "new",
      custom: { first_name: firstName, last_name: lastName, email },
    } as Parameters<typeof api.createLead>[0]);
    if (lead) {
      push("success", "Lead created.");
      onDone();
    }
  }
  return (
    <form onSubmit={submit} className="space-y-4">
      <div className="grid grid-cols-2 gap-3">
        <Field label="First name"><Input required value={firstName} onChange={(e) => setFirstName(e.target.value)} /></Field>
        <Field label="Last name"><Input required value={lastName} onChange={(e) => setLastName(e.target.value)} /></Field>
      </div>
      <Field label="Email"><Input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
      <Field label="Source">
        <Select value={source} onChange={(e) => setSource(e.target.value)}>
          <option value="website">Website</option>
          <option value="referral">Referral</option>
          <option value="outbound">Outbound</option>
          <option value="partner">Partner</option>
        </Select>
      </Field>
      {create.error && <p className="text-sm text-red-700" role="alert">{create.error.body.message}</p>}
      <Button type="submit" variant="primary" disabled={create.loading}>
        {create.loading ? "Creating…" : "Create lead"}
      </Button>
    </form>
  );
}

/** Signature demo 1: lead → score → outreach draft → approval → follow-up task. */
function StartLeadPipeline({ lead, pipelines }: { lead: Lead; pipelines: Pipeline[] }) {
  const { push } = useToast();
  const navigate = useNavigate();
  const start = useMutation(
    async (): Promise<string | null> => {
      const defs = await api.listWorkflowDefinitions().then(asPage);
      const def = defs.items.find((d) => d.name === "new-lead-pipeline" && d.is_active);
      if (!def) return null;
      const ex = await api.startWorkflowExecution(def.id, undefined, { lead_id: lead.id });
      try { localStorage.setItem(FIRST_RUN_FLAG, "1"); } catch { /* ignore */ }
      return ex.id;
    },
  );

  async function run() {
    const id = await start.mutate(undefined);
    if (id) {
      push("success", "Lead pipeline started. Watch the workflow run, then check Approvals for the outreach draft.");
      navigate("/workflows");
    } else if (!start.error) {
      push("error", "The 'new-lead-pipeline' workflow definition is not seeded yet (run seed_demo.py).");
    } else {
      push("error", start.error.body.message);
    }
  }

  if (pipelines.length === 0) return null;
  return (
    <Button size="sm" variant="primary" onClick={() => void run()} disabled={start.loading}>
      {start.loading ? "Starting…" : "▶ Start lead pipeline"}
    </Button>
  );
}

function Leads({ pipelines }: { pipelines: Pipeline[] }) {
  const [status, setStatus] = useState("");
  const [showNew, setShowNew] = useState(false);
  const leads = useApi(() => api.listLeads(status ? { status } : undefined).then(asPage), [status]);
  const { push } = useToast();
  const convert = useMutation((id: string) => api.convertLead(id));

  async function onConvert(id: string) {
    const res = await convert.mutate(id);
    if (res) {
      push("success", "Lead converted to organization + contact + opportunity.");
      leads.reload();
    } else {
      push("error", convert.error?.body.message ?? "Convert failed.");
    }
  }

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <Select aria-label="Filter by lead status" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All statuses</option>
          {["new", "contacted", "qualified", "unqualified", "converted"].map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </Select>
        <Button variant="primary" size="sm" onClick={() => setShowNew(true)}>New lead</Button>
      </div>
      <QueryView query={leads} context="GET /crm/leads">
        {(page) =>
          page.items.length === 0 ? (
            <EmptyState title="No leads" hint="Leads flow in from the website, referrals, and outbound. The seeded demo includes several." />
          ) : (
            <DataTable
              columns={[
                { key: "id", header: "Lead", render: (l) => <code className="text-xs">{l.id.slice(0, 8)}…</code> },
                { key: "source", header: "Source", render: (l) => l.source ?? "—" },
                { key: "status", header: "Status", render: (l) => <Badge tone={LEAD_TONE[l.status] ?? "neutral"}>{l.status}</Badge> },
                {
                  key: "score",
                  header: "Score",
                  render: (l) => (l.score == null ? "—" : <span className="font-semibold tabular-nums">{l.score}</span>),
                },
                {
                  key: "actions",
                  header: "Actions",
                  render: (l) => (
                    <div className="flex flex-wrap gap-2" onClick={(e) => e.stopPropagation()}>
                      <StartLeadPipeline lead={l} pipelines={pipelines} />
                      {l.status !== "converted" && (
                        <Button size="sm" onClick={() => void onConvert(l.id)} disabled={convert.loading}>
                          Convert
                        </Button>
                      )}
                    </div>
                  ),
                },
              ]}
              rows={page.items}
              caption="Leads"
            />
          )
        }
      </QueryView>
      {showNew && (
        <Modal title="New lead" onClose={() => setShowNew(false)}>
          <LeadForm onDone={() => { setShowNew(false); leads.reload(); }} />
        </Modal>
      )}
    </div>
  );
}

/* ---------------- Deals (kanban) ---------------- */

function Deals({ pipelines }: { pipelines: Pipeline[] }) {
  const [pipelineId, setPipelineId] = useState(pipelines[0]?.id ?? "");
  const deals = useApi(
    () => api.listOpportunities(pipelineId ? { pipeline: pipelineId } : undefined).then(asPage),
    [pipelineId],
  );
  const { push } = useToast();
  const move = useMutation(({ id, stage_id }: { id: string; stage_id: string }) => api.moveOpportunity(id, stage_id));

  const pipeline = pipelines.find((p) => p.id === pipelineId) ?? pipelines[0];
  const stages = pipeline?.stages ?? [];

  async function onMove(deal: Opportunity, stageId: string) {
    const res = await move.mutate({ id: deal.id, stage_id: stageId });
    if (res) {
      push("success", `Moved to ${stages.find((s) => s.id === stageId)?.name ?? "stage"}.`);
      deals.reload();
    } else {
      push("error", move.error?.body.message ?? "Move failed (stage transitions are validated server-side).");
    }
  }

  if (pipelines.length === 0)
    return <EmptyState title="No pipelines" hint="Create a pipeline first (seed_demo.py seeds a default sales pipeline)." />;

  return (
    <div>
      <div className="mb-4">
        <Select aria-label="Pipeline" value={pipelineId} onChange={(e) => setPipelineId(e.target.value)}>
          {pipelines.map((p) => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </Select>
      </div>
      <QueryView query={deals} context="GET /crm/opportunities">
        {(page) => (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            {stages.map((stage) => {
              const cards = page.items.filter((d) => d.stage_id === stage.id);
              const total = cards.reduce((a, c) => a + Number(c.amount ?? 0), 0);
              return (
                <div key={stage.id} className="rounded-xl border border-neutral-200 bg-neutral-50 p-3">
                  <div className="mb-1 flex items-center justify-between">
                    <h3 className="text-sm font-semibold">{stage.name}</h3>
                    <Badge tone="neutral">{cards.length}</Badge>
                  </div>
                  <p className="mb-3 text-xs tabular-nums text-neutral-500">
                    ${total.toLocaleString()} · {Math.round(stage.probability * 100)}%
                  </p>
                  <div className="space-y-2">
                    {cards.map((d) => (
                      <div key={d.id} className="rounded-lg border border-neutral-200 bg-white p-3 text-sm shadow-sm">
                        <p className="font-medium">{d.name}</p>
                        <p className="mt-0.5 text-xs tabular-nums text-neutral-500">
                          {d.amount != null ? `$${Number(d.amount).toLocaleString()} ${d.currency}` : "No amount"}
                          {d.close_date ? ` · closes ${d.close_date}` : ""}
                        </p>
                        <div className="mt-2 flex flex-wrap gap-1">
                          {stages
                            .filter((s) => s.id !== stage.id)
                            .map((s) => (
                              <Button key={s.id} size="sm" variant="ghost" onClick={() => void onMove(d, s.id)} disabled={move.loading} title={`Move to ${s.name}`}>
                                → {s.name}
                              </Button>
                            ))}
                        </div>
                      </div>
                    ))}
                    {cards.length === 0 && <p className="text-xs text-neutral-400">Empty</p>}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </QueryView>
    </div>
  );
}

/* ---------------- Contacts & Organizations ---------------- */

function ContactForm({ onDone }: { onDone: () => void }) {
  const { push } = useToast();
  const [f, setF] = useState({ first_name: "", last_name: "", email: "", phone: "", title: "" });
  const create = useMutation((b: Parameters<typeof api.createContact>[0]) => api.createContact(b));
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const c = await create.mutate({ ...f, tags: [] });
    if (c) { push("success", "Contact created."); onDone(); }
  }
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });
  return (
    <form onSubmit={submit} className="space-y-4">
      <div className="grid grid-cols-2 gap-3">
        <Field label="First name"><Input required value={f.first_name} onChange={set("first_name")} /></Field>
        <Field label="Last name"><Input required value={f.last_name} onChange={set("last_name")} /></Field>
      </div>
      <Field label="Email"><Input type="email" value={f.email} onChange={set("email")} /></Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Phone"><Input value={f.phone} onChange={set("phone")} /></Field>
        <Field label="Title"><Input value={f.title} onChange={set("title")} /></Field>
      </div>
      {create.error && <p className="text-sm text-red-700" role="alert">{create.error.body.message}</p>}
      <Button type="submit" variant="primary" disabled={create.loading}>{create.loading ? "Creating…" : "Create contact"}</Button>
    </form>
  );
}

function Contacts() {
  const [search, setSearch] = useState("");
  const [showNew, setShowNew] = useState(false);
  const [debounced, setDebounced] = useState("");
  const contacts = useApi(() => api.listContacts(debounced ? { search: debounced } : undefined).then(asPage), [debounced]);

  return (
    <div>
      <div className="mb-4 flex items-center justify-between gap-2">
        <Input
          aria-label="Search contacts"
          placeholder="Search contacts…"
          value={search}
          onChange={(e) => { setSearch(e.target.value); }}
          onKeyDown={(e) => { if (e.key === "Enter") setDebounced(search); }}
          className="max-w-xs"
        />
        <Button variant="primary" size="sm" onClick={() => setShowNew(true)}>New contact</Button>
      </div>
      <QueryView query={contacts} context="GET /crm/contacts">
        {(page) =>
          page.items.length === 0 ? (
            <EmptyState title="No contacts" hint="Contacts belong to organizations and can carry tags." />
          ) : (
            <DataTable
              columns={[
                { key: "name", header: "Name", render: (c) => <span className="font-medium">{c.first_name} {c.last_name}</span> },
                { key: "email", header: "Email", render: (c) => c.email ?? "—" },
                { key: "phone", header: "Phone", render: (c) => c.phone ?? "—" },
                { key: "title", header: "Title", render: (c) => c.title ?? "—" },
                { key: "tags", header: "Tags", render: (c) => c.tags.length ? c.tags.map((t) => <Badge key={t}>{t}</Badge>) : "—" },
              ]}
              rows={page.items}
              caption="Contacts"
            />
          )
        }
      </QueryView>
      {showNew && (
        <Modal title="New contact" onClose={() => setShowNew(false)}>
          <ContactForm onDone={() => { setShowNew(false); contacts.reload(); }} />
        </Modal>
      )}
    </div>
  );
}

function Organizations() {
  const [search, setSearch] = useState("");
  const orgs = useApi(() => api.listOrganizations(search ? { search } : undefined).then(asPage), [search]);
  return (
    <div>
      <div className="mb-4">
        <Input aria-label="Search organizations" placeholder="Search organizations…" value={search} onChange={(e) => setSearch(e.target.value)} className="max-w-xs" />
      </div>
      <QueryView query={orgs} context="GET /crm/organizations">
        {(page) =>
          page.items.length === 0 ? (
            <EmptyState title="No organizations" hint="Organizations are the account records deals and contacts attach to." />
          ) : (
            <DataTable
              columns={[
                { key: "name", header: "Organization", render: (o) => <span className="font-medium">{o.name}</span> },
                { key: "domain", header: "Domain", render: (o) => o.domain ?? "—" },
                { key: "industry", header: "Industry", render: (o) => o.industry ?? "—" },
                { key: "stage", header: "Lifecycle", render: (o) => o.lifecycle_stage ? <Badge>{o.lifecycle_stage}</Badge> : "—" },
              ]}
              rows={page.items}
              caption="Organizations"
            />
          )
        }
      </QueryView>
    </div>
  );
}

function ActivityLog() {
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const acts = useApi(() => api.listActivities().then(asPage));
  const { push } = useToast();
  const log = useMutation((b: Parameters<typeof api.logActivity>[0]) => api.logActivity(b));
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const a = await log.mutate({ subject_type: "note", subject_id: subject || "general", type: "note", body });
    if (a) { push("success", "Activity logged."); setBody(""); acts.reload(); }
  }
  return (
    <div className="mt-8">
      <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-neutral-500">Activity log</h2>
      <Card className="mb-4">
        <form onSubmit={submit} className="flex flex-col gap-3 md:flex-row md:items-end">
          <Field label="Subject id (optional)">
            <Input value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="contact / deal id" className="md:w-48" />
          </Field>
          <Field label="Note">
            <TextArea value={body} onChange={(e) => setBody(e.target.value)} required rows={1} placeholder="Log a call, email, meeting…" className="md:w-96" />
          </Field>
          <Button type="submit" variant="primary" size="sm" disabled={log.loading}>Log activity</Button>
        </form>
      </Card>
      <QueryView query={acts} context="GET /crm/activities">
        {(page) =>
          page.items.length === 0 ? (
            <EmptyState title="No activity yet" hint="Calls, emails, and meetings logged against records appear here." />
          ) : (
            <ul className="space-y-2">
              {page.items.slice(0, 20).map((a) => (
                <li key={a.id} className="rounded-lg border border-neutral-200 bg-white p-3 text-sm">
                  <div className="flex items-center gap-2">
                    <Badge>{a.type}</Badge>
                    <span className="text-xs text-neutral-500">{a.subject_type}:{a.subject_id} · {new Date(a.occurred_at).toLocaleString()}</span>
                  </div>
                  {a.body && <p className="mt-1 text-neutral-800">{a.body}</p>}
                </li>
              ))}
            </ul>
          )
        }
      </QueryView>
    </div>
  );
}

export default function CRM() {
  const [tab, setTab] = useState<Tab>("leads");
  const pipelines = useApi(() => api.listPipelines().then(asPage));

  return (
    <div>
      <PageHeader title="CRM" subtitle="Leads, deals, contacts, and organizations." />
      <Tabs
        tabs={[
          { key: "leads", label: "Leads" },
          { key: "deals", label: "Deals" },
          { key: "contacts", label: "Contacts" },
          { key: "organizations", label: "Organizations" },
        ]}
        active={tab}
        onChange={setTab}
      />
      <QueryView query={pipelines} context="GET /crm/pipelines">
        {(pp) => (
          <>
            {tab === "leads" && <Leads pipelines={pp.items} />}
            {tab === "deals" && <Deals pipelines={pp.items} />}
            {tab === "contacts" && <Contacts />}
            {tab === "organizations" && <Organizations />}
          </>
        )}
      </QueryView>
      <ActivityLog />
    </div>
  );
}
