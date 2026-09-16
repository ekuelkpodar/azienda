import { Link } from "react-router-dom";
import { api } from "../api/client";
import { useApi } from "../api/hooks";
import { Badge, Card, PageHeader, QueryView, Spinner } from "../components/ui";

function Kpi({ label, value, to }: { label: string; value: string | number; to: string }) {
  return (
    <Link
      to={to}
      className="block rounded-xl border border-neutral-200 bg-white p-5 shadow-sm transition-colors hover:border-neutral-400"
    >
      <div className="text-xs font-medium uppercase tracking-wide text-neutral-500">{label}</div>
      <div className="mt-1 text-3xl font-semibold tabular-nums">{value}</div>
    </Link>
  );
}

export default function Dashboard() {
  const summary = useApi(() => api.getCommandSummary());

  return (
    <div>
      <PageHeader
        title="Dashboard"
        subtitle="System status and business KPIs, tenant-scoped from the command-center aggregates."
      />
      <QueryView query={summary} context="GET /command-center/summary">
        {(s) => (
          <>
            <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
              <Kpi label="Active goals" value={s.active_goals} to="/command-center" />
              <Kpi label="Running tasks" value={s.running_tasks} to="/tasks" />
              <Kpi label="Pending approvals" value={s.pending_approvals} to="/approvals" />
              <Kpi label="Agent burn today" value={`$${Number(s.burn_today_usd).toFixed(2)}`} to="/billing" />
              <Kpi
                label="Outcomes (all time)"
                value={Object.values(s.outcome_counts).reduce((a, b) => a + b, 0)}
                to="/command-center"
              />
            </div>
            <div className="mt-6 grid gap-4 lg:grid-cols-2">
              <Card>
                <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-neutral-500">
                  System status
                </h2>
                <ul className="space-y-2 text-sm">
                  <li className="flex items-center justify-between">
                    <span>API (same-origin /api/v1)</span>
                    <Badge tone="green">Reachable</Badge>
                  </li>
                  <li className="flex items-center justify-between">
                    <span>Auth session</span>
                    <Badge tone="green">Active</Badge>
                  </li>
                  <li className="flex items-center justify-between">
                    <span>Agent workforce</span>
                    <Badge tone={s.running_tasks > 0 ? "blue" : "neutral"}>
                      {s.running_tasks > 0 ? `${s.running_tasks} running` : "Idle"}
                    </Badge>
                  </li>
                  <li className="flex items-center justify-between">
                    <span>Approvals queue</span>
                    <Badge tone={s.pending_approvals > 0 ? "amber" : "green"}>
                      {s.pending_approvals > 0 ? `${s.pending_approvals} waiting` : "Clear"}
                    </Badge>
                  </li>
                </ul>
              </Card>
              <Card>
                <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-neutral-500">
                  Outcomes by type
                </h2>
                {Object.keys(s.outcome_counts).length === 0 ? (
                  <p className="text-sm text-neutral-500">No outcomes recorded yet.</p>
                ) : (
                  <ul className="space-y-2 text-sm">
                    {Object.entries(s.outcome_counts).map(([type, count]) => (
                      <li key={type} className="flex items-center justify-between">
                        <span className="capitalize">{type.replace(/_/g, " ")}</span>
                        <span className="font-medium tabular-nums">{count}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </Card>
            </div>
          </>
        )}
      </QueryView>
      {summary.loading && <Spinner label="Loading dashboard…" />}
    </div>
  );
}
