import { useState } from "react";
import { Link } from "react-router-dom";
import { api, asPage } from "../api/client";
import { useApi } from "../api/hooks";
import {
  Badge,
  Button,
  Card,
  DataTable,
  EmptyState,
  Input,
  PageHeader,
  PlannedBadge,
  QueryView,
  Spinner,
  Tabs,
} from "../components/ui";

type Tab = "briefing" | "goals" | "outcomes" | "costs" | "policy";

function fmtUsd(n: number | null | undefined): string {
  return `$${Number(n ?? 0).toFixed(2)}`;
}

/** "What needs my attention" — derived from live aggregates, every item links to evidence. */
function Briefing() {
  const summary = useApi(() => api.getCommandSummary());
  const approvals = useApi(() => api.listApprovals({ status: "pending", page_size: 5 }).then(asPage));
  const outcomes = useApi(() => api.listOutcomes().then(asPage));
  const alerts = useApi(() => api.listBudgetAlerts().then(asPage));

  const loading = summary.loading || approvals.loading || outcomes.loading || alerts.loading;
  if (loading) return <Spinner label="Assembling briefing…" />;

  const items: { title: string; detail: string; to: string; tone: "red" | "amber" | "blue" | "neutral" }[] = [];
  const s = summary.data;
  if (s) {
    if (s.pending_approvals > 0)
      items.push({
        title: `${s.pending_approvals} approval${s.pending_approvals === 1 ? "" : "s"} waiting`,
        detail: "Agent actions are blocked on human decisions. Timeouts deny by default — review soon.",
        to: "/approvals",
        tone: "amber",
      });
    if (s.running_tasks > 0)
      items.push({
        title: `${s.running_tasks} task${s.running_tasks === 1 ? "" : "s"} running`,
        detail: "Agent workforce is executing. Open Tasks to see lifecycle states and cost so far.",
        to: "/tasks",
        tone: "blue",
      });
  }
  (alerts.data?.items ?? []).slice(0, 3).forEach((a) => {
    items.push({
      title: `Budget alert: ${a.message}`,
      detail: "Spend threshold crossed — review the cost ledger before new agent work.",
      to: "/admin",
      tone: "red",
    });
  });
  if (items.length === 0)
    items.push({
      title: "Nothing urgent",
      detail: "No pending approvals, no budget alerts, no blocked work detected.",
      to: "/tasks",
      tone: "neutral",
    });

  return (
    <div className="space-y-4">
      {summary.error && (
        <p className="text-sm text-red-700">
          Briefing aggregates unavailable ({summary.error.code}). Showing partial data.
        </p>
      )}
      <div>
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-neutral-500">
          Needs your attention
        </h2>
        <ul className="space-y-2">
          {items.map((it, i) => (
            <li key={i}>
              <Link
                to={it.to}
                className="flex items-start justify-between gap-4 rounded-xl border border-neutral-200 bg-white p-4 shadow-sm hover:border-neutral-400"
              >
                <div>
                  <p className="text-sm font-medium text-neutral-900">{it.title}</p>
                  <p className="mt-0.5 text-xs text-neutral-500">{it.detail}</p>
                </div>
                <Badge tone={it.tone === "neutral" ? "neutral" : it.tone}>View →</Badge>
              </Link>
            </li>
          ))}
        </ul>
      </div>
      <div>
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-neutral-500">
          Latest outcomes
        </h2>
        {outcomes.error || !outcomes.data || outcomes.data.items.length === 0 ? (
          <EmptyState
            title="No outcomes yet"
            hint="Completed agent work lands on the outcome ledger with fully-loaded cost."
          />
        ) : (
          <DataTable
            columns={[
              { key: "type", header: "Type", render: (o) => <span className="capitalize">{o.type.replace(/_/g, " ")}</span> },
              { key: "title", header: "Outcome", render: (o) => o.title ?? "—" },
              { key: "status", header: "Status", render: (o) => <Badge tone={o.status === "completed" ? "green" : "neutral"}>{o.status}</Badge> },
              { key: "cost", header: "Cost", render: (o) => <span className="tabular-nums">{fmtUsd(o.cost_usd)}</span> },
              { key: "at", header: "Recorded", render: (o) => new Date(o.created_at).toLocaleString() },
            ]}
            rows={outcomes.data.items.slice(0, 10)}
            caption="Latest outcomes"
          />
        )}
      </div>
    </div>
  );
}

function Goals() {
  const q = useApi(() => api.listGoals().then(asPage));
  return (
    <QueryView query={q} context="GET /command-center/goals">
      {(page) =>
        page.items.length === 0 ? (
          <EmptyState title="No goals yet" hint="Goals live in the AGRL ledger and survive sessions and agents." />
        ) : (
          <DataTable
            columns={[
              { key: "title", header: "Goal", render: (g) => <span className="font-medium">{g.title}</span> },
              { key: "status", header: "Status", render: (g) => <Badge tone={g.status === "active" ? "green" : "neutral"}>{g.status}</Badge> },
              {
                key: "progress",
                header: "Progress",
                render: (g) => (g.progress == null ? "—" : `${Math.round(g.progress * 100)}%`),
              },
              { key: "updated", header: "Updated", render: (g) => new Date(g.updated_at).toLocaleString() },
            ]}
            rows={page.items}
            caption="Goals from AGRL projections"
          />
        )
      }
    </QueryView>
  );
}

