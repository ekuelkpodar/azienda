import { Link } from "react-router-dom";
import { Card, PageHeader, PlannedBadge } from "../components/ui";

/**
 * Honest placeholder for modules whose API contract is not in API.md yet.
 * No fake data, no fake buttons — the planned surface is described, all
 * controls are visibly Planned/disabled.
 */
export function PlannedModule({
  title,
  subtitle,
  contractNote,
  plannedSurface,
  demoNote,
}: {
  title: string;
  subtitle: string;
  contractNote: string;
  plannedSurface: { name: string; detail: string }[];
  demoNote?: string;
}) {
  return (
    <div>
      <PageHeader
        title={title}
        subtitle={subtitle}
        actions={<PlannedBadge reason={contractNote} />}
      />
      <Card className="mb-4 border-dashed">
        <h2 className="mb-1 text-sm font-semibold">Status</h2>
        <p className="text-sm text-neutral-600">{contractNote}</p>
        <p className="mt-2 text-xs text-neutral-500">
          Per the no-fake-functionality rule, this page renders no data and no working controls
          until the backend contract lands. Nothing here pretends to work.
        </p>
      </Card>
      <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-neutral-500">
        Planned surface
      </h2>
      <div className="grid gap-3 md:grid-cols-2">
        {plannedSurface.map((s) => (
          <Card key={s.name}>
            <div className="flex items-center justify-between gap-2">
              <h3 className="text-sm font-semibold">{s.name}</h3>
              <PlannedBadge />
            </div>
            <p className="mt-1 text-xs text-neutral-500">{s.detail}</p>
          </Card>
        ))}
      </div>
      {demoNote && (
        <Card className="mt-4">
          <h2 className="mb-1 text-sm font-semibold">Signature demo path</h2>
          <p className="text-xs text-neutral-500">{demoNote}</p>
        </Card>
      )}
      <p className="mt-4 text-xs text-neutral-500">
        Meanwhile: <Link to="/onboarding" className="underline">onboarding</Link> tracks which
        modules are live.
      </p>
    </div>
  );
}
