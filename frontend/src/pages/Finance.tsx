import { PlannedModule } from "../components/PlannedModule";

export default function Finance() {
  return (
    <PlannedModule
      title="Finance"
      subtitle="Invoices, expenses, and reports — with the NEXORA ERP seam for financial truth."
      contractNote="No finance endpoints exist in API.md §1–15 yet (backend builder 3 is implementing the finance package). Per ARCHITECTURE.md, financial truth lives in NEXORA ERP; Azienda's finance module is foundational with an explicit NexoraSeam, and consequential financial actions route through NEXORA's approval path."
      plannedSurface={[
        { name: "Invoices", detail: "Draft, send (approval-gated), track payment status. Posting truth stays in NEXORA." },
        { name: "Expenses", detail: "Capture, categorize, approve — agent-assisted extraction with human review." },
        { name: "Reports", detail: "P&L and cash views composed from the NEXORA seam; agent-generated commentary labeled as such." },
      ]}
      demoNote="The demo invoice fixture is prepared in backend/app/cli/seed_demo.py and will be seeded once the finance contract lands."
    />
  );
}
