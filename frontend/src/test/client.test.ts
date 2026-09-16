import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiClientError, AziendaClient, asPage } from "../api/client";

/* Minimal sessionStorage mock (node has none). */
function installStorage() {
  const store = new Map<string, string>();
  (globalThis as unknown as Record<string, unknown>).sessionStorage = {
    getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
    setItem: (k: string, v: string) => store.set(k, v),
    removeItem: (k: string) => store.delete(k),
  };
  // crypto.randomUUID exists in node 24; ensure it does.
  if (!globalThis.crypto) throw new Error("crypto unavailable");
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("AziendaClient", () => {
  beforeEach(() => {
    installStorage();
    vi.unstubAllGlobals();
  });

  it("sends the Bearer token from storage and parses JSON", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, { ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    const client = new AziendaClient();
    client.setToken("tok-123");
    const data = await client.request<{ ok: boolean }>("GET", "/tenants/me");

    expect(data).toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/v1/tenants/me");
    expect((init.headers as Record<string, string>)["Authorization"]).toBe("Bearer tok-123");
    expect((init.headers as Record<string, string>)["Idempotency-Key"]).toBeUndefined();
  });

  it("adds Idempotency-Key on POST and honors a caller-supplied key", async () => {
    const seen: Record<string, string>[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_u: string, init?: RequestInit) => {
        seen.push(init?.headers as Record<string, string>);
        return jsonResponse(200, { id: "1" });
      }),
    );
    const client = new AziendaClient();
    await client.request("POST", "/tasks", { body: { title: "t" } });
    await client.request("POST", "/tasks", { body: { title: "t" }, idempotencyKey: "custom-key" });
    expect(seen[0]["Idempotency-Key"]).toMatch(/^[0-9a-f-]{36}$/);
    expect(seen[1]["Idempotency-Key"]).toBe("custom-key");
  });

  it("encodes query params and skips empty values", async () => {
    const urls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (u: string) => {
        urls.push(u);
        return jsonResponse(200, { items: [], next_page_token: null });
      }),
    );
    const client = new AziendaClient();
    await client.listLeads({ status: "new", owner: undefined, page_size: 10 });
    expect(urls[0]).toBe("/api/v1/crm/leads?status=new&page_size=10");
  });

  it("parses the API error envelope into ApiClientError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse(403, { error: { code: "policy_denied", message: "Denied by policy", trace_id: "abc" } }),
      ),
    );
    const client = new AziendaClient();
    const err = await client.request("POST", "/tools/x/invoke", { body: {} }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiClientError);
    expect((err as ApiClientError).status).toBe(403);
    expect((err as ApiClientError).code).toBe("policy_denied");
    expect((err as ApiClientError).body.trace_id).toBe("abc");
  });

  it("refreshes once on 401 and retries with the new token", async () => {
    const calls: { url: string; auth?: string }[] = [];
    let n = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (u: string, init?: RequestInit) => {
        n += 1;
        calls.push({ url: u, auth: (init?.headers as Record<string, string>)?.["Authorization"] });
        if (u === "/api/v1/auth/refresh") return jsonResponse(200, { access_token: "tok-new" });
        if (n === 1) return jsonResponse(401, { error: { code: "unauthenticated", message: "expired" } });
        return jsonResponse(200, { ok: true });
      }),
    );
    const client = new AziendaClient();
    client.setToken("tok-old");
    const data = await client.request<{ ok: boolean }>("GET", "/command-center/summary");
    expect(data).toEqual({ ok: true });
    expect(calls.map((c) => c.url)).toEqual([
      "/api/v1/command-center/summary",
      "/api/v1/auth/refresh",
      "/api/v1/command-center/summary",
    ]);
    expect(calls[2].auth).toBe("Bearer tok-new");
    expect(client.accessToken).toBe("tok-new");
  });

  it("clears the session when refresh fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (u: string) => {
        if (u === "/api/v1/auth/refresh") return jsonResponse(401, { error: { code: "unauthenticated", message: "revoked" } });
        return jsonResponse(401, { error: { code: "unauthenticated", message: "expired" } });
      }),
    );
    const client = new AziendaClient();
    client.setToken("tok-old");
    const err = await client.request("GET", "/auth/me").catch((e) => e);
    expect(err).toBeInstanceOf(ApiClientError);
    expect(client.accessToken).toBeNull();
    expect(client.isAuthenticated).toBe(false);
  });

  it("login stores the token; logout clears it", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (u: string) => {
        if (u.endsWith("/auth/login")) return jsonResponse(200, { access_token: "tok-abc", token_type: "bearer" });
        return new Response(null, { status: 204 });
      }),
    );
    const client = new AziendaClient();
    await client.login("a@b.c", "pw");
    expect(client.accessToken).toBe("tok-abc");
    await client.logout();
    expect(client.accessToken).toBeNull();
  });

  it("intentQuery rejects honestly — no fake NL endpoint", async () => {
    const client = new AziendaClient();
    const err = await client.intentQuery("hello").catch((e) => e);
    expect(err).toBeInstanceOf(ApiClientError);
    expect((err as ApiClientError).code).toBe("not_implemented");
  });
});

describe("asPage", () => {
  it("wraps bare arrays and passes pages through", () => {
    expect(asPage([{ id: "1" }])).toEqual({ items: [{ id: "1" }], next_page_token: null });
    const page = { items: [{ id: "1" }], next_page_token: "tok" };
    expect(asPage(page)).toBe(page);
  });
});
