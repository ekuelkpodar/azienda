import { useState } from "react";
import { api, asPage } from "../api/client";
import { useApi, useMutation } from "../api/hooks";
import type { Task, TaskStatus } from "../api/types";
import {
  Badge,
  Button,
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

const TONE: Record<TaskStatus, "green" | "amber" | "neutral" | "blue" | "red"> = {
  pending: "neutral",
  planning: "blue",
  waiting_approval: "amber",
  executing: "blue",
  blocked: "red",
  completed: "green",
  failed: "red",
  cancelled: "neutral",
};

function TaskForm({ onDone }: { onDone: () => void }) {
  const { push } = useToast();
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [priority, setPriority] = useState("medium");
  const create = useMutation((b: Parameters<typeof api.createTask>[0]) => api.createTask(b));
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const t = await create.mutate({ title, description, priority });
    if (t) { push("success", "Task created."); onDone(); }
  }
  return (
    <form onSubmit={submit} className="space-y-4">
      <Field label="Title"><Input required value={title} onChange={(e) => setTitle(e.target.value)} /></Field>
      <Field label="Description"><TextArea value={description} onChange={(e) => setDescription(e.target.value)} rows={3} /></Field>
      <Field label="Priority">
        <Select value={priority} onChange={(e) => setPriority(e.target.value)}>
          <option value="low">Low</option>
          <option value="medium">Medium</option>
          <option value="high">High</option>
          <option value="urgent">Urgent</option>
        </Select>
      </Field>
      {create.error && <p className="text-sm text-red-700" role="alert">{create.error.body.message}</p>}
      <Button type="submit" variant="primary" disabled={create.loading}>{create.loading ? "Creating…" : "Create task"}</Button>
    </form>
  );
}

