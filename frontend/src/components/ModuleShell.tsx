/**
 * Honest placeholder shell for MVP modules.
 * Each page renders this until its API contract is implemented —
 * no fake data, no fake functionality.
 */
export default function ModuleShell({
  title,
  contract,
}: {
  title: string;
  contract: string;
}) {
  return (
    <div className="mx-auto max-w-2xl rounded-xl border border-dashed border-neutral-300 bg-white p-10 text-center">
      <h1 className="text-2xl font-semibold">{title}</h1>
      <p className="mt-3 text-sm text-neutral-600">
        MVP module — API contract defined (<code className="rounded bg-neutral-100 px-1">{contract}</code>),
        implementation in progress.
      </p>
      <p className="mt-2 text-xs text-neutral-400">
        This shell renders no data. Builders implement against <code>API.md</code>.
      </p>
    </div>
  );
}
