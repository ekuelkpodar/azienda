import { useState } from "react";
import { api, asPage } from "../api/client";
import { useApi, useMutation } from "../api/hooks";
import { Badge, Button, Card, DataTable, EmptyState, Field, Input, PageHeader, QueryView, Tabs, useToast } from "../components/ui";

type Tab = "overview" | "plans" | "invoices";

function Overview() {
  const { push } = useToast();
  const sub = useApi(() => api.getSubscription());
  const usage = useApi(() => api.getUsage().then(asPage));
  const [cap, setCap] = useState("");
  const setCapMut = useMutation((c: number) => api.setSpendCap(c));

  async function onCap(e: React.FormEvent) {
    e.preventDefault();
    const v = Number(cap);
    if (!Number.isFinite(v) || v < 0) { push("error", "Enter a valid cap amount."); return; }
    const r = await setCapMut.mutate(v);
    if (r) { push("success", `Spend cap set to $${v.toFixed(2)}. Overage requires explicit cap.`); setCap(""); }
    else push("error", setCapMut.error?.body.message ?? "Failed to set cap.");
  }

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-neutral-500">Subscription</h2>
        <QueryView query={sub} context="GET /billing/subscription">
          {(s) => (
            <dl className="space-y-2 text-sm">
              <div className="flex justify-between"><dt className="text-neutral-500">Plan</dt><dd className="font-medium">{s.plan_name}</dd></div>
              <div className="flex justify-between"><dt className="text-neutral-500">Status</dt><dd><Badge tone={s.status === "active" ? "green" : "neutral"}>{s.status}</Badge></dd></div>
              <div className="flex justify-between"><dt className="text-neutral-500">Credit balance</dt><dd className="font-medium tabular-nums">{s.credit_balance}</dd></div>
              {s.renews_at && <div className="flex justify-between"><dt className="text-neutral-500">Renews</dt><dd>{new Date(s.renews_at).toLocaleDateString()}</dd></div>}
            </dl>
          )}
        </QueryView>
        <form onSubmit={(e) => void onCap(e)} className="mt-4 border-t border-neutral-200 pt-4">
          <Field label="Customer spend cap (USD)" hint="Overage spend is blocked until you raise the cap.">
            <div className="flex gap-2">
              <Input type="number" min="0" step="0.01" value={cap} onChange={(e) => setCap(e.target.value)} placeholder="e.g. 500" />
              <Button type="submit" size="sm" disabled={setCapMut.loading}>Set cap</Button>
            </div>
          </Field>
        </form>
      </Card>
      <Card>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-neutral-500">Metered usage</h2>
        <QueryView query={usage} context="GET /billing/usage">
          {(page) =>
            page.items.length === 0 ? (
              <p className="text-sm text-neutral-500">No usage recorded in this period.</p>
            ) : (
              <ul className="space-y-2 text-sm">
                {page.items.map((u, i) => (
                  <li key={i} className="flex items-center justify-between">
                    <span className="capitalize">{u.dimension.replace(/_/g, " ")}</span>
                    <span className="tabular-nums">{u.used} {u.unit}</span>
                  </li>
                ))}
              </ul>
            )
          }
        </QueryView>
      </Card>
    </div>
  );
}

function Plans() {
  const { push } = useToast();
  const plans = useApi(() => api.listBillingPlans().then(asPage));
  const change = useMutation((plan_id: string) => api.changePlan(plan_id));
  async function onChange(planId: string, planName: string) {
    const s = await change.mutate(planId);
    if (s) push("success", `Plan changed to ${planName} (idempotent).`);
    else push("error", change.error?.body.message ?? "Plan change failed.");
  }
  return (
    <QueryView query={plans} context="GET /billing/plans">
      {(page) => (
        <div className="grid gap-4 md:grid-cols-3">
          {page.items.map((p) => (
            <Card key={p.id}>
              <h3 className="font-semibold">{p.name}</h3>
              <p className="mt-1 text-2xl font-semibold tabular-nums">${p.price_usd}<span className="text-sm font-normal text-neutral-500">/mo</span></p>
              <p className="mt-1 text-sm text-neutral-500">{p.included_credits} execution credits included</p>
              <Button size="sm" variant="primary" className="mt-4" onClick={() => void onChange(p.id, p.name)} disabled={change.loading}>
                Choose {p.name}
              </Button>
            </Card>
          ))}
        </div>
      )}
    </QueryView>
  );
}

function Invoices() {
  const inv = useApi(() => api.listBillingInvoices().then(asPage));
  return (
    <QueryView query={inv} context="GET /billing/invoices">
      {(page) =>
        page.items.length === 0 ? (
          <EmptyState title="No invoices yet" hint="" />
        ) : (
          <DataTable
            columns={[
              { key: "period", header: "Period", render: (i) => i.period },
              { key: "total", header: "Total", render: (i) => <span className="font-medium tabular-nums">${Number(i.total_usd).toFixed(2)}</span> },
              { key: "status", header: "Status", render: (i) => <Badge tone={i.status === "paid" ? "green" : "amber"}>{i.status}</Badge> },
            ]}
            rows={page.items}
            caption="Billing invoices"
          />
        )
      }
    </QueryView>
  );
}

export default function Billing() {
  const [tab, setTab] = useState<Tab>("overview");
  return (
    <div>
      <PageHeader title="Billing" subtitle="Azienda's own SaaS billing: plans, credits, metered usage, and spend caps." />
      <Tabs
        tabs={[
          { key: "overview", label: "Overview" },
          { key: "plans", label: "Plans" },
          { key: "invoices", label: "Invoices" },
        ]}
        active={tab}
        onChange={setTab}
      />
      {tab === "overview" && <Overview />}
      {tab === "plans" && <Plans />}
      {tab === "invoices" && <Invoices />}
    </div>
  );
}
