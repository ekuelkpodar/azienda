"""Identity service: tenants, users, roles, sessions, API keys.

Lives in ``core/`` because identity/tenancy is the platform substrate every
package builds on. All methods are tenant-scoped except the bootstrap paths
(register/login), which establish the tenant from credentials.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import errors, security
from app.core.config import Settings
from app.core.models import ApiKey, RefreshToken, Role, Tenant, User, UserRole
from app.core.tenancy import Principal
from app.core.time import as_utc

_SLUG_RE = re.compile(r"^[a-z0-9-]{2,64}$")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return (slug or "tenant")[:64]


@dataclass
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in_seconds: int


class AuthService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    # ------------------------------------------------------------ bootstrap
    async def register_tenant(self, tenant_name: str, email: str, password: str,
                              display_name: str = "") -> tuple[Tenant, User, TokenPair]:
        """Self-serve signup: creates tenant + owner user + session. Public."""
        email = email.strip().lower()
        if not email or "@" not in email:
            raise errors.BadRequestError("valid email is required")
        base_slug = slugify(tenant_name)
        slug = base_slug
        i = 1
        while (await self.db.execute(
                select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none():
            i += 1
            slug = f"{base_slug}-{i}"
        tenant = Tenant(name=tenant_name.strip(), slug=slug, settings={})
        self.db.add(tenant)
        await self.db.flush()
        await self.ensure_system_roles(tenant.id)
        user = User(tenant_id=tenant.id, email=email,
                    password_hash=security.hash_password(password),
                    display_name=display_name or email.split("@")[0])
        self.db.add(user)
        await self.db.flush()
        await self._grant_role(user.id, tenant.id, "owner")
        pair = await self._new_session(user, tenant.id, ("owner",))
        await self.db.flush()
        return tenant, user, pair

    async def login(self, email: str, password: str,
                    tenant_slug: str | None = None) -> tuple[User, Tenant, TokenPair]:
        email = email.strip().lower()
        q = (select(User, Tenant).join(Tenant, User.tenant_id == Tenant.id)
             .where(User.email == email, User.is_active.is_(True)))
        if tenant_slug:
            q = q.where(Tenant.slug == tenant_slug)
        rows = (await self.db.execute(q)).all()
        if not rows:
            # Same error as wrong password: no user enumeration.
            raise errors.UnauthenticatedError("invalid credentials")
        if len(rows) > 1 and not tenant_slug:
            raise errors.BadRequestError(
                "email belongs to multiple tenants; specify tenant_slug",
                details={"code": "tenant_ambiguous"})
        user, tenant = rows[0]
        if not user.password_hash or not security.verify_password(
                password, user.password_hash):
            raise errors.UnauthenticatedError("invalid credentials")
        roles = await self._user_roles(user.id)
        pair = await self._new_session(user, tenant.id, roles)
        await self.db.flush()
        return user, tenant, pair

    # -------------------------------------------------------------- sessions
    async def _new_session(self, user: User, tenant_id: str,
                           roles: tuple[str, ...]) -> TokenPair:
        access = security.issue_access_token(self.settings, user.id, tenant_id,
                                             roles)
        raw_refresh = security.new_refresh_token()
        self.db.add(RefreshToken(
            tenant_id=tenant_id, user_id=user.id,
            token_hash=security.hash_token(raw_refresh),
            expires_at=_utcnow() + timedelta(days=self.settings.jwt_refresh_ttl_days)))
        return TokenPair(access_token=access, refresh_token=raw_refresh,
                         expires_in_seconds=self.settings.jwt_access_ttl_minutes * 60)

    async def refresh(self, raw_token: str) -> TokenPair:
        row = (await self.db.execute(
            select(RefreshToken).where(
                RefreshToken.token_hash == security.hash_token(raw_token))
        )).scalar_one_or_none()
        if row is None:
            raise errors.UnauthenticatedError("invalid refresh token")
        if row.revoked_at is not None:
            if row.replaced_by is not None:
                # Reuse of a rotated token: possible theft -> revoke the chain.
                await self._revoke_user_tokens(row.user_id)
                await self.db.flush()
                raise errors.UnauthenticatedError(
                    "refresh token reuse detected; all sessions revoked")
            raise errors.UnauthenticatedError("refresh token revoked")
        if as_utc(row.expires_at) <= _utcnow():
            raise errors.UnauthenticatedError("refresh token expired")
        user = await self.db.get(User, row.user_id)
        if user is None or not user.is_active:
            raise errors.UnauthenticatedError("user inactive")
        roles = await self._user_roles(user.id)
        # rotate
        new_raw = security.new_refresh_token()
        new_row = RefreshToken(
            tenant_id=row.tenant_id, user_id=user.id,
            token_hash=security.hash_token(new_raw),
            expires_at=_utcnow() + timedelta(days=self.settings.jwt_refresh_ttl_days))
        self.db.add(new_row)
        await self.db.flush()
        row.revoked_at = _utcnow()
        row.replaced_by = new_row.id
        access = security.issue_access_token(self.settings, user.id, row.tenant_id,
                                             roles)
        await self.db.flush()
        return TokenPair(access_token=access, refresh_token=new_raw,
                         expires_in_seconds=self.settings.jwt_access_ttl_minutes * 60)

    async def logout(self, raw_token: str) -> None:
        row = (await self.db.execute(
            select(RefreshToken).where(
                RefreshToken.token_hash == security.hash_token(raw_token))
        )).scalar_one_or_none()
        if row is not None and row.revoked_at is None:
            row.revoked_at = _utcnow()
            await self.db.flush()

    async def _revoke_user_tokens(self, user_id: str) -> None:
        rows = (await self.db.execute(
            select(RefreshToken).where(RefreshToken.user_id == user_id,
                                       RefreshToken.revoked_at.is_(None))
        )).scalars().all()
        for row in rows:
            row.revoked_at = _utcnow()

    # ---------------------------------------------------------------- roles
    async def ensure_system_roles(self, tenant_id: str) -> None:
        for name in ("owner", "admin", "member", "agent"):
            existing = (await self.db.execute(
                select(Role).where(Role.tenant_id == tenant_id, Role.name == name)
            )).scalar_one_or_none()
            if existing is None:
                self.db.add(Role(
                    tenant_id=tenant_id, name=name,
                    permissions=sorted(security.ROLE_PERMISSIONS[name]),
                    is_system=True))
        await self.db.flush()

    async def _user_roles(self, user_id: str) -> tuple[str, ...]:
        rows = (await self.db.execute(
            select(Role.name).join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user_id))).all()
        return tuple(r[0] for r in rows)

    async def _grant_role(self, user_id: str, tenant_id: str, role_name: str) -> None:
        role = (await self.db.execute(
            select(Role).where(Role.tenant_id == tenant_id, Role.name == role_name)
        )).scalar_one_or_none()
        if role is None:
            raise errors.NotFoundError(f"role '{role_name}' not found")
        existing = (await self.db.execute(
            select(UserRole).where(UserRole.user_id == user_id,
                                   UserRole.role_id == role.id))).scalar_one_or_none()
        if existing is None:
            self.db.add(UserRole(user_id=user_id, role_id=role.id))

    # ----------------------------------------------------------------- users
    async def list_users(self, tenant_id: str) -> list[dict[str, Any]]:
        users = (await self.db.execute(
            select(User).where(User.tenant_id == tenant_id)
            .order_by(User.created_at))).scalars().all()
        out = []
        for u in users:
            out.append({"id": u.id, "email": u.email,
                        "display_name": u.display_name,
                        "is_active": u.is_active,
                        "roles": list(await self._user_roles(u.id)),
                        "created_at": u.created_at})
        return out

    async def create_user(self, tenant_id: str, email: str, password: str,
                          display_name: str, roles: list[str]) -> dict[str, Any]:
        email = email.strip().lower()
        existing = (await self.db.execute(
            select(User).where(User.tenant_id == tenant_id, User.email == email)
        )).scalar_one_or_none()
        if existing is not None:
            raise errors.ConflictError("user with this email already exists")
        user = User(tenant_id=tenant_id, email=email,
                    password_hash=security.hash_password(password),
                    display_name=display_name)
        self.db.add(user)
        await self.db.flush()
        for role_name in roles:
            await self._grant_role(user.id, tenant_id, role_name)
        await self.db.flush()
        return {"id": user.id, "email": user.email,
                "display_name": user.display_name, "is_active": user.is_active,
                "roles": list(await self._user_roles(user.id))}

    async def update_user(self, tenant_id: str, user_id: str,
                          roles: list[str] | None = None,
                          is_active: bool | None = None,
                          display_name: str | None = None,
                          acting_user_id: str | None = None) -> dict[str, Any]:
        user = await self._tenant_user(tenant_id, user_id)
        if roles is not None:
            if acting_user_id == user_id and "owner" not in roles:
                raise errors.BadRequestError(
                    "cannot remove your own owner role")
            # replace roles
            current = (await self.db.execute(
                select(UserRole).where(UserRole.user_id == user_id))).scalars().all()
            # never leave a tenant without an owner
            if "owner" not in roles:
                owners = await self._count_role(tenant_id, "owner", exclude=user_id)
                if owners == 0:
                    raise errors.BadRequestError(
                        "tenant must retain at least one owner")
            for ur in current:
                await self.db.delete(ur)
            for role_name in roles:
                await self._grant_role(user_id, tenant_id, role_name)
        if is_active is not None:
            if acting_user_id == user_id and not is_active:
                raise errors.BadRequestError("cannot deactivate yourself")
            user.is_active = is_active
        if display_name is not None:
            user.display_name = display_name
        await self.db.flush()
        return {"id": user.id, "email": user.email,
                "display_name": user.display_name, "is_active": user.is_active,
                "roles": list(await self._user_roles(user.id))}

    async def _count_role(self, tenant_id: str, role_name: str,
                          exclude: str | None = None) -> int:
        q = (select(func.count()).select_from(User)
             .join(UserRole, UserRole.user_id == User.id)
             .join(Role, Role.id == UserRole.role_id)
             .where(User.tenant_id == tenant_id, Role.name == role_name,
                    User.is_active.is_(True)))
        if exclude:
            q = q.where(User.id != exclude)
        return (await self.db.execute(q)).scalar_one()

    async def _tenant_user(self, tenant_id: str, user_id: str) -> User:
        user = (await self.db.execute(
            select(User).where(User.id == user_id, User.tenant_id == tenant_id)
        )).scalar_one_or_none()
        if user is None:
            raise errors.NotFoundError("user not found")
        return user

    # --------------------------------------------------------------- api keys
    async def create_api_key(self, tenant_id: str, created_by: str | None,
                             name: str, scopes: list[str],
                             expires_days: int | None = None) -> tuple[dict[str, Any], str]:
        presented, key_hash = security.new_api_key(self.settings.api_key_prefix)
        record = ApiKey(tenant_id=tenant_id, name=name, key_hash=key_hash,
                        key_prefix=presented[:12], scopes=scopes,
                        expires_at=(_utcnow() + timedelta(days=expires_days)
                                    if expires_days else None),
                        created_by=created_by)
        self.db.add(record)
        await self.db.flush()
        view = {"id": record.id, "name": name, "key_prefix": record.key_prefix,
                "scopes": scopes, "expires_at": record.expires_at,
                "revoked_at": None, "created_at": record.created_at}
        return view, presented

    async def list_api_keys(self, tenant_id: str) -> list[dict[str, Any]]:
        rows = (await self.db.execute(
            select(ApiKey).where(ApiKey.tenant_id == tenant_id)
            .order_by(ApiKey.created_at.desc()))).scalars().all()
        return [{"id": r.id, "name": r.name, "key_prefix": r.key_prefix,
                 "scopes": r.scopes, "expires_at": r.expires_at,
                 "revoked_at": r.revoked_at, "created_at": r.created_at}
                for r in rows]

    async def revoke_api_key(self, tenant_id: str, key_id: str) -> None:
        row = (await self.db.execute(
            select(ApiKey).where(ApiKey.id == key_id,
                                 ApiKey.tenant_id == tenant_id)
        )).scalar_one_or_none()
        if row is None:
            raise errors.NotFoundError("api key not found")
        row.revoked_at = _utcnow()
        await self.db.flush()

    async def authenticate_api_key(self, presented: str) -> Principal | None:
        row = (await self.db.execute(
            select(ApiKey).where(
                ApiKey.key_hash == security.hash_token(presented))
        )).scalar_one_or_none()
        if row is None or row.revoked_at is not None:
            return None
        if row.expires_at is not None and as_utc(row.expires_at) <= _utcnow():
            return None
        return Principal(kind="api_key", user_id=row.created_by,
                         tenant_id=row.tenant_id, roles=("agent",),
                         scopes=tuple(row.scopes or []),
                         api_key_id=row.id)

    async def principal_from_access_token(self, token: str) -> Principal:
        try:
            claims = security.decode_access_token(self.settings, token)
        except security.TokenExpiredError as e:
            raise errors.UnauthenticatedError("access token expired",
                                              details={"code": "token_expired"}) from e
        except security.TokenError as e:
            raise errors.UnauthenticatedError(str(e)) from e
        user = (await self.db.execute(
            select(User).where(User.id == claims.user_id,
                               User.tenant_id == claims.tenant_id)
        )).scalar_one_or_none()
        if user is None or not user.is_active:
            raise errors.UnauthenticatedError("user inactive or deleted")
        return Principal(kind="user", user_id=user.id,
                         tenant_id=claims.tenant_id, roles=claims.roles)
