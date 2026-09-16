import { PlannedModule } from "../components/PlannedModule";

export default function Marketing() {
  return (
    <PlannedModule
      title="Marketing"
      subtitle="Campaigns, audiences, and content — executed by agents, gated by policy."
      contractNote="No marketing endpoints exist in API.md §1–15 yet (backend builder 3 is implementing the marketing package). Campaign list/detail, audience builder, and launch actions will be wired once the contract lands."
      plannedSurface={[
        { name: "Campaigns", detail: "List, create, schedule. Launching a campaign requires human approval (bulk-send policy)." },
        { name: "Audiences", detail: "Segments built from CRM data; membership counts before send." },
        { name: "Content assets", detail: "Agent-drafted email/SMS/social copy with version history and approval trail." },
        { name: "Performance", detail: "Sends, opens, replies, and fully-loaded cost per campaign from the outcome ledger." },
      ]}
      demoNote="The demo campaign fixture is prepared in backend/app/cli/seed_demo.py and will be seeded once the marketing contract lands."
    />
  );
}
