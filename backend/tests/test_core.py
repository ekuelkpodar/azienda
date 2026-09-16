"""Unit tests: core security, tenancy, idempotency, rate limits, DLP."""
from __future__ import annotations

from datetime import UTC, datetime

import jwt
import pytest

from app.core import errors, security
from app.core.idempotency import IdempotencyStore, request_fingerprint
from app.core.ratelimit import InMemoryRateLimiter
from app.core.redact import redact_mapping, redact_text
from app.core.tenancy import Principal, assert_tenant_match, principal_to_tenant_context


def _jwt_settings():
    from app.core.config import Settings
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    s.jwt_secret = "unit-test-secret"
    s.jwt_access_ttl_minutes = 10
    return s


# ---------------------------------------------------------------- passwords
def test_password_hash_and_verify():
    h = security.hash_password("Str0ng!Passw0rd")
    assert h != "Str0ng!Passw0rd"
    assert security.verify_password("Str0ng!Passw0rd", h)
    assert not security.verify_password("wrong-password", h)


def test_empty_password_rejected():
    with pytest.raises(ValueError):
        security.hash_password("")


# ---------------------------------------------------------------- JWT
def test_jwt_issue_and_verify():
    s = _jwt_settings()
    token = security.issue_access_token(s, "user-1", "tenant-1", ("owner",))
    claims = security.decode_access_token(s, token)
    assert claims.user_id == "user-1"
    assert claims.tenant_id == "tenant-1"
    assert "owner" in claims.roles


def test_jwt_wrong_secret_rejected():
    s = _jwt_settings()
    token = security.issue_access_token(s, "u", "t", ("member",))
    other = _jwt_settings()
    other.jwt_secret = "different-secret"
    with pytest.raises(security.TokenInvalidError):
        security.decode_access_token(other, token)


def test_jwt_expired_rejected():
    s = _jwt_settings()
    s.jwt_access_ttl_minutes = -1  # already expired at issue time
    token = security.issue_access_token(s, "u", "t", ("member",))
    with pytest.raises(security.TokenExpiredError):
        security.decode_access_token(s, token)


def test_jwt_tenant_claim_roundtrip():
    s = _jwt_settings()
    a = security.issue_access_token(s, "u", "tenant-a", ("member",))
    b = security.issue_access_token(s, "u", "tenant-b", ("member",))
    assert security.decode_access_token(s, a).tenant_id == "tenant-a"
    assert security.decode_access_token(s, b).tenant_id == "tenant-b"


def test_jwt_expiry_is_tz_aware_short_lived():
    s = _jwt_settings()
    token = security.issue_access_token(s, "u", "t", ("m",))
    payload = jwt.decode(token, options={"verify_signature": False})
    exp = datetime.fromtimestamp(payload["exp"], tz=UTC)
    assert exp.tzinfo is not None
    # ADR-008: access tokens live 5–15 minutes.
    ttl = (exp - datetime.now(UTC)).total_seconds() / 60
    assert 5 <= ttl <= 15


# ---------------------------------------------------------------- refresh tokens (service-level)
@pytest.mark.asyncio
async def test_refresh_rotation_and_reuse_detection(db_session):
    from app.core.auth import AuthService
    from app.core.config import Settings
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    s.jwt_secret = "unit-test-secret"
    auth = AuthService(db_session, s)
    await auth.register_tenant("Acme", "owner@example.com", "Str0ng!Passw0rd")
    _, _, pair0 = await auth.login("owner@example.com", "Str0ng!Passw0rd")

    rotated = await auth.refresh(pair0.refresh_token)
    assert rotated.access_token != pair0.access_token
    assert rotated.refresh_token != pair0.refresh_token

    # Reusing the old (rotated-out) refresh token = theft signal: the whole
    # chain is revoked and every subsequent use fails.
    with pytest.raises(errors.UnauthenticatedError):
        await auth.refresh(pair0.refresh_token)
    with pytest.raises(errors.UnauthenticatedError):
        await auth.refresh(rotated.refresh_token)


@pytest.mark.asyncio
async def test_logout_revokes_token(db_session):
    from app.core.auth import AuthService
    from app.core.config import Settings
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    s.jwt_secret = "unit-test-secret"
    auth = AuthService(db_session, s)
    await auth.register_tenant("Acme", "owner2@example.com", "Str0ng!Passw0rd")
    _, _, pair = await auth.login("owner2@example.com", "Str0ng!Passw0rd")
    await auth.logout(pair.refresh_token)
    with pytest.raises(errors.UnauthenticatedError):
        await auth.refresh(pair.refresh_token)


