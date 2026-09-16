"""API-level auth + tenancy tests: the security boundary, over HTTP.

Covers: registration/login/me, 401 paths, RBAC enforcement, privilege
escalation resistance, cross-tenant read/write isolation (403/404),
refresh-token rotation over the API, API-key auth, idempotency replay over
HTTP, rate limiting, and a static check that the migrations actually ship
Postgres RLS (the DB here is SQLite, so RLS is verified by inspection).
"""
from __future__ import annotations

import pathlib

import pytest

from app.billing.service import BillingService
from tests.conftest import auth_headers, register_tenant

MIGRATIONS = pathlib.Path(__file__).parent.parent / "alembic" / "versions"


def _tokens(reg: dict) -> tuple[str, str]:
    return reg["access_token"], reg["user"]["id"]


@pytest.mark.asyncio
async def test_register_login_me_roundtrip(client):
    reg = await register_tenant(client)
    token, _ = _tokens(reg)
    assert reg["user"]["email"] == "owner@example.com"

    r = await client.post("/api/v1/auth/login",
                          json={"email": "owner@example.com",
                                "password": "Str0ng!Passw0rd"})
    assert r.status_code == 200, r.text
    me = await client.get("/api/v1/auth/me",
                          headers=auth_headers(r.json()["access_token"]))
    assert me.status_code == 200
    assert me.json()["principal"]["tenant_id"] == reg["tenant"]["id"]
    assert "owner" in me.json()["principal"]["roles"]


@pytest.mark.asyncio
async def test_login_wrong_password_401(client):
    await register_tenant(client)
    r = await client.post("/api/v1/auth/login",
                          json={"email": "owner@example.com",
                                "password": "Wrong!Password1"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthenticated"


@pytest.mark.asyncio
async def test_no_token_401(client):
    r = await client.get("/api/v1/auth/me")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_bad_token_401(client):
    r = await client.get("/api/v1/auth/me",
                         headers=auth_headers("not.a.real.token"))
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_member_cannot_invite_users_403(client):
    reg = await register_tenant(client)
    owner_h = auth_headers(reg["access_token"])
    # Owner invites a member.
    r = await client.post("/api/v1/tenants/me/users", headers=owner_h,
                          json={"email": "member@example.com",
                                "password": "Str0ng!Passw0rd",
                                "roles": ["member"]})
    assert r.status_code in (200, 201), r.text
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "member@example.com", "password": "Str0ng!Passw0rd"})
    member_h = auth_headers(login.json()["access_token"])

    # Member tries to invite another user -> 403.
    r = await client.post("/api/v1/tenants/me/users", headers=member_h,
                          json={"email": "evil@example.com",
                                "password": "Str0ng!Passw0rd"})
    assert r.status_code == 403

    # Member tries to read admin-only policy surface -> 403.
    r = await client.get("/api/v1/admin/policies", headers=member_h)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_privilege_escalation_blocked(client):
    """A member cannot grant themselves owner, even via direct PATCH."""
    reg = await register_tenant(client)
    owner_h = auth_headers(reg["access_token"])
    r = await client.post("/api/v1/tenants/me/users", headers=owner_h,
                          json={"email": "member@example.com",
                                "password": "Str0ng!Passw0rd",
                                "roles": ["member"]})
    member_id = r.json()["id"]
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "member@example.com", "password": "Str0ng!Passw0rd"})
    member_h = auth_headers(login.json()["access_token"])

    r = await client.patch(f"/api/v1/tenants/me/users/{member_id}",
                           headers=member_h, json={"roles": ["owner"]})
    assert r.status_code == 403

    # And the role is unchanged.
    me = await client.get("/api/v1/auth/me", headers=member_h)
    assert "owner" not in me.json()["principal"]["roles"]


@pytest.mark.asyncio
async def test_cross_tenant_user_write_404(client):
    """Tenant B's admin cannot touch tenant A's users: scoped lookup -> 404."""
    reg_a = await register_tenant(client, name="Tenant A",
                                  email="a-owner@example.com")
    reg_b = await register_tenant(client, name="Tenant B",
                                  email="b-owner@example.com")
    users_a = await client.get("/api/v1/tenants/me/users",
                               headers=auth_headers(reg_a["access_token"]))
    victim_id = users_a.json()["items"][0]["id"]

    r = await client.patch(f"/api/v1/tenants/me/users/{victim_id}",
                           headers=auth_headers(reg_b["access_token"]),
                           json={"display_name": "pwned"})
    assert r.status_code in (403, 404), r.text


