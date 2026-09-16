import { PlannedModule } from "../components/PlannedModule";

export default function Scheduling() {
  return (
    <PlannedModule
      title="Scheduling"
      subtitle="Calendars, bookings, and agent-arranged appointments."
      contractNote="No scheduling endpoints exist in API.md §1–15 yet (backend builder 3 is implementing the scheduling package). Calendar views, booking links, and agent booking actions will be wired once the contract lands."
      plannedSurface={[
        { name: "Calendar", detail: "Team calendars with agent-booked appointments marked and cost-attributed." },
        { name: "Booking links", detail: "Public booking pages; confirmations via comms (policy-gated sends)." },
        { name: "Agent booking", detail: "Agents propose times and book under policy — double-booking prevented, confirmations approved." },
      ]}
    />
  );
}
