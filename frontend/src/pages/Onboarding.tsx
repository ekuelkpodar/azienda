import { Link } from "react-router-dom";
import { api, asPage } from "../api/client";
import { useApi } from "../api/hooks";
import { Badge, Button, Card, PageHeader, PlannedBadge, Spinner, useToast } from "../components/ui";

interface Step {
  key: string;
  title: string;
  detail: string;
  to: string;
  done: boolean | null; // null = planned (no API to verify)
  plannedReason?: string;
}

export const FIRST_RUN_FLAG = "azienda.onboarding.first_workflow_run";

export default function Onboarding() {
  const { push } = useToast();
  const tenant = useApi(() => api.getTenant());
  const users = useApi(() => api.listUsers().then(asPage));
  const me = useApi(() => api.me());
  const agents = useApi(() => api.listAgents().then(asPage));
  const roles = useApi(() => api.listRoles().catch(() => [] as { id: string }[]));
  const defs = useApi(() => api.listWorkflowDefinitions().then(asPage));

  const loading = tenant.loading || users.loading || me.loading || agents.loading || roles.loading || defs.loading;

  const steps: Step[] = [
    {
      key: "org",
      title: "Create your organization",
      detail: "Your workspace exists and is tenant-scoped.",
      to: "/settings",
      done: !tenant.error && !!tenant.data,
    },
    {
      key: "invite",
      title: "Invite your team",
      detail: "Accounts are by invitation — no self-service registration.",
      to: "/admin",
      done: !users.error && (users.data?.items.length ?? 0) >= 2,
    },
    {
      key: "profile",
      title: "Complete your profile",
      detail: "Display name set on your account.",
      to: "/settings",
      done: !me.error && !!me.data?.user.display_name,
    },
    {
      key: "integrations",
      title: "Connect integrations",
      detail: "MCP servers, SaaS APIs (NEXORA, GHL), comms providers.",
      to: "/admin",
      done: null,
      plannedReason: "Integration management endpoints are not in the API contract yet (builder 3).",
    },
    {
      key: "import",
      title: "Import your data",
      detail: "Contacts, deals, historical records.",
      to: "/crm",
      done: null,
      plannedReason: "Bulk import endpoints are not in the API contract yet.",
    },
    {
      key: "ai-config",
      title: "Configure AI providers",
      detail: "Model registry, LiteLLM provider keys, default model mix.",
      to: "/admin",
      done: null,
      plannedReason: "Model-registry endpoints are not in the API contract yet (builder 4).",
    },
    {
      key: "agents",
      title: "Register your first agents",
      detail: "The workforce that will run your workflows.",
      to: "/agents",
      done: !agents.error && (agents.data?.items.length ?? 0) > 0,
    },
    {
      key: "permissions",
      title: "Set roles & permissions",
      detail: "RBAC per tenant; least privilege for humans and agents.",
      to: "/admin",
      done: !roles.error && (roles.data?.length ?? 0) > 0,
    },
    {
      key: "approvals",
      title: "Configure approval policies",
      detail: "Which actions need a human, keyed to risk × reversibility × impact.",
      to: "/approvals",
      done: null,
      plannedReason: "Policy authoring UI is not in the API contract yet — policies are managed server-side.",
    },
    {
      key: "first-workflow",
      title: "Run your first workflow",
      detail: "Try the seeded 'new-lead-pipeline': lead → outreach draft → approval → follow-up task.",
      to: "/workflows",
      done:
        (defs.data?.items.length ?? 0) > 0 &&
        (typeof localStorage !== "undefined" && localStorage.getItem(FIRST_RUN_FLAG) === "1"),
    },
  ];

  const doneCount = steps.filter((s) => s.done === true).length;

  function copySeedCmd() {
    const cmd = "cd backend && python -m app.cli.seed_demo --base-url http://localhost:8000 --email admin@demo.azienda --password 'change-me'";
    void navigator.clipboard?.writeText(cmd).then(
      () => push("success", "Seed command copied."),
      () => push("error", "Clipboard unavailable — copy the command manually."),
    );
  }

  return (
    <div>
      <PageHeader title="Onboarding" subtitle="Get your workspace production-ready. Each step links to where you do it." />
      {loading ? (
        <Spinner label="Checking workspace state…" />
      ) : (
        <>
          <div className="mb-5">
            <div className="flex items-center justify-between text-sm">
              <span className="font-medium">{doneCount} of {steps.length} complete</span>
              <span className="text-neutral-500">{steps.filter((s) => s.done === null).length} planned</span>
            </div>
            <div className="mt-1 h-2 overflow-hidden rounded-full bg-neutral-200">
              <div className="h-full rounded-full bg-neutral-900 transition-all" style={{ width: `${(doneCount / steps.length) * 100}%` }} />
            </div>
          </div>
          <ol className="space-y-2">
            {steps.map((s, i) => (
              <li key={s.key}>
                <Link
                  to={s.to}
                  className="flex items-center gap-4 rounded-xl border border-neutral-200 bg-white p-4 shadow-sm hover:border-neutral-400"
                >
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-neutral-100 text-sm font-semibold text-neutral-600">
                    {i + 1}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-neutral-900">{s.title}</p>
                    <p className="text-xs text-neutral-500">{s.detail}</p>
                  </div>
                  {s.done === null ? (
                    <PlannedBadge reason={s.plannedReason} />
                  ) : s.done ? (
                    <Badge tone="green">Done</Badge>
                  ) : (
                    <Badge tone="amber">To do</Badge>
                  )}
                </Link>
              </li>
            ))}
          </ol>
        </>
      )}

      <Card className="mt-8">
        <h2 className="mb-1 text-sm font-semibold">Demo mode — reset instructions</h2>
        <p className="text-xs text-neutral-500">
          Demo data is seeded server-side by <code className="rounded bg-neutral-100 px-1">backend/app/cli/seed_demo.py</code>.
          To reset the demo workspace, re-run the seed with <code className="rounded bg-neutral-100 px-1">--reset</code>:
        </p>
        <pre className="mt-2 overflow-x-auto rounded-md bg-neutral-900 p-3 text-xs text-neutral-100">
          cd backend && python -m app.cli.seed_demo --reset --base-url http://localhost:8000 --email admin@demo.azienda --password 'change-me'
        </pre>
        <Button size="sm" className="mt-3" onClick={copySeedCmd}>Copy seed command</Button>
      </Card>
    </div>
  );
}
