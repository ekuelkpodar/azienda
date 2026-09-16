import { useState } from "react";
import { api, asPage } from "../api/client";
import { useApi, useMutation } from "../api/hooks";
import type { Agent, TaskStatus } from "../api/types";
import {
  Badge,
  Button,
  Card,
  DataTable,
  EmptyState,
  Modal,
  PageHeader,
  QueryView,
  Spinner,
  useToast,
} from "../components/ui";

const STATUS_TONE: Record<string, "green" | "amber" | "neutral" | "blue"> = {
  active: "green",
  paused: "amber",
  draft: "neutral",
};

const TASK_STATES: TaskStatus[] = [
  "pending",
  "planning",
  "waiting_approval",
  "executing",
  "blocked",
  "completed",
  "failed",
  "cancelled",
];

function AgentDetail({ agent, onClose, reload }: { agent: Agent; onClose: () => void; reload: () => void }) {
  const detail = useApi(() => api.getAgent(agent.id), [agent.id]);
  const { push } = useToast();
  const pause = useMutation(() => api.pauseAgent(agent.id));
  const resume = useMutation(() => api.resumeAgent(agent.id));

  async function toggle() {
    const ok = agent.status === "active" ? await pause.mutate(undefined) : await resume.mutate(undefined);
    if (ok) {
      push("success", `Agent ${agent.status === "active" ? "paused" : "resumed"}.`);
      reload();
      onClose();
    } else {
      push("error", (pause.error ?? resume.error)?.body.message ?? "Action failed.");
    }
  }

  return (
    <Modal title={agent.name} onClose={onClose} wide>
      <QueryView query={detail} context={`GET /agents/${agent.id}`}>
        {(d) => (
          <div className="space-y-4 text-sm">
            <div className="flex items-center gap-2">
              <Badge tone={STATUS_TONE[d.status] ?? "neutral"}>{d.status}</Badge>
              {d.description && <span className="text-neutral-500">{d.description}</span>}
            </div>
            <div>
              <h3 className="mb-2 font-semibold">Versions</h3>
              {!d.versions || d.versions.length === 0 ? (
                <p className="text-neutral-500">No published versions yet.</p>
              ) : (
                <DataTable
                  columns={[
                    { key: "v", header: "Version", render: (v) => `v${v.version}` },
                    { key: "caps", header: "Capabilities", render: (v) => v.capabilities.join(", ") || "—" },
                    { key: "tools", header: "Allowed tools", render: (v) => v.allowed_tools.join(", ") || "—" },
                    {
                      key: "autonomy",
                      header: "Autonomy L",
                      render: (v) => (v.autonomy_level == null ? "—" : `L${v.autonomy_level}`),
                    },
                  ]}
                  rows={d.versions.map((v) => ({ ...v, id: v.id }))}
                  caption="Agent versions"
                />
              )}
            </div>
            <div className="flex gap-2">
              <Button variant={agent.status === "active" ? "danger" : "primary"} size="sm" onClick={() => void toggle()} disabled={pause.loading || resume.loading}>
                {agent.status === "active" ? "Pause agent" : "Resume agent"}
              </Button>
            </div>
          </div>
        )}
      </QueryView>
    </Modal>
  );
}