function TaskDetail({ task, onClose, reload }: { task: Task; onClose: () => void; reload: () => void }) {
  const { push } = useToast();
  const detail = useApi(() => api.getTask(task.id), [task.id]);
  const transitions = useApi(() => api.listTaskTransitions(task.id), [task.id]);
  const comments = useApi(() => api.listTaskComments(task.id).then(asPage), [task.id]);
  const outcome = useApi(() => api.getTaskOutcome(task.id).catch(() => null), [task.id]);
  const [note, setNote] = useState("");
  const [comment, setComment] = useState("");
  const [next, setNext] = useState<TaskStatus>("executing");

  const transition = useMutation(({ to, n }: { to: TaskStatus; n?: string }) => api.transitionTask(task.id, to, n));
  const delegate = useMutation(() => api.delegateTask(task.id));
  const addComment = useMutation((body: string) => api.addTaskComment(task.id, body));

  async function onTransition() {
    const t = await transition.mutate({ to: next, n: note || undefined });
    if (t) { push("success", `Task moved to ${next.replace(/_/g, " ")}.`); setNote(""); reload(); detail.reload(); transitions.reload(); }
    else push("error", transition.error?.body.message ?? "Transition rejected by the state machine.");
  }
  async function onDelegate() {
    const r = await delegate.mutate(undefined);
    if (r) push("success", `Delegated to agent workforce (run ${r.run_id.slice(0, 8)}…).`);
    else push("error", delegate.error?.body.message ?? "Delegation failed.");
  }
  async function onComment(e: React.FormEvent) {
    e.preventDefault();
    const c = await addComment.mutate(comment);
    if (c) { setComment(""); comments.reload(); }
  }

  const current = detail.data ?? task;

  return (
    <Modal title={current.title} onClose={onClose} wide>
      <div className="space-y-5 text-sm">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={TONE[current.status]}>{current.status.replace(/_/g, " ")}</Badge>
          <Badge tone="neutral">{current.priority}</Badge>
          {current.cost_usd != null && <span className="tabular-nums text-neutral-600">Cost so far ${Number(current.cost_usd).toFixed(2)}</span>}
          {current.due_at && <span className="text-neutral-500">Due {new Date(current.due_at).toLocaleDateString()}</span>}
        </div>
        {current.description && <p className="text-neutral-700">{current.description}</p>}

        <div>
          <h3 className="mb-2 font-semibold">Transition (guarded by state machine)</h3>
          <div className="flex flex-wrap items-end gap-2">
            <Select aria-label="Next state" value={next} onChange={(e) => setNext(e.target.value as TaskStatus)}>
              {(Object.keys(TONE) as TaskStatus[]).map((s) => (
                <option key={s} value={s}>{s.replace(/_/g, " ")}</option>
              ))}
            </Select>
            <Input aria-label="Transition note" placeholder="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} className="max-w-xs" />
            <Button size="sm" variant="primary" onClick={() => void onTransition()} disabled={transition.loading}>Apply</Button>
            <Button size="sm" variant="secondary" onClick={() => void onDelegate()} disabled={delegate.loading} title="Hand to the agent workforce — creates an agent run (policy-gated)">
              Delegate to agents
            </Button>
          </div>
          {transition.error && <p className="mt-1 text-xs text-red-700">{transition.error.body.message}</p>}
        </div>

        <div>
          <h3 className="mb-2 font-semibold">Transition history</h3>
          {transitions.loading ? <p className="text-neutral-500">Loading…</p> :
            !transitions.data || transitions.data.length === 0 ? <p className="text-neutral-500">No transitions recorded.</p> :
            (
              <ol className="space-y-1">
                {transitions.data.map((t) => (
                  <li key={t.id} className="text-xs text-neutral-600">
                    <code>{t.from_status}</code> → <code>{t.to_status}</code>
                    {t.note && <span> — {t.note}</span>}
                    <span className="ml-2 text-neutral-400">{new Date(t.created_at).toLocaleString()}</span>
                  </li>
                ))}
              </ol>
            )}
        </div>

        <div>
          <h3 className="mb-2 font-semibold">Outcome record</h3>
          {outcome.loading ? <p className="text-neutral-500">Loading…</p> :
            !outcome.data ? <p className="text-neutral-500">No outcome yet — recorded when the task completes or fails.</p> :
            (
              <pre className="max-h-56 overflow-auto rounded-md bg-neutral-50 p-3 text-xs">
                {JSON.stringify(outcome.data, null, 2)}
              </pre>
            )}
        </div>

        <div>
          <h3 className="mb-2 font-semibold">Comments</h3>
          <form onSubmit={(e) => void onComment(e)} className="mb-2 flex gap-2">
            <Input aria-label="Add a comment" value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Add a comment…" required />
            <Button type="submit" size="sm" disabled={addComment.loading}>Post</Button>
          </form>
          <ul className="space-y-2">
            {(comments.data?.items ?? []).map((c) => (
              <li key={c.id} className="rounded-md bg-neutral-50 p-2 text-xs">
                <p>{c.body}</p>
                <p className="mt-0.5 text-neutral-400">{new Date(c.created_at).toLocaleString()}</p>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </Modal>
  );
}

export default function Tasks() {
  const [view, setView] = useState<"list" | "board">("board");
  const [status, setStatus] = useState("");
  const [showNew, setShowNew] = useState(false);
  const [selected, setSelected] = useState<Task | null>(null);
  const tasks = useApi(() => api.listTasks(status ? { status } : undefined).then(asPage), [status]);

  const states: TaskStatus[] = ["pending", "planning", "waiting_approval", "executing", "blocked", "completed", "failed"];

  return (
    <div>
      <PageHeader
        title="Tasks"
        subtitle="Human and agent work items on the governed lifecycle."
        actions={
          <>
            <Select aria-label="Filter by status" value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">All statuses</option>
              {states.concat("cancelled").map((s) => (
                <option key={s} value={s}>{s.replace(/_/g, " ")}</option>
              ))}
            </Select>
            <Button variant="primary" size="sm" onClick={() => setShowNew(true)}>New task</Button>
          </>
        }
      />
      <Tabs
        tabs={[{ key: "board", label: "Board" }, { key: "list", label: "List" }]}
        active={view}
        onChange={setView}
      />
      <QueryView query={tasks} context="GET /tasks">
        {(page) =>
          page.items.length === 0 ? (
            <EmptyState title="No tasks" hint="Create a task, or delegate one to the agent workforce to create an agent run." />
          ) : view === "list" ? (
            <DataTable
              columns={[
                { key: "title", header: "Task", render: (t) => <span className="font-medium">{t.title}</span> },
                { key: "status", header: "Status", render: (t) => <Badge tone={TONE[t.status]}>{t.status.replace(/_/g, " ")}</Badge> },
                { key: "priority", header: "Priority", render: (t) => t.priority },
                { key: "cost", header: "Cost", render: (t) => <span className="tabular-nums">{t.cost_usd != null ? `$${Number(t.cost_usd).toFixed(2)}` : "—"}</span> },
                { key: "due", header: "Due", render: (t) => (t.due_at ? new Date(t.due_at).toLocaleDateString() : "—") },
              ]}
              rows={page.items}
              onRowClick={setSelected}
              caption="Tasks"
            />
          ) : (
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
              {states.map((s) => {
                const cards = page.items.filter((t) => t.status === s);
                return (
                  <div key={s} className="rounded-xl border border-neutral-200 bg-neutral-50 p-3">
                    <div className="mb-3 flex items-center justify-between">
                      <h3 className="text-sm font-semibold capitalize">{s.replace(/_/g, " ")}</h3>
                      <Badge tone="neutral">{cards.length}</Badge>
                    </div>
                    <div className="space-y-2">
                      {cards.map((t) => (
                        <button
                          key={t.id}
                          onClick={() => setSelected(t)}
                          className="w-full rounded-lg border border-neutral-200 bg-white p-3 text-left text-sm shadow-sm hover:border-neutral-400 focus:outline-none focus-visible:ring-2 focus-visible:ring-neutral-500"
                        >
                          <p className="font-medium">{t.title}</p>
                          <p className="mt-1 text-xs text-neutral-500">
                            {t.priority}
                            {t.cost_usd != null && <span className="tabular-nums"> · ${Number(t.cost_usd).toFixed(2)}</span>}
                          </p>
                        </button>
                      ))}
                      {cards.length === 0 && <p className="text-xs text-neutral-400">Empty</p>}
                    </div>
                  </div>
                );
              })}
            </div>
          )
        }
      </QueryView>
      {showNew && (
        <Modal title="New task" onClose={() => setShowNew(false)}>
          <TaskForm onDone={() => { setShowNew(false); tasks.reload(); }} />
        </Modal>
      )}
      {selected && <TaskDetail task={selected} onClose={() => setSelected(null)} reload={tasks.reload} />}
    </div>
  );
}
