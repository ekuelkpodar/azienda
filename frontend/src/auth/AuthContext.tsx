import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { api, ApiClientError } from "../api/client";
import type { AuthMe } from "../api/types";

interface AuthState {
  me: AuthMe | null;
  loading: boolean;
  error: string | null;
  login: (email: string, password: string) => Promise<boolean>;
  logout: () => Promise<void>;
  refreshMe: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<AuthMe | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refreshMe = useCallback(async () => {
    if (!api.isAuthenticated) {
      setMe(null);
      setLoading(false);
      return;
    }
    try {
      const m = await api.me();
      setMe(m);
      setError(null);
    } catch (e) {
      if (e instanceof ApiClientError && (e.status === 401 || e.status === 403)) {
        setMe(null);
      } else {
        setError("Could not load session. The API may not be reachable.");
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshMe();
  }, [refreshMe]);

  const login = useCallback(async (email: string, password: string): Promise<boolean> => {
    setError(null);
    try {
      await api.login(email, password);
      const m = await api.me();
      setMe(m);
      return true;
    } catch (e) {
      if (e instanceof ApiClientError) {
        setError(e.status === 401 ? "Invalid email or password." : e.body.message);
      } else {
        setError("Login failed — the API may not be reachable.");
      }
      return false;
    }
  }, []);

  const logout = useCallback(async () => {
    await api.logout();
    setMe(null);
  }, []);

  const value = useMemo(
    () => ({ me, loading, error, login, logout, refreshMe }),
    [me, loading, error, login, logout, refreshMe],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
