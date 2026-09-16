import { PlannedModule } from "../components/PlannedModule";

export default function Support() {
  return (
    <PlannedModule
      title="Support"
      subtitle="Ticket inbox, SLA tracking, and agent-assisted resolution."
      contractNote="No support endpoints exist in API.md §1–15 yet (backend builder 3 is implementing the support package). The inbox, ticket detail, KB lookup, and resolve/escalate actions will be wired once the contract lands."
      plannedSurface={[
        { name: "Ticket inbox", detail: "Filter by status, priority, SLA risk, assignee. Bulk actions are policy-gated." },
        { name: "Ticket detail", detail: "Conversation thread, linked contact/org, SLA clock, resolution actions." },
        { name: "KB-assisted drafts", detail: "Agent drafts a reply grounded in knowledge-base articles; sending needs approval." },
        { name: "Escalation", detail: "Escalate to a human queue with full context and policy trail." },
      ]}
      demoNote="Signature demo 2 (support ticket: identify → KB → draft → authorized action / escalate) will be drivable end-to-end from this page once the support contract lands. The KB articles fixture is already prepared in backend/app/cli/seed_demo.py."
    />
  );
}
