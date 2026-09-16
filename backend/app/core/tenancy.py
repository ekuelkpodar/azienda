"""TenantContext resolution from the request + RLS session variables.

The tenant ALWAYS comes from the credential (JWT claim or API key) — never from
a client-supplied parameter. Any ``tenant_id`` the client sends that disagrees
with the credential is a 403 (fail closed).
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core import errors
from app.core.contracts import TenantContext


@dataclass(frozen=True)
class Principal:
    """Authenticated caller: human user, API key, or agent grant."""

    kind: str  # "user" | "api_key"
    user_id: str | None
    tenant_id: str
    roles: tuple[str, ...]
    scopes: tuple[str, ...] = ()
    api_key_id: str | None = None


def principal_to_tenant_context(principal: Principal) -> TenantContext:
    return TenantContext(tenant_id=principal.tenant_id,
                         user_id=principal.user_id,
                         roles=tuple(principal.roles))


def assert_tenant_match(principal: Principal, claimed_tenant_id: str | None) -> None:
    """Reject client-supplied tenant ids that disagree with the credential."""
    if claimed_tenant_id and claimed_tenant_id != principal.tenant_id:
        raise errors.CrossTenantError(
            "tenant_id in request does not match authenticated tenant",
            details={"authenticated_tenant": principal.tenant_id})


def scoped_query_tenant_id(principal: Principal) -> str:
    """The single tenant_id every repository query must filter on."""
    return principal.tenant_id
