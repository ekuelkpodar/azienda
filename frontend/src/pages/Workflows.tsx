import { useState } from "react";
import { api, asPage } from "../api/client";
import { FIRST_RUN_FLAG } from "./Onboarding";
import { useApi, useMutation } from "../api/hooks";
import type { WorkflowDefinition, WorkflowExecution } from "../api/types";
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

type Tab = "definitions" | "executions";

const EX_TONE: Record<string, "green" | "amber" | "neutral" | "blue" | "red"> = {
  pending: "neutral",
  running: "blue",
  waiting_approval: "amber",
  succeeded: "green",
  failed: "red",
  cancelled: "neutral",
};

function DefForm({ onDone }: { onDone: () => void }) {
  const { push } = useToast();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [autonomy, setAutonomy] = useState(2);
  const create = useMutation((b: Parameters<typeof api.createWorkflowDefinition>[0]) => api.createWorkflowDefinition(b));
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const d = await create.mutate({ name, description, autonomy_level: autonomy, is_active: true });
    if (d) { push("success", "Workflow definition created as draft."); onDone(); }
  }
  return (
    <form onSubmit={submit} className="space-y-4">
      <Field label="Name"><Input required value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. new-lead-pipeline" /></Field>
      <Field label="Description"><TextArea value={description} onChange={(e) => setDescription(e.target.value)} rows={2} /></Field>
      <Field label="Autonomy level (L0–L5)" hint="Per-workflow progressive autonomy dial; the governance rail enforces it.">
        <Select value={autonomy} onChange={(e) => setAutonomy(Number(e.target.value))}>
          {[0, 1, 2, 3, 4, 5].map((n) => (
            <option key={n} value={n}>L{n}</option>
          ))}
        </Select>
      </Field>
      {create.error && <p className="text-sm text-red-700" role="alert">{create.error.body.message}</p>}
      <Button type="submit" variant="primary" disabled={create.loading}>{create.loading ? "Creating…" : "Create definition"}</Button>
    </form>
  );
}

function PublishVersion({ def, onDone }: { def: WorkflowDefinition; onDone: () => void }) {
  const { push } = useToast();
  const [dag, setDag] = useState('{\n  "steps": []\n}');
  const publish = useMutation((d: unknown) => api.publishWorkflowVersion(def.id, d));
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    let parsed: unknown;
    try {
      parsed = JSON.parse(dag);
    } catch {
      push("error", "DAG is not valid JSON.");
      return;
    }
    const v = await publish.mutate(parsed);
    if (v) { push("success", `Published version ${v.version} (immutable).`); onDone(); }
  }
  return (
    <form onSubmit={submit} className="space-y-4">
      <Field label="DAG (JSON)" hint="Published versions are immutable; executions pin a version.">
        <TextArea value={dag} onChange={(e) => setDag(e.target.value)} rows={8} className="font-mono text-xs" />
      </Field>
      {publish.error && <p className="text-sm text-red-700" role="alert">{publish.error.body.message}</p>}
      <Button type="submit" variant="primary" disabled={publish.loading}>{publish.loading ? "Publishing…" : "Publish version"}</Button>
    </form>
  );
}

function StartExecution({ def, onDone }: { def: WorkflowDefinition; onDone: (id: string) => void }) {
  const { push } = useToast();
  const [input, setInput] = useState("{}");
  const start = useMutation(async (): Promise<string | null> => {
    let parsed: unknown = {};
    try { parsed = JSON.parse(input); } catch { push("error", "Input is not valid JSON."); return null; }
    const ex = await api.startWorkflowExecution(def.id, undefined, parsed);
    return ex.id;
  });
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const id = await start.mutate(undefined);
    if (id) { push("success", "Execution started."); onDone(id); }
    else if (start.error) push("error", start.error.body.message);
  }
  return (
    <form onSubmit={submit} className="space-y-4">
      <Field label="Input (JSON)">
        <TextArea value={input} onChange={(e) => setInput(e.target.value)} rows={4} className="font-mono text-xs" />
      </Field>
      <Button type="submit" variant="primary" disabled={start.loading}>{start.loading ? "Starting…" : "Start execution"}</Button>
    </form>
  );
}

