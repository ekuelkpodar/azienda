import { Route, Routes } from "react-router-dom";
import Layout, { RequireAuth } from "./components/Layout";
import { AuthProvider } from "./auth/AuthContext";
import { ToastProvider } from "./components/ui";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import CommandCenter from "./pages/CommandCenter";
import AgentConsole from "./pages/AgentConsole";
import CRM from "./pages/CRM";
import Tasks from "./pages/Tasks";
import Workflows from "./pages/Workflows";
import Approvals from "./pages/Approvals";
import Support from "./pages/Support";
import Marketing from "./pages/Marketing";
import Scheduling from "./pages/Scheduling";
import Finance from "./pages/Finance";
import Billing from "./pages/Billing";
import Admin from "./pages/Admin";
import Onboarding from "./pages/Onboarding";
import Settings from "./pages/Settings";

export default function App() {
  return (
    <AuthProvider>
      <ToastProvider>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/login" element={<Login />} />
            <Route
              index
              element={
                <RequireAuth>
                  <Dashboard />
                </RequireAuth>
              }
            />
            {[
              ["command-center", <CommandCenter />],
              ["agents", <AgentConsole />],
              ["crm", <CRM />],
              ["tasks", <Tasks />],
              ["workflows", <Workflows />],
              ["approvals", <Approvals />],
              ["support", <Support />],
              ["marketing", <Marketing />],
              ["scheduling", <Scheduling />],
              ["finance", <Finance />],
              ["billing", <Billing />],
              ["admin", <Admin />],
              ["onboarding", <Onboarding />],
              ["settings", <Settings />],
            ].map(([path, el]) => (
              <Route
                key={path as string}
                path={path as string}
                element={<RequireAuth>{el}</RequireAuth>}
              />
            ))}
          </Route>
        </Routes>
      </ToastProvider>
    </AuthProvider>
  );
}