/** Look up a run by id — drill-down from task outcome or manual entry. */
function RunInspector() {
  const [runId, setRunId] = useState("");
  const [lookup, setLookup] = useState<string | null>(null);
  const run = useApi(() => (lookup ? api.getAgentRun(lookup) : Promise.reject(new Error("no run"))), [lookup ?? ""]);

  return (
    <Card className="mb-5">
      <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-neutral-500">Run inspector</h2>
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (runId.trim()) setLookup(runId.trim());
        }}
      >
        <input
          value={runId}
          onChange={(e) => setRunId(e.target.value)}
          placeholder="Paste agent run id…"
          aria-label="Agent run id"
          className="w-full rounded-md border border-neutral-300 px-3 py-2 text-sm focus:border-neutral-500 focus:outline-none"
        />
        <Button type="submit" variant="primary" size="sm">
          Inspect
        </Button>
      </form>
      {lookup && (
        <div className="mt-4">
          {run.loading && <Spinner label="Loading run…" />}
          {run.error && (
            <p className="text-sm text-red-700">
              Run not found or unavailable ({run.error.code}). Runs are created when a task is delegated to the agent workforce.
            </p>
          )}
          {run.data && (
            <div className="space-y-3 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <Badge>{run.data.status}</Badge>
                {run.data.cost_usd != null && <span className="tabular-nums text-neutral-600">Cost ${Number(run.data.cost_usd).toFixed(2)}</span>}
              </div>
              <div>
                <h3 className="mb-1 font-semibold">Plan steps</h3>
                {run.data.steps.length === 0 ? (
                  <p className="text-neutral-500">No steps recorded.</p>
                ) : (
                  <ol className="list-decimal space-y-1 pl-5">
                    {run.data.steps.map((s, i) => (
                      <li key={i}>
                        <span className="font-medium">{s.name}</span>
                        {s.tool && <code className="ml-2 rounded bg-neutral-100 px-1 text-xs">{s.tool}</code>}
                        <Badge tone="neutral">{s.status}</Badge>
                        {s.cost_usd != null && <span className="ml-2 tabular-nums text-neutral-500">${Number(s.cost_usd).toFixed(4)}</span>}
                      </li>
                    ))}
                  </ol>
                )}
              </div>
              <div>
                <h3 className="mb-1 font-semibold">Policy decisions</h3>
                <pre className="max-h-48 overflow-auto rounded-md bg-neutral-50 p-3 text-xs">
                  {JSON.stringify(run.data.policy_decisions ?? [], null, 2)}
                </pre>
              </div>
              <div>
                <h3 className="mb-1 font-semibold">Tool calls</h3>
                <pre className="max-h-48 overflow-auto rounded-md bg-neutral-50 p-3 text-xs">
                  {JSON.stringify(run.data.tool_calls ?? [], null, 2)}
                </pre>
              </div>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

function LifecycleStates() {
  const q = useApi(() => api.listTasks({ page_size: 200 }).then(asPage));
  const counts = new Map<TaskStatus, number>();
  for (const t of q.data?.items ?? []) counts.set(t.status, (counts.get(t.status) ?? 0) + 1);
  return (
    <Card className="mb-5">
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-neutral-500">
        Task lifecycle states (governed pipeline)
      </h2>
      {q.loading ? (
        <Spinner label="Loading task states…" />
      ) : q.error ? (
        <p className="text-sm text-neutral-500">Task states unavailable ({q.error.code}).</p>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          {TASK_STATES.map((s, i) => (
            <span key={s} className="flex items-center gap-2">
              <Badge tone={s === "failed" || s === "blocked" ? "red" : s === "completed" ? "green" : s === "executing" ? "blue" : s === "waiting_approval" ? "amber" : "neutral"}>
                {s.replace(/_/g, " ")} · {counts.get(s) ?? 0}
              </Badge>
              {i < TASK_STATES.length - 1 && <span className="text-neutral-300">→</span>}
            </span>
          ))}
        </div>
      )}
      <p className="mt-2 text-xs text-neutral-500">
        Binding state machine: pending → planning → waiting_approval → executing → blocked → completed | failed, plus cancelled from any non-terminal state.
      </p>
    </Card>
  );
}

export default function AgentConsole() {
  const [status, setStatus] = useState("");
  const [selected, setSelected] = useState<Agent | null>(null);
  const agents = useApi(() => api.listAgents(status ? { status } : undefined).then(asPage), [status]);

  return (
    <div>
      <PageHeader
        title="Agent Console"
        subtitle="The registered agent workforce: lifecycle states, run detail, plan steps, and audit trail."
        actions={
          <select
            aria-label="Filter by status"
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            className="rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm"
          >
            <option value="">All statuses</option>
            <option value="active">Active</option>
            <option value="paused">Paused</option>
            <option value="draft">Draft</option>
          </select>
        }
      />
      <LifecycleStates />
      <RunInspector />
      <QueryView query={agents} context="GET /agents">
        {(page) =>
          page.items.length === 0 ? (
            <EmptyState
              title="No agents registered"
              hint="Register agents via POST /agents (admin). Demo data includes two seeded agents once seed_demo.py has run."
            />
          ) : (
            <DataTable
              columns={[
                { key: "name", header: "Agent", render: (a) => <span className="font-medium">{a.name}</span> },
                { key: "desc", header: "Description", render: (a) => <span className="text-neutral-500">{a.description ?? "—"}</span> },
                { key: "status", header: "Status", render: (a) => <Badge tone={STATUS_TONE[a.status] ?? "neutral"}>{a.status}</Badge> },
              ]}
              rows={page.items}
              onRowClick={setSelected}
              caption="Registered agents"
            />
          )
        }
      </QueryView>
      {selected && <AgentDetail agent={selected} onClose={() => setSelected(null)} reload={agents.reload} />}
    </div>
  );
}