# ---------------------------------------------------------------- API keys
@pytest.mark.asyncio
async def test_api_key_lifecycle(db_session):
    from app.core.auth import AuthService
    from app.core.config import Settings
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    s.jwt_secret = "unit-test-secret"
    auth = AuthService(db_session, s)
    tenant, user, _ = await auth.register_tenant("Acme", "o3@example.com",
                                                 "Str0ng!Passw0rd")
    view, presented = await auth.create_api_key(tenant.id, user.id, "ci-key",
                                                ["tools:read"])
    assert presented.startswith("azk_")
    assert "key_hash" not in view  # hash never leaves the server

    principal = await auth.authenticate_api_key(presented)
    assert principal is not None
    assert principal.tenant_id == tenant.id
    assert principal.kind == "api_key"

    await auth.revoke_api_key(tenant.id, view["id"])
    assert await auth.authenticate_api_key(presented) is None


# ---------------------------------------------------------------- RBAC
def test_rbac_permission_map():
    assert security.has_permission(("owner",), "users.manage")
    assert security.has_permission(("owner",), "anything.at.all")  # wildcard
    assert security.has_permission(("admin",), "policies.manage")
    assert not security.has_permission(("member",), "policies.manage")
    assert security.has_permission(("member",), "approvals.request")
    assert security.has_permission(("agent",), "tasks.read")
    assert not security.has_permission(("agent",), "billing.manage")
    assert not security.has_permission(("member",), "billing.manage")
    assert security.has_any_role(("member", "admin"), "owner", "admin")
    assert not security.has_any_role(("member",), "owner", "admin")


# ---------------------------------------------------------------- tenancy
def test_assert_tenant_match_rejects_spoofed_tenant():
    p = Principal(kind="user", user_id="u1", tenant_id="t1", roles=("owner",))
    assert_tenant_match(p, "t1")  # matching claim: fine
    assert_tenant_match(p, None)  # absent claim: fine
    with pytest.raises(errors.CrossTenantError):
        assert_tenant_match(p, "t2")  # spoofed tenant_id: rejected


def test_principal_tenant_from_credential():
    p = Principal(kind="user", user_id="u1", tenant_id="t1", roles=("owner",))
    ctx = principal_to_tenant_context(p)
    assert ctx.tenant_id == "t1"  # tenant comes from the credential, always
    assert ctx.user_id == "u1"


# ---------------------------------------------------------------- idempotency
@pytest.mark.asyncio
async def test_idempotency_replay_and_conflict(db_session, test_settings):
    store = IdempotencyStore(db_session, test_settings)
    body = b'{"plan": "growth"}'
    first = await store.acquire("t1", "key-1", "POST",
                                "/api/v1/billing/subscription", body)
    assert isinstance(first, str)  # first use returns the key
    await store.store("t1", "key-1", "POST", "/api/v1/billing/subscription",
                      body, 200, {"ok": True})

    replayed = await store.acquire("t1", "key-1", "POST",
                                   "/api/v1/billing/subscription", body)
    assert replayed == (200, {"ok": True})

    with pytest.raises(errors.ConflictError):
        await store.acquire("t1", "key-1", "POST",
                            "/api/v1/billing/subscription", b'{"plan": "scale"}')


def test_request_fingerprint_stable():
    assert (request_fingerprint("POST", "/x", b"a")
            == request_fingerprint("POST", "/x", b"a"))
    assert (request_fingerprint("POST", "/x", b"a")
            != request_fingerprint("POST", "/x", b"b"))


# ---------------------------------------------------------------- rate limit
@pytest.mark.asyncio
async def test_rate_limiter_blocks_after_limit():
    limiter = InMemoryRateLimiter()
    for _ in range(5):
        assert (await limiter.check("k", 5, 60)).allowed
    result = await limiter.check("k", 5, 60)
    assert not result.allowed
    assert result.retry_after_seconds > 0


# ---------------------------------------------------------------- DLP redaction
def test_redact_masks_secrets():
    text = ("api_key=sk-live-abc1234567890 card 4111111111111111 ssn 123-45-6789 "
            "token=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c")
    out = redact_text(text)
    assert "sk-live-abc1234567890" not in out
    assert "4111111111111111" not in out
    assert "123-45-6789" not in out
    assert "eyJhbGciOiJIUzI1NiJ9" not in out
    assert "<REDACTED" in out


def test_redact_leaves_benign_text():
    assert redact_text("hello world, invoice #12345") == "hello world, invoice #12345"


def test_redact_mapping_redacts_sensitive_keys():
    out = redact_mapping({"api_key": "azk_secretvalue123456", "note": "hello"})
    assert out["api_key"] == "<REDACTED>"
    assert out["note"] == "hello"


# ---------------------------------------------------------------- time helper
def test_as_utc_normalizes_naive():
    from app.core.time import as_utc, utcnow
    naive = datetime(2026, 9, 15, 12, 0, 0)
    aware = as_utc(naive)
    assert aware is not None and aware.tzinfo is not None
    # Must not raise on SQLite-style naive datetimes:
    assert aware <= utcnow()
    assert as_utc(None) is None
    already = datetime.now(UTC)
    assert as_utc(already) is already