function Outcomes() {
  const q = useApi(() => api.listOutcomes().then(asPage));
  return (
    <QueryView query={q} context="GET /command-center/outcomes">
      {(page) =>
        page.items.length === 0 ? (
          <EmptyState title="No outcomes yet" hint="Every completed task records plan, decisions, tool calls, retries, and fully-loaded cost." />
        ) : (
          <DataTable
            columns={[
              { key: "type", header: "Type", render: (o) => <span className="capitalize">{o.type.replace(/_/g, " ")}</span> },
              { key: "title", header: "Outcome", render: (o) => o.title ?? "—" },
              { key: "status", header: "Status", render: (o) => <Badge>{o.status}</Badge> },
              { key: "cost", header: "Fully-loaded cost", render: (o) => <span className="tabular-nums">{fmtUsd(o.cost_usd)}</span> },
              { key: "at", header: "Recorded", render: (o) => new Date(o.created_at).toLocaleString() },
            ]}
            rows={page.items}
            caption="Outcome ledger"
          />
        )
      }
    </QueryView>
  );
}

function Costs() {
  const q = useApi(() => api.getCostBreakdown());
  return (
    <QueryView query={q} context="GET /command-center/costs">
      {(raw) => {
        const items = Array.isArray(raw) ? raw : raw.items;
        if (items.length === 0) return <EmptyState title="No cost data yet" hint="Model calls are metered per task, agent, and workflow." />;
        const total = items.reduce((a, b) => a + b.cost_usd, 0);
        return (
          <Card>
            <ul className="space-y-3 text-sm">
              {items.map((c) => (
                <li key={c.key}>
                  <div className="flex items-center justify-between">
                    <span className="font-medium">{c.label}</span>
                    <span className="tabular-nums">{fmtUsd(c.cost_usd)}</span>
                  </div>
                  <div className="mt-1 h-2 overflow-hidden rounded-full bg-neutral-100">
                    <div
                      className="h-full rounded-full bg-neutral-800"
                      style={{ width: total > 0 ? `${Math.min(100, (c.cost_usd / total) * 100)}%` : "0%" }}
                    />
                  </div>
                </li>
              ))}
            </ul>
            <p className="mt-4 border-t border-neutral-200 pt-3 text-sm font-semibold">
              Total <span className="float-right tabular-nums">{fmtUsd(total)}</span>
            </p>
          </Card>
        );
      }}
    </QueryView>
  );
}

function PolicyActivity() {
  const q = useApi(() => api.getPolicyActivity());
  return (
    <QueryView query={q} context="GET /command-center/policy-activity">
      {(items) =>
        items.length === 0 ? (
          <EmptyState title="No policy decisions yet" hint="Every consequential agent action is evaluated allow / deny / require-approval before the tool call." />
        ) : (
          <DataTable
            columns={[
              { key: "action", header: "Action", render: (p) => <code className="text-xs">{p.action}</code> },
              {
                key: "decision",
                header: "Decision",
                render: (p) => (
                  <Badge tone={p.decision === "allow" ? "green" : p.decision === "deny" ? "red" : "amber"}>
                    {p.decision.replace("_", " ")}
                  </Badge>
                ),
              },
              { key: "count", header: "Count", render: (p) => <span className="tabular-nums">{p.count}</span> },
            ]}
            rows={items.map((p, i) => ({ ...p, id: `${p.action}-${p.decision}-${i}` }))}
            caption="Recent policy decisions"
          />
        )
      }
    </QueryView>
  );
}

export default function CommandCenter() {
  const [tab, setTab] = useState<Tab>("briefing");
  const [nlQuery, setNlQuery] = useState("");

  return (
    <div>
      <PageHeader
        title="AI Command Center"
        subtitle="The ops view: what needs attention, goals, outcomes, costs, and policy activity — all tenant-scoped."
      />

      {/* NL query box — honest Planned state: API.md defines no intent endpoint. */}
      <Card className="mb-5">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-sm font-semibold">Ask anything</h2>
          <PlannedBadge reason="API.md defines no NL intent endpoint yet (command-center is read-only aggregates). The box is disabled until the contract lands — no fake answers." />
        </div>
        <div className="mt-2 flex gap-2">
          <Input
            value={nlQuery}
            onChange={(e) => setNlQuery(e.target.value)}
            placeholder="e.g. Which deals are at risk this week?"
            disabled
            aria-label="Natural-language query (planned)"
          />
          <Button
            variant="primary"
            planned
            plannedReason="The NL intent endpoint is not in the API contract yet. The box is disabled until backend builder 4 defines it."
          >
            Ask
          </Button>
        </div>
        <p className="mt-2 text-xs text-neutral-500">
          Today: use the Briefing tab for the executive view — approvals, running work, budget alerts, and latest outcomes, each linking to its evidence.
        </p>
      </Card>

      <Tabs
        tabs={[
          { key: "briefing", label: "Briefing" },
          { key: "goals", label: "Goals" },
          { key: "outcomes", label: "Outcomes" },
          { key: "costs", label: "Costs" },
          { key: "policy", label: "Policy activity" },
        ]}
        active={tab}
        onChange={setTab}
      />
      {tab === "briefing" && <Briefing />}
      {tab === "goals" && <Goals />}
      {tab === "outcomes" && <Outcomes />}
      {tab === "costs" && <Costs />}
      {tab === "policy" && <PolicyActivity />}
    </div>
  );
}
