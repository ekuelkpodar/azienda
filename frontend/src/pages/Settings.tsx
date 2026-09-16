import { useState } from "react";
import { api } from "../api/client";
import { useApi, useMutation } from "../api/hooks";
import { useAuth } from "../auth/AuthContext";
import {
  Badge,
  Button,
  Card,
  Field,
  Input,
  PageHeader,
  QueryView,
  Tabs,
  TextArea,
  useToast,
} from "../components/ui";

type Tab = "workspace" | "profile" | "api-keys";

function Workspace() {
  const { push } = useToast();
  const tenant = useApi(() => api.getTenant());
  const [settingsJson, setSettingsJson] = useState("");
  const [editing, setEditing] = useState(false);
  const save = useMutation((s: Record<string, unknown>) => api.updateTenant(s));

  async function onSave(e: React.FormEvent) {
    e.preventDefault();
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(settingsJson || "{}") as Record<string, unknown>;
    } catch {
      push("error", "Settings are not valid JSON.");
      return;
    }
    const t = await save.mutate(parsed);
    if (t) {
      push("success", "Workspace settings updated.");
      setEditing(false);
      tenant.reload();
    } else push("error", save.error?.body.message ?? "Update failed (admin role required).");
  }

  return (
    <QueryView query={tenant} context="GET /tenants/me">
      {(t) => (
        <Card>
          <dl className="space-y-2 text-sm">
            <div className="flex justify-between"><dt className="text-neutral-500">Name</dt><dd className="font-medium">{t.name}</dd></div>
            <div className="flex justify-between"><dt className="text-neutral-500">Slug</dt><dd className="font-mono text-xs">{t.slug}</dd></div>
          </dl>
          {!editing ? (
            <div className="mt-4">
              <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-neutral-500">Settings (JSON)</h3>
              <pre className="max-h-64 overflow-auto rounded-md bg-neutral-50 p-3 text-xs">{JSON.stringify(t.settings ?? {}, null, 2)}</pre>
              <Button size="sm" className="mt-3" onClick={() => { setSettingsJson(JSON.stringify(t.settings ?? {}, null, 2)); setEditing(true); }}>
                Edit settings
              </Button>
            </div>
          ) : (
            <form onSubmit={(e) => void onSave(e)} className="mt-4 space-y-3">
              <Field label="Settings (JSON)" hint="PATCH /tenants/me — admin role required.">
                <TextArea rows={8} value={settingsJson} onChange={(e) => setSettingsJson(e.target.value)} className="font-mono text-xs" />
              </Field>
              <div className="flex gap-2">
                <Button type="submit" variant="primary" size="sm" disabled={save.loading}>Save</Button>
                <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>Cancel</Button>
              </div>
            </form>
          )}
        </Card>
      )}
    </QueryView>
  );
}

function Profile() {
  const { me } = useAuth();
  if (!me) return null;
  return (
    <Card>
      <dl className="space-y-2 text-sm">
        <div className="flex justify-between"><dt className="text-neutral-500">Name</dt><dd className="font-medium">{me.user.display_name}</dd></div>
        <div className="flex justify-between"><dt className="text-neutral-500">Email</dt><dd>{me.user.email}</dd></div>
        <div className="flex justify-between"><dt className="text-neutral-500">Workspace</dt><dd>{me.tenant.name}</dd></div>
        <div className="flex justify-between"><dt className="text-neutral-500">Roles</dt><dd>{me.roles.map((r) => <Badge key={r}>{r}</Badge>)}</dd></div>
      </dl>
      <p className="mt-3 text-xs text-neutral-500">
        Profile edits are not in the API contract yet — ask your admin to update your record.
      </p>
    </Card>
  );
}

function ApiKeys() {
  const { push } = useToast();
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState("crm.read tasks.read");
  const [created, setCreated] = useState<{ id: string; key: string } | null>(null);
  const [revokeId, setRevokeId] = useState("");
  const create = useMutation(() => api.createApiKey(name, scopes.split(/\s+/).filter(Boolean)));
  const revoke = useMutation((id: string) => api.revokeApiKey(id));

  async function onCreate(e: React.FormEvent) {
    e.preventDefault();
    const k = await create.mutate(undefined);
    if (k) {
      setCreated(k);
      push("success", "API key created — copy it now, it will not be shown again.");
      setName("");
    } else push("error", create.error?.body.message ?? "Creation failed (admin role required).");
  }
  async function onRevoke(e: React.FormEvent) {
    e.preventDefault();
    await revoke.mutate(revokeId);
    if (!revoke.error) { push("success", "Key revoked."); setRevokeId(""); }
    else push("error", revoke.error.body.message);
  }

  return (
    <div className="space-y-4">
      <Card>
        <h2 className="mb-1 text-sm font-semibold">Create scoped service key</h2>
        <p className="mb-3 text-xs text-neutral-500">Agents and services authenticate with tenant-scoped keys — never ambient authority.</p>
        <form onSubmit={(e) => void onCreate(e)} className="flex flex-wrap items-end gap-3">
          <Field label="Key name"><Input required value={name} onChange={(e) => setName(e.target.value)} placeholder="etl-worker" /></Field>
          <Field label="Scopes (space-separated)" hint="e.g. crm.read tasks.read">
            <Input value={scopes} onChange={(e) => setScopes(e.target.value)} className="w-64" />
          </Field>
          <Button type="submit" variant="primary" size="sm" disabled={create.loading}>Create key</Button>
        </form>
        {created && (
          <div className="mt-3 rounded-md border border-amber-300 bg-amber-50 p-3">
            <p className="text-xs font-semibold text-amber-900">Copy now — this is the only time the secret is shown:</p>
            <code className="mt-1 block break-all rounded bg-white p-2 text-xs">{created.key}</code>
          </div>
        )}
      </Card>
      <Card>
        <h2 className="mb-1 text-sm font-semibold">Revoke a key</h2>
        <p className="mb-3 text-xs text-neutral-500">API.md defines no list-keys endpoint; paste the key id from creation.</p>
        <form onSubmit={(e) => void onRevoke(e)} className="flex items-end gap-2">
          <Field label="Key id"><Input value={revokeId} onChange={(e) => setRevokeId(e.target.value)} placeholder="key id" className="w-64" /></Field>
          <Button type="submit" variant="danger" size="sm" disabled={revoke.loading || !revokeId}>Revoke</Button>
        </form>
      </Card>
    </div>
  );
}

export default function Settings() {
  const [tab, setTab] = useState<Tab>("workspace");
  return (
    <div>
      <PageHeader title="Settings" subtitle="Workspace, profile, and service credentials." />
      <Tabs
        tabs={[
          { key: "workspace", label: "Workspace" },
          { key: "profile", label: "Profile" },
          { key: "api-keys", label: "API keys" },
        ]}
        active={tab}
        onChange={setTab}
      />
      {tab === "workspace" && <Workspace />}
      {tab === "profile" && <Profile />}
      {tab === "api-keys" && <ApiKeys />}
    </div>
  );
}