function ExecutionDetail({ execution, onClose, reload }: { execution: WorkflowExecution; onClose: () => void; reload: () => void }) {
  const { push } = useToast();
  const detail = useApi(() => api.getWorkflowExecution(execution.id), [execution.id]);
  const events = useApi(
    () => api.listWorkflowExecutionEvents(execution.id).then((r) => (Array.isArray(r) ? r : r.items)),
    [execution.id],
  );
  const [signal, setSignal] = useState("");
  const sendSignal = useMutation((s: string) => api.signalWorkflowExecution(execution.id, s));
  const cancel = useMutation(() => api.cancelWorkflowExecution(execution.id, "cancelled from console"));

  async function onSignal(e: React.FormEvent) {
    e.preventDefault();
    const ex = await sendSignal.mutate(signal);
    if (ex) { push("success", "Signal sent."); setSignal(""); detail.reload(); events.reload(); }
    else push("error", sendSignal.error?.body.message ?? "Signal failed.");
  }
  async function onCancel() {
    const ex = await cancel.mutate(undefined);
    if (ex) { push("success", "Execution cancelled."); reload(); detail.reload(); }
    else push("error", cancel.error?.body.message ?? "Cancel failed.");
  }

  const current = detail.data ?? execution;

  return (
    <Modal title={`Execution ${execution.id.slice(0, 8)}…`} onClose={onClose} wide>
      <div className="space-y-4 text-sm">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={EX_TONE[current.status] ?? "neutral"}>{current.status.replace(/_/g, " ")}</Badge>
          {current.cost_usd != null && <span className="tabular-nums text-neutral-600">Cost ${Number(current.cost_usd).toFixed(2)}</span>}
          <Button size="sm" variant="danger" onClick={() => void onCancel()} disabled={cancel.loading}>Cancel</Button>
        </div>
        <form onSubmit={(e) => void onSignal(e)} className="flex gap-2">
          <Input aria-label="Send signal" value={signal} onChange={(e) => setSignal(e.target.value)} placeholder='Signal (e.g. {"approved": true} for HITL steps)' className="font-mono text-xs" />
          <Button type="submit" size="sm" disabled={sendSignal.loading || !signal}>Send</Button>
        </form>
        <div>
          <h3 className="mb-2 font-semibold">Event stream</h3>
          {events.loading ? <p className="text-neutral-500">Loading…</p> :
            events.error ? <p className="text-neutral-500">Events unavailable ({events.error.code}).</p> :
            !events.data || events.data.length === 0 ? <p className="text-neutral-500">No events yet.</p> :
            (
              <ol className="max-h-64 space-y-1 overflow-auto">
                {events.data.map((ev) => (
                  <li key={ev.id} className="rounded-md bg-neutral-50 p-2 text-xs">
                    <span className="font-mono font-medium">#{ev.seq} {ev.event_type}</span>
                    <span className="ml-2 text-neutral-400">{new Date(ev.created_at).toLocaleString()}</span>
                    {ev.payload !== undefined && (
                      <pre className="mt-1 overflow-auto text-neutral-600">{JSON.stringify(ev.payload)}</pre>
                    )}
                  </li>
                ))}
              </ol>
            )}
        </div>
        <div>
          <h3 className="mb-2 font-semibold">State</h3>
          <pre className="max-h-48 overflow-auto rounded-md bg-neutral-50 p-3 text-xs">
            {JSON.stringify({ input: current.input, state: current.state }, null, 2)}
          </pre>
        </div>
      </div>
    </Modal>
  );
}

