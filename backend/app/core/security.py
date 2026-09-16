"""JWT issue/verify, password hashing, API-key handling.

Rules (ADR-008): short-lived access (5-15 min), rotating refresh with reuse
detection, never roll custom crypto. JWT via PyJWT; passwords via bcrypt.

Note on passlib: pyproject pins ``passlib[bcrypt]``, but passlib 1.7.4 is
incompatible with bcrypt>=4.1 (``__about__`` removal breaks its version probe —
CONFIRMED upstream issue). We call ``bcrypt`` directly instead: same primitive,
no shim. If passlib is ever pinned to a fixed release, revisit.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.core.config import Settings


def utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------- passwords
def hash_password(password: str, rounds: int = 12) -> str:
    """bcrypt hash. Raises ValueError on empty password (fail closed)."""
    if not password:
        raise ValueError("password must not be empty")
    pw = password.encode("utf-8")
    if len(pw) > 72:
        # bcrypt truncates at 72 bytes; refuse silently-weak hashes instead.
        raise ValueError("password must be at most 72 bytes")
    return bcrypt.hashpw(pw, bcrypt.gensalt(rounds=rounds)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------- JWT
@dataclass(frozen=True)
class AccessClaims:
    user_id: str
    tenant_id: str
    roles: tuple[str, ...]
    token_type: str = "access"  # noqa: S105


def issue_access_token(settings: Settings, user_id: str, tenant_id: str,
                       roles: tuple[str, ...] | list[str]) -> str:
    now = utcnow()
    payload = {
        "iss": settings.jwt_issuer,
        "sub": user_id,
        "tenant_id": tenant_id,
        "roles": list(roles),
        "type": "access",
        "jti": uuid.uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_access_ttl_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.require_jwt_secret(), algorithm=settings.jwt_algorithm)


def decode_access_token(settings: Settings, token: str) -> AccessClaims:
    """Verify signature + expiry and return typed claims. Raises on any problem."""
    try:
        payload = jwt.decode(token, settings.require_jwt_secret(),
                             algorithms=[settings.jwt_algorithm],
                             issuer=settings.jwt_issuer,
                             options={"require": ["exp", "iat", "iss", "sub"]})
    except jwt.ExpiredSignatureError as e:
        raise TokenExpiredError("access token expired") from e
    except jwt.InvalidTokenError as e:
        raise TokenInvalidError(f"invalid access token: {e}") from e
    if payload.get("type") != "access":
        raise TokenInvalidError("not an access token")
    tenant_id = payload.get("tenant_id")
    if not tenant_id:
        raise TokenInvalidError("access token missing tenant_id claim")
    return AccessClaims(user_id=str(payload["sub"]), tenant_id=str(tenant_id),
                        roles=tuple(payload.get("roles") or []))


class TokenError(Exception):
    """Base for token problems."""


class TokenExpiredError(TokenError):
    pass


class TokenInvalidError(TokenError):
    pass


# ------------------------------------------------- refresh tokens (opaque, DB-backed)
def new_refresh_token() -> str:
    """Opaque refresh token value. Only its SHA-256 is ever stored."""
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- API keys
def new_api_key(prefix: str = "azk_") -> tuple[str, str]:
    """Return (presented_key, stored_hash). The full key is shown exactly once."""
    secret = secrets.token_hex(32)
    presented = f"{prefix}{secret}"
    return presented, hash_token(presented)


# ---------------------------------------------------------------- RBAC
ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    # owner: everything in the tenant
    "owner": frozenset({"*"}),
    "admin": frozenset({
        "users.manage", "teams.manage", "roles.view",
        "policies.manage", "budgets.manage", "billing.manage",
        "audit.read", "apikeys.manage", "agents.manage",
        "approvals.decide", "tenant.settings",
    }),
    "member": frozenset({
        "roles.view", "audit.read.own", "approvals.request",
    }),
    # agent: service principal for the agent workforce; narrow by design
    "agent": frozenset({
        "approvals.request", "tasks.read", "tasks.update.own",
    }),
}


def has_permission(roles: tuple[str, ...] | list[str], permission: str) -> bool:
    for role in roles:
        perms = ROLE_PERMISSIONS.get(role, frozenset())
        if "*" in perms or permission in perms:
            return True
    return False


def has_any_role(roles: tuple[str, ...] | list[str], *required: str) -> bool:
    return any(r in roles for r in required)
