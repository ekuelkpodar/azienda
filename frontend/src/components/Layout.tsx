import { NavLink, Navigate, Outlet, useLocation, useNavigate, Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { Button, PlannedBadge } from "./ui";

const NAV: { to: string; label: string; planned?: boolean }[] = [
  { to: "/", label: "Dashboard" },
  { to: "/command-center", label: "Command Center" },
  { to: "/agents", label: "Agent Console" },
  { to: "/crm", label: "CRM" },
  { to: "/tasks", label: "Tasks" },
  { to: "/workflows", label: "Workflows" },
  { to: "/approvals", label: "Approvals" },
  { to: "/support", label: "Support", planned: true },
  { to: "/marketing", label: "Marketing", planned: true },
  { to: "/scheduling", label: "Scheduling", planned: true },
  { to: "/finance", label: "Finance", planned: true },
  { to: "/billing", label: "Billing" },
  { to: "/admin", label: "Admin" },
  { to: "/onboarding", label: "Onboarding" },
  { to: "/settings", label: "Settings" },
];

export function RequireAuth({ children }: { children: React.ReactNode }) {
  const { me, loading } = useAuth();
  const location = useLocation();
  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-neutral-500">
        Loading session…
      </div>
    );
  }
  if (!me) {
    return <Navigate to="/login" state={{ from: location.pathname }} replace />;
  }
  return <>{children}</>;
}

function DemoBanner() {
  return (
    <div className="border-b border-amber-300 bg-amber-50 px-4 py-2 text-xs text-amber-900" role="status">
      <span className="font-semibold">Demo mode</span> — fictional data seeded by{" "}
      <code className="rounded bg-amber-100 px-1">backend/app/cli/seed_demo.py</code>. No real
      customers, money, or messages.{" "}
      <Link to="/onboarding" className="underline hover:text-amber-700">
        Reset instructions
      </Link>
    </div>
  );
}

export default function Layout() {
  const { me, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const isLogin = location.pathname === "/login";

  if (isLogin) return <Outlet />;

  return (
    <div className="flex min-h-screen bg-neutral-50 text-neutral-900">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:bg-white focus:p-2">
        Skip to content
      </a>
      <aside className="w-60 shrink-0 border-r border-neutral-200 bg-white p-4" aria-label="Primary">
        <div className="mb-6 px-2">
          <div className="text-xl font-bold tracking-tight">Azienda</div>
          <div className="text-xs text-neutral-500">AI Business OS</div>
          {me && (
            <div className="mt-2 truncate rounded-md bg-neutral-100 px-2 py-1 text-xs text-neutral-600" title={me.tenant.name}>
              {me.tenant.name}
            </div>
          )}
        </div>
        <nav className="space-y-1" aria-label="Modules">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) =>
                `flex items-center justify-between rounded-md px-3 py-2 text-sm ${
                  isActive ? "bg-neutral-900 font-medium text-white" : "text-neutral-700 hover:bg-neutral-100"
                }`
              }
            >
              <span>{item.label}</span>
              {item.planned && <PlannedBadge reason="API contract not yet defined — backend builder 3 is implementing this module." />}
            </NavLink>
          ))}
        </nav>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-neutral-200 bg-white px-6 py-3">
          <div className="text-sm text-neutral-500">
            {me ? (
              <>
                Signed in as <span className="font-medium text-neutral-800">{me.user.display_name || me.user.email}</span>
                {me.roles.length > 0 && <span className="ml-2 text-xs">({me.roles.join(", ")})</span>}
              </>
            ) : (
              "Not signed in"
            )}
          </div>
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="sm" onClick={() => navigate("/onboarding")}>
              Onboarding
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                void logout().then(() => navigate("/login"));
              }}
            >
              Sign out
            </Button>
          </div>
        </header>
        <DemoBanner />
        <main id="main" className="min-w-0 flex-1 p-6" tabIndex={-1}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}