export default function Workflows() {
  const [tab, setTab] = useState<Tab>("definitions");
  const [showNew, setShowNew] = useState(false);
  const [publishFor, setPublishFor] = useState<WorkflowDefinition | null>(null);
  const [startFor, setStartFor] = useState<WorkflowDefinition | null>(null);
  const [selectedEx, setSelectedEx] = useState<WorkflowExecution | null>(null);
  const [recentRuns, setRecentRuns] = useState<WorkflowExecution[]>([]);

  const defs = useApi(() => api.listWorkflowDefinitions().then(asPage));
  // API.md §6 defines no list-executions endpoint: we track runs started in this session honestly.
  const executionsQuery = useApi(
    () => api.listWorkflowExecutions().then(asPage).catch(() => null),
    [],
  );

  function pushRun(ex: WorkflowExecution) {
    setRecentRuns((r) => [ex, ...r.filter((x) => x.id !== ex.id)]);
  }

  const serverExecutions = executionsQuery.data?.items ?? [];
  const executions = [...recentRuns.filter((r) => !serverExecutions.some((s) => s.id === r.id)), ...serverExecutions];

  return (
    <div>
      <PageHeader
        title="Workflows"
        subtitle="Durable business processes: definitions, immutable versions, executions with event streams."
        actions={
          <Button variant="primary" size="sm" onClick={() => setShowNew(true)}>New definition</Button>
        }
      />
      <Tabs
        tabs={[{ key: "definitions", label: "Definitions" }, { key: "executions", label: "Executions" }]}
        active={tab}
        onChange={setTab}
      />
      {tab === "definitions" && (
        <QueryView query={defs} context="GET /workflows/definitions">
          {(page) =>
            page.items.length === 0 ? (
              <EmptyState title="No workflow definitions" hint="seed_demo.py seeds 'new-lead-pipeline' — the signature lead demo workflow." />
            ) : (
              <DataTable
                columns={[
                  { key: "name", header: "Definition", render: (d) => <span className="font-mono text-xs font-medium">{d.name}</span> },
                  { key: "desc", header: "Description", render: (d) => <span className="text-neutral-500">{d.description ?? "—"}</span> },
                  { key: "autonomy", header: "Autonomy", render: (d) => <Badge>L{d.autonomy_level}</Badge> },
                  { key: "active", header: "Active", render: (d) => <Badge tone={d.is_active ? "green" : "neutral"}>{d.is_active ? "yes" : "no"}</Badge> },
                  {
                    key: "actions",
                    header: "Actions",
                    render: (d) => (
                      <div className="flex gap-2" onClick={(e) => e.stopPropagation()}>
                        <Button size="sm" onClick={() => setPublishFor(d)}>Publish version</Button>
                        <Button size="sm" variant="primary" onClick={() => setStartFor(d)}>Run</Button>
                      </div>
                    ),
                  },
                ]}
                rows={page.items}
                caption="Workflow definitions"
              />
            )
          }
        </QueryView>
      )}
      {tab === "executions" && (
        executions.length === 0 ? (
          <EmptyState
            title="No executions"
            hint="Start a run from a definition. Execution listing beyond this session requires a backend list endpoint (not in API.md §6)."
          />
        ) : (
          <DataTable
            columns={[
              { key: "id", header: "Execution", render: (e) => <code className="text-xs">{e.id.slice(0, 8)}…</code> },
              { key: "def", header: "Definition", render: (e) => <code className="text-xs">{e.definition_id.slice(0, 8)}…</code> },
              { key: "status", header: "Status", render: (e) => <Badge tone={EX_TONE[e.status] ?? "neutral"}>{e.status.replace(/_/g, " ")}</Badge> },
              { key: "cost", header: "Cost", render: (e) => <span className="tabular-nums">{e.cost_usd != null ? `$${Number(e.cost_usd).toFixed(2)}` : "—"}</span> },
              { key: "started", header: "Started", render: (e) => (e.started_at ? new Date(e.started_at).toLocaleString() : "—") },
            ]}
            rows={executions}
            onRowClick={setSelectedEx}
            caption="Workflow executions"
          />
        )
      )}
      {showNew && (
        <Modal title="New workflow definition" onClose={() => setShowNew(false)}>
          <DefForm onDone={() => { setShowNew(false); defs.reload(); }} />
        </Modal>
      )}
      {publishFor && (
        <Modal title={`Publish version — ${publishFor.name}`} onClose={() => setPublishFor(null)}>
          <PublishVersion def={publishFor} onDone={() => { setPublishFor(null); defs.reload(); }} />
        </Modal>
      )}
      {startFor && (
        <Modal title={`Run — ${startFor.name}`} onClose={() => setStartFor(null)}>
          <StartExecution
            def={startFor}
            onDone={(id) => {
              setStartFor(null);
              try { localStorage.setItem(FIRST_RUN_FLAG, "1"); } catch { /* ignore */ }
              api.getWorkflowExecution(id).then(pushRun).catch(() => undefined);
              setTab("executions");
            }}
          />
        </Modal>
      )}
      {selectedEx && (
        <ExecutionDetail
          execution={selectedEx}
          onClose={() => setSelectedEx(null)}
          reload={() => {
            api.getWorkflowExecution(selectedEx.id).then((ex) => {
              setSelectedEx(ex);
              pushRun(ex);
            }).catch(() => undefined);
          }}
        />
      )}
      <Card className="mt-6">
        <h2 className="mb-1 text-sm font-semibold">Signature demo: new-lead pipeline</h2>
        <p className="text-xs text-neutral-500">
          Lead → score → outreach draft → <strong>approval</strong> → follow-up task. Start it from the CRM
          Leads tab (“Start lead pipeline”), watch the run here, then approve the outreach draft in the
          Approvals inbox — the follow-up task appears in Tasks.
        </p>
      </Card>
    </div>
  );
}
