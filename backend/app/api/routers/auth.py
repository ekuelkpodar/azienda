"""Router: auth. Contract: API.md §2 (+ POST /auth/register, a documented
addition: self-serve signup creating tenant + owner).

No business logic here; delegates to ``core.auth.AuthService``.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from app.api.deps import (
    get_auth_service,
    get_principal,
    get_settings,
    get_tenant_context,
    rate_limit,
    require_roles,
)
from app.core.auth import AuthService, TokenPair
from app.core.config import Settings
from app.core.contracts import TenantContext
from app.core.tenancy import Principal

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "azienda_refresh"


def _set_refresh_cookie(response: Response, refresh_token: str,
                        settings: Settings) -> None:
    response.set_cookie(
        REFRESH_COOKIE, refresh_token,
        max_age=settings.jwt_refresh_ttl_days * 86400,
        httponly=True,
        secure=settings.is_production,  # Secure only in prod (local dev is http)
        samesite="lax",
        path="/api/v1/auth")


class RegisterIn(BaseModel):
    tenant_name: str = Field(min_length=2, max_length=255)
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=72)
    display_name: str = Field(default="", max_length=255)


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=72)
    tenant_slug: str | None = Field(default=None, max_length=64)


class ApiKeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    scopes: list[str] = Field(default_factory=list)
    expires_days: int | None = Field(default=None, ge=1, le=3650)


def _session_out(user: Any, tenant: Any, pair: TokenPair,
                 roles: list[str]) -> dict[str, Any]:
    return {"access_token": pair.access_token,
            "token_type": "bearer",
            "expires_in_seconds": pair.expires_in_seconds,
            "user": {"id": user.id, "email": user.email,
                     "display_name": user.display_name, "roles": roles},
            "tenant": {"id": tenant.id, "name": tenant.name,
                       "slug": tenant.slug}}


@router.post("/register", status_code=201, dependencies=[Depends(rate_limit)])
async def register(body: RegisterIn, response: Response,
                   auth: AuthService = Depends(get_auth_service),
                   settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    tenant, user, pair = await auth.register_tenant(
        body.tenant_name, body.email, body.password, body.display_name)
    _set_refresh_cookie(response, pair.refresh_token, settings)
    return _session_out(user, tenant, pair, ["owner"])


@router.post("/login", dependencies=[Depends(rate_limit)])
async def login(body: LoginIn, response: Response,
                auth: AuthService = Depends(get_auth_service),
                settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    user, tenant, pair = await auth.login(body.email, body.password,
                                          body.tenant_slug)
    _set_refresh_cookie(response, pair.refresh_token, settings)
    roles = [r for r in (await auth._user_roles(user.id))]
    return _session_out(user, tenant, pair, roles)


@router.post("/refresh", dependencies=[Depends(rate_limit)])
async def refresh(request: Request, response: Response,
                  auth: AuthService = Depends(get_auth_service),
                  settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    raw = request.cookies.get(REFRESH_COOKIE)
    if not raw:
        # non-browser clients may send the refresh token in the body-adjacent header
        raw = request.headers.get("X-Refresh-Token")
    if not raw:
        from app.core import errors
        raise errors.UnauthenticatedError("missing refresh token")
    pair = await auth.refresh(raw)
    _set_refresh_cookie(response, pair.refresh_token, settings)
    return {"access_token": pair.access_token, "token_type": "bearer",
            "expires_in_seconds": pair.expires_in_seconds}


@router.post("/logout")
async def logout(request: Request, response: Response,
                 auth: AuthService = Depends(get_auth_service)) -> dict[str, Any]:
    raw = request.cookies.get(REFRESH_COOKIE) or request.headers.get("X-Refresh-Token")
    if raw:
        await auth.logout(raw)
    response.delete_cookie(REFRESH_COOKIE, path="/api/v1/auth")
    return {"ok": True}


@router.get("/me")
async def me(principal: Principal = Depends(get_principal),
             tenant: TenantContext = Depends(get_tenant_context),
             auth: AuthService = Depends(get_auth_service)) -> dict[str, Any]:
    user_view = None
    if principal.kind == "user" and principal.user_id:
        users = await auth.list_users(tenant.tenant_id)
        user_view = next((u for u in users if u["id"] == principal.user_id), None)
    return {"principal": {"kind": principal.kind, "user_id": principal.user_id,
                          "tenant_id": principal.tenant_id,
                          "roles": list(principal.roles),
                          "scopes": list(principal.scopes)},
            "user": user_view}


@router.post("/api-keys", dependencies=[Depends(require_roles("owner", "admin"))])
async def create_api_key(body: ApiKeyIn,
                         principal: Principal = Depends(get_principal),
                         tenant: TenantContext = Depends(get_tenant_context),
                         auth: AuthService = Depends(get_auth_service)) -> dict[str, Any]:
    view, presented = await auth.create_api_key(
        tenant.tenant_id, principal.user_id, body.name, body.scopes,
        body.expires_days)
    # The full key is returned EXACTLY once, at creation.
    return {**view, "api_key": presented}


@router.get("/api-keys", dependencies=[Depends(require_roles("owner", "admin"))])
async def list_api_keys(tenant: TenantContext = Depends(get_tenant_context),
                        auth: AuthService = Depends(get_auth_service)) -> dict[str, Any]:
    return {"items": await auth.list_api_keys(tenant.tenant_id)}


@router.delete("/api-keys/{key_id}",
               dependencies=[Depends(require_roles("owner", "admin"))])
async def revoke_api_key(key_id: str,
                         tenant: TenantContext = Depends(get_tenant_context),
                         auth: AuthService = Depends(get_auth_service)) -> dict[str, Any]:
    await auth.revoke_api_key(tenant.tenant_id, key_id)
    return {"ok": True}
