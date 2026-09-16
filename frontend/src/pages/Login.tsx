import { useState } from "react";
import { useLocation, useNavigate, Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { Button, Card, Field, Input } from "../components/ui";

export default function Login() {
  const { login, loading, error, me } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);

  const from = (location.state as { from?: string } | null)?.from ?? "/";

  if (me && !loading) {
    navigate(from, { replace: true });
    return null;
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    const ok = await login(email.trim(), password);
    setBusy(false);
    if (ok) navigate(from, { replace: true });
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-neutral-50 p-4">
      <Card className="w-full max-w-md">
        <h1 className="text-xl font-bold tracking-tight">Azienda</h1>
        <p className="mt-1 text-sm text-neutral-500">Sign in to your workspace</p>
        <form onSubmit={onSubmit} className="mt-6 space-y-4">
          <Field label="Email">
            <Input
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@company.com"
            />
          </Field>
          <Field label="Password">
            <Input
              type="password"
              required
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </Field>
          {error && (
            <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
              {error}
            </p>
          )}
          <Button type="submit" variant="primary" disabled={busy || loading} className="w-full">
            {busy ? "Signing in…" : "Sign in"}
          </Button>
        </form>
        <div className="mt-6 border-t border-neutral-200 pt-4 text-xs text-neutral-500">
          <p className="font-medium text-neutral-700">No self-service registration in this build.</p>
          <p className="mt-1">
            Accounts are created by invitation (<code className="rounded bg-neutral-100 px-1">POST /tenants/me/users</code>).
            Seed a demo workspace with{" "}
            <code className="rounded bg-neutral-100 px-1">backend/app/cli/seed_demo.py</code>, then sign in
            with the seeded admin credentials.
          </p>
          <p className="mt-2">
            <Link to="/" className="underline">Back</Link>
          </p>
        </div>
      </Card>
    </div>
  );
}
