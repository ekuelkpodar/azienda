import { useState } from "react";
import { api, asPage } from "../api/client";
import { useApi, useMutation } from "../api/hooks";
import type { Approval } from "../api/types";
import {
  Badge,
  Button,
  DataTable,
  EmptyState,
  Field,
  Input,
  Modal,
  PageHeader,
  QueryView,
  Select,
  TextArea,
  useToast,
} from "../components/ui";

const TONE: Record<string, "green" | "amber" | "neutral" | "red" | "blue"> = {
  pending: "amber",
  approved: "green",
  denied: "red",
  expired: "neutral",
  escalated: "blue",
};

function riskTone(score: number | null | undefined): "green" | "amber" | "red" | "neutral" {
  if (score == null) return "neutral";
  if (score >= 0.7) return "red";
  if (score >= 0.4) return "amber";
  return "green";
}

function ApprovalDetail({ approval, onClose, reload }: { approval: Approval; onClose: () => void; reload: () => void }) {
  const { push } = useToast();
  const detail = useApi(() => api.getApproval(approval.id), [approval.id]);
  const [note, setNote] = useState("");
  const [escalateTo, setEscalateTo] = useState("");
  const decide = useMutation(({ approved, n }: { approved: boolean; n?: string }) => api.decideApproval(approval.id, approved, n));
  const escalate = useMutation((to?: string) => api.escalateApproval(approval.id, to));

  async function onDecide(approved: boolean) {
    const a = await decide.mutate({ approved, n: note || undefined });
    if (a) {
      push("success", approved ? "Approved — the agent action may proceed." : "Denied — the agent action is blocked (fail closed).");
      reload();
      onClose();
    } else {
      push("error", decide.error?.body.message ?? "Decision failed.");
    }
  }
  async function onEscalate() {
    const a = await escalate.mutate(escalateTo || undefined);
    if (a) {
      push("success", "Escalated to another approver.");
      reload();
      onClose();
    } else {
      push("error", escalate.error?.body.message ?? "Escalation failed.");
    }
  }

  const current = detail.data ?? approval;
  const isPending = current.status === "pending";

  return (
    <Modal title={`Approval ${approval.id.slice(0, 8)}…`} onClose={onClose} wide>
      <div className="space-y-4 text-sm">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={TONE[current.status]}>{current.status}</Badge>
          <Badge tone={riskTone(current.risk_score)}>
            risk {current.risk_score != null ? current.risk_score.toFixed(2) : "n/a"}
          </Badge>
          {current.requester && <span className="text-neutral-500">requested by {current.requester}</span>}
          <span className="text-neutral-400">{new Date(current.created_at).toLocaleString()}</span>
        </div>
        <div>
          <h3 className="mb-1 font-semibold">Action</h3>
          <code className="rounded bg-neutral-100 px-2 py-1 text-xs">{current.action}</code>
        </div>
        {current.policy_reasons && current.policy_reasons.length > 0 && (
          <div>
            <h3 className="mb-1 font-semibold">Policy reasons</h3>
            <ul className="list-disc space-y-1 pl-5 text-neutral-700">
              {current.policy_reasons.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          </div>
        )}
        <div>
          <h3 className="mb-1 font-semibold">Arguments</h3>
          <pre className="max-h-56 overflow-auto rounded-md bg-neutral-50 p-3 text-xs">
            {JSON.stringify(current.args ?? {}, null, 2)}
          </pre>
        </div>
        {isPending ? (
          <>
            <Field label="Decision note (recorded in the audit ledger)">
              <TextArea value={note} onChange={(e) => setNote(e.target.value)} rows={2} placeholder="Reason for your decision…" />
            </Field>
            {decide.error && <p className="text-xs text-red-700">{decide.error.body.message}</p>}
            <div className="flex flex-wrap gap-2">
              <Button variant="primary" onClick={() => void onDecide(true)} disabled={decide.loading}>
                Approve
              </Button>
              <Button variant="danger" onClick={() => void onDecide(false)} disabled={decide.loading}>
                Deny
              </Button>
            </div>
            <div className="border-t border-neutral-200 pt-3">
              <Field label="Escalate to (email or user id, optional)">
                <div className="flex gap-2">
                  <Input value={escalateTo} onChange={(e) => setEscalateTo(e.target.value)} placeholder="approver@company.com" />
                  <Button size="sm" onClick={() => void onEscalate()} disabled={escalate.loading}>Escalate</Button>
                </div>
              </Field>
            </div>
          </>
        ) : (
          <p className="text-neutral-500">
            Decided {current.decided_at ? new Date(current.decided_at).toLocaleString() : ""}. Decisions are final and audited.
          </p>
        )}
      </div>
    </Modal>
  );
}

export default function Approvals() {
  const [status, setStatus] = useState("pending");
  const [selected, setSelected] = useState<Approval | null>(null);
  const approvals = useApi(() => api.listApprovals(status ? { status } : undefined).then(asPage), [status]);

  return (
    <div>
      <PageHeader
        title="Approvals"
        subtitle="The human queue: every agent action that policy flagged require_approval waits here. Timeouts deny by default (fail closed)."
        actions={
          <Select aria-label="Filter by status" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="pending">Pending</option>
            <option value="">All</option>
            <option value="approved">Approved</option>
            <option value="denied">Denied</option>
            <option value="expired">Expired</option>
            <option value="escalated">Escalated</option>
          </Select>
        }
      />
      <QueryView query={approvals} context="GET /approvals">
        {(page) =>
          page.items.length === 0 ? (
            <EmptyState
              title={status === "pending" ? "Queue is clear" : "No approvals"}
              hint="Policy-gated agent actions (outreach drafts, spend, bulk sends) land here for a human decision."
            />
          ) : (
            <DataTable
              columns={[
                { key: "action", header: "Action", render: (a) => <code className="text-xs">{a.action}</code> },
                {
                  key: "risk",
                  header: "Risk",
                  render: (a) => (
                    <Badge tone={riskTone(a.risk_score)}>
                      {a.risk_score != null ? a.risk_score.toFixed(2) : "n/a"}
                    </Badge>
                  ),
                },
                { key: "requester", header: "Requested by", render: (a) => a.requester ?? "—" },
                { key: "status", header: "Status", render: (a) => <Badge tone={TONE[a.status]}>{a.status}</Badge> },
                { key: "at", header: "Requested", render: (a) => new Date(a.created_at).toLocaleString() },
              ]}
              rows={page.items}
              onRowClick={setSelected}
              caption="Approval queue"
            />
          )
        }
      </QueryView>
      {selected && <ApprovalDetail approval={selected} onClose={() => setSelected(null)} reload={approvals.reload} />}
    </div>
  );
}