@pytest.mark.asyncio
async def test_cross_tenant_approval_invisible(client, db_session, test_settings):
    """Approvals created in tenant A are invisible to tenant B (404, not 403
    — we do not confirm or deny the existence of other tenants' objects)."""
    from app.core.contracts import ActionRequest, PolicyDecision, PolicyEffect, TenantContext
    from app.governance.approvals.store import ApprovalStoreImpl

    reg_a = await register_tenant(client, name="Tenant A",
                                  email="a-owner@example.com")
    reg_b = await register_tenant(client, name="Tenant B",
                                  email="b-owner@example.com")
    tenant_a_id = reg_a["tenant"]["id"]
    store = ApprovalStoreImpl(db_session, test_settings)
    approval = await store.request(
        PolicyDecision(PolicyEffect.REQUIRE_APPROVAL, None, reasons=("t",)),
        ActionRequest(tenant=TenantContext(tenant_id=tenant_a_id),
                      action="tool.crm.send_bulk_sms"),
        80.0, ())

    r = await client.get(f"/api/v1/approvals/{approval.approval_id}",
                         headers=auth_headers(reg_b["access_token"]))
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_refresh_rotation_over_api(client):
    reg = await register_tenant(client)
    refresh = reg.get("refresh_token") or client.cookies.get("azienda_refresh")
    assert refresh, "register must issue a refresh token (cookie)"
    h = {"X-Refresh-Token": refresh}

    r1 = await client.post("/api/v1/auth/refresh", headers=h)
    assert r1.status_code == 200, r1.text
    new_refresh = (r1.cookies.get("azienda_refresh")
                   or r1.headers.get("x-refresh-token"))
    assert new_refresh and new_refresh != refresh

    # Old refresh token is now dead (reuse detection). Clear the jar first:
    # the client holds the NEW cookie, and the endpoint prefers the cookie,
    # which would make this look like a legitimate rotation.
    client.cookies.clear()
    r2 = await client.post("/api/v1/auth/refresh",
                           headers={"X-Refresh-Token": refresh})
    assert r2.status_code == 401


@pytest.mark.asyncio
async def test_api_key_auth_over_http(client):
    reg = await register_tenant(client)
    owner_h = auth_headers(reg["access_token"])
    r = await client.post("/api/v1/auth/api-keys", headers=owner_h,
                          json={"name": "ci", "scopes": ["tools:read"]})
    assert r.status_code in (200, 201), r.text
    presented = r.json()["api_key"]

    me = await client.get("/api/v1/auth/me", headers={"X-API-Key": presented})
    assert me.status_code == 200
    assert me.json()["principal"]["kind"] == "api_key"

    # Revoke -> the key stops working.
    await client.delete(f"/api/v1/auth/api-keys/{r.json()['id']}",
                        headers=owner_h)
    me = await client.get("/api/v1/auth/me", headers={"X-API-Key": presented})
    assert me.status_code == 401


@pytest.mark.asyncio
async def test_idempotency_replay_over_http(client, db_session, test_settings):
    """Same Idempotency-Key + same body on a mutating POST replays the
    original response instead of double-applying."""
    await BillingService(db_session, test_settings).seed_plans()
    reg = await register_tenant(client)
    h = {**auth_headers(reg["access_token"]),
         "Idempotency-Key": "sub-key-1"}

    r1 = await client.post("/api/v1/billing/subscription", headers=h,
                           json={"plan_slug": "starter"})
    assert r1.status_code == 200, r1.text
    r2 = await client.post("/api/v1/billing/subscription", headers=h,
                           json={"plan_slug": "starter"})
    assert r2.status_code == 200
    assert r2.json() == r1.json()  # replayed, not re-executed

    # Same key, different body -> 409 conflict.
    r3 = await client.post("/api/v1/billing/subscription", headers=h,
                           json={"plan_slug": "growth"})
    assert r3.status_code == 409


