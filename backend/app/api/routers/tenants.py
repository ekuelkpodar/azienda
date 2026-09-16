"""Router: tenants & users. Contract: API.md §3.

Tenant identity always comes from the credential; a client-supplied tenant id
that disagrees is rejected (403) by ``assert_tenant_match``.
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import get_auth_service, get_principal, get_tenant_context, require_roles
from app.api.schemas import Page, PaginationParams, paginate
from app.core.auth import AuthService
from app.core.contracts import TenantContext
from app.core.models import Role, Tenant
from app.core.tenancy import Principal, assert_tenant_match

router = APIRouter(prefix="/tenants", tags=["tenants"])


class TenantSettingsIn(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    settings: dict[str, Any] | None = None


class InviteUserIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=72)
    display_name: str = Field(default="", max_length=255)
    roles: list[str] = Field(default_factory=lambda: ["member"])


class UpdateUserIn(BaseModel):
    roles: list[str] | None = None
    is_active: bool | None = None
    display_name: str | None = Field(default=None, max_length=255)


@router.get("/me")
async def get_tenant(tenant: TenantContext = Depends(get_tenant_context),
                     auth: AuthService = Depends(get_auth_service)) -> dict[str, Any]:
    row = (await auth.db.execute(
        select(Tenant).where(Tenant.id == tenant.tenant_id))).scalar_one()
    return {"id": row.id, "name": row.name, "slug": row.slug,
            "settings": row.settings,
            "spend_frozen": row.spend_frozen,
            "created_at": row.created_at}


@router.patch("/me", dependencies=[Depends(require_roles("owner", "admin"))])
async def update_tenant(body: TenantSettingsIn,
                        tenant: TenantContext = Depends(get_tenant_context),
                        auth: AuthService = Depends(get_auth_service)) -> dict[str, Any]:
    row = (await auth.db.execute(
        select(Tenant).where(Tenant.id == tenant.tenant_id))).scalar_one()
    if body.name is not None:
        row.name = body.name
    if body.settings is not None:
        row.settings = body.settings
    await auth.db.flush()
    return {"id": row.id, "name": row.name, "slug": row.slug,
            "settings": row.settings}


@router.get("/me/users")
async def list_users(p: Annotated[PaginationParams, Depends()],
                     tenant: TenantContext = Depends(get_tenant_context),
                     auth: AuthService = Depends(get_auth_service)) -> Page[dict[str, Any]]:
    users = await auth.list_users(tenant.tenant_id)
    return paginate(users, p.page_size, p.offset())


@router.post("/me/users", dependencies=[Depends(require_roles("owner", "admin"))])
async def invite_user(body: InviteUserIn,
                      tenant: TenantContext = Depends(get_tenant_context),
                      auth: AuthService = Depends(get_auth_service)) -> dict[str, Any]:
    return await auth.create_user(tenant.tenant_id, body.email, body.password,
                                  body.display_name, body.roles)


@router.patch("/me/users/{user_id}",
              dependencies=[Depends(require_roles("owner", "admin"))])
async def update_user(user_id: str, body: UpdateUserIn,
                      principal: Principal = Depends(get_principal),
                      tenant: TenantContext = Depends(get_tenant_context),
                      auth: AuthService = Depends(get_auth_service)) -> dict[str, Any]:
    assert_tenant_match(principal, tenant.tenant_id)
    return await auth.update_user(tenant.tenant_id, user_id, body.roles,
                                  body.is_active, body.display_name,
                                  acting_user_id=principal.user_id)


@router.get("/me/roles")
async def list_roles(tenant: TenantContext = Depends(get_tenant_context),
                     auth: AuthService = Depends(get_auth_service)) -> dict[str, Any]:
    rows = (await auth.db.execute(
        select(Role).where(Role.tenant_id == tenant.tenant_id)
        .order_by(Role.name))).scalars().all()
    return {"items": [{"id": r.id, "name": r.name, "permissions": r.permissions,
                       "is_system": r.is_system} for r in rows]}