@pytest.mark.asyncio
async def test_rate_limit_blocks_abuse(client, test_settings):
    from app.core.config import Settings
    from tests import conftest as cf

    low = Settings(_env_file=None)  # type: ignore[call-arg]
    low.database_url = test_settings.database_url
    low.jwt_secret = test_settings.jwt_secret
    low.rate_limit_per_minute = 3
    low.rate_limit_burst = 3
    cf._settings_holder["settings"] = low
    try:
        statuses = []
        for _ in range(8):
            r = await client.post(
                "/api/v1/auth/login",
                json={"email": "nobody@example.com",
                      "password": "Wrong!Password1"})
            statuses.append(r.status_code)
        assert 429 in statuses, f"expected 429s, got {statuses}"
    finally:
        cf._settings_holder["settings"] = test_settings


@pytest.mark.asyncio
async def test_admin_policy_crud_roundtrip(client):
    reg = await register_tenant(client)
    h = auth_headers(reg["access_token"])
    r = await client.post("/api/v1/admin/policies", headers=h, json={
        "name": "test-deny", "rules": [
            {"name": "deny-x", "effect": "deny", "priority": 900,
             "condition": {"action": "*nuke*"}, "reason": "test"}]})
    assert r.status_code in (200, 201), r.text
    policy_id = r.json()["id"]

    # Invalid rule is rejected at write time, not silently stored.
    r = await client.post("/api/v1/admin/policies", headers=h, json={
        "name": "bad", "rules": [{"name": "bad", "effect": "explode"}]})
    assert r.status_code == 400

    # Dry-run evaluation does not create approvals.
    r = await client.post("/api/v1/admin/policies/evaluate", headers=h,
                          json={"action": "tool.crm.nuke_everything"})
    assert r.status_code == 200
    assert r.json()["effect"] == "deny"
    assert "dry-run" in r.json()["note"]

    # Deactivate.
    r = await client.patch(f"/api/v1/admin/policies/{policy_id}", headers=h,
                           json={"is_active": False})
    assert r.status_code == 200
    assert r.json()["is_active"] is False


@pytest.mark.asyncio
async def test_audit_verify_endpoint(client):
    reg = await register_tenant(client)
    h = auth_headers(reg["access_token"])
    r = await client.post("/api/v1/audit/verify", headers=h, json={})
    assert r.status_code == 200
    assert r.json()["ok"] is True


@pytest.mark.asyncio
async def test_kill_switch_over_http(client):
    reg = await register_tenant(client)
    h = auth_headers(reg["access_token"])
    r = await client.post("/api/v1/budgets/kill-switch", headers=h,
                          json={"reason": "test incident"})
    assert r.status_code == 200
    assert r.json()["spend_frozen"] is True
    r = await client.post("/api/v1/budgets/kill-switch/release", headers=h)
    assert r.status_code == 200
    assert r.json()["spend_frozen"] is False


def test_migrations_ship_postgres_rls():
    """Static verification: every tenant-table migration routes through the
    RLS helper, and the helper emits real FORCE ROW LEVEL SECURITY policy SQL.

    Postgres is unavailable in this environment, so RLS cannot be
    integration-tested here; this test pins the wiring so a future
    refactor cannot silently drop it.
    """
    helper = (MIGRATIONS.parent / "helpers.py").read_text()
    assert "ENABLE ROW LEVEL SECURITY" in helper
    assert "FORCE ROW LEVEL SECURITY" in helper
    assert "tenant_isolation" in helper
    for path in ("0001_tenancy_auth.py", "0002_governance.py",
                 "0003_billing.py"):
        src = (MIGRATIONS / path).read_text()
        assert "apply_rls(" in src, path  # every migration routes through it
        assert "TENANT_TABLES" in src, path
        assert "drop_rls(" in src, path  # downgrade removes the policies
    # Spot-check: the tables that must be isolated are actually registered.
    t1 = (MIGRATIONS / "0001_tenancy_auth.py").read_text()
    assert '"users"' in t1 and '"api_keys"' in t1 and '"refresh_tokens"' in t1
    t2 = (MIGRATIONS / "0002_governance.py").read_text()
    assert '"approvals"' in t2 and '"audit_ledger"' in t2
    t3 = (MIGRATIONS / "0003_billing.py").read_text()
    assert '"credit_pools"' in t3 and '"billing_invoices"' in t3
