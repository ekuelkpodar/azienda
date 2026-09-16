"""FastAPI dependencies: settings, DB sessions, auth, tenancy, RBAC, rate limits.

COMPOSITION ROOT (contracts seam note): this module is the one place allowed
to import concrete implementations from ``core``, ``governance`` and
``billing`` and bind them to the protocols in ``core/contracts.py``. Routers
and services must depend on the protocols (via these ``get_*`` providers),
never on another package's implementation modules directly. Wiring has to live
*somewhere* — keeping it here, in one auditable module, is the documented
exception to the "contracts-only seam" rule (AGENTS.md §1.1).

No business logic here — only wiring. Routers stay thin; domain work lives in
the service implementations behind ``core.contracts`` protocols.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.service import BillingService
from app.core import errors
from app.core.auth import AuthService
from app.core.config import Settings
from app.core.config import settings as global_settings
from app.core.contracts import (
    ApprovalStore,
    AuditLedger,
    BudgetEnforcer,
    CostRecorder,
    CreditLedger,
    PolicyEngine,
    RiskScorer,
    TenantContext,
)
from app.core.db import apply_tenant_rls, get_session_factory
from app.core.idempotency import IdempotencyStore
from app.core.ratelimit import InMemoryRateLimiter, rate_limit_key
from app.core.security import has_any_role, has_permission
from app.core.tenancy import Principal, principal_to_tenant_context
from app.governance.approvals.store import ApprovalStoreImpl
from app.governance.audit.ledger import AuditLedgerImpl
from app.governance.budgets.costs import CostRecorderImpl
from app.governance.budgets.enforcer import BudgetEnforcerImpl
from app.governance.policy.engine import RulePolicyEngine
from app.governance.policy.risk import RiskScorerImpl

_bearer = HTTPBearer(auto_error=False)
_rate_limiter = InMemoryRateLimiter()


def get_settings(request: Request) -> Settings:
    return getattr(request.app.state, "settings", global_settings)


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """Request-scoped session. Commits on success, rolls back on error."""
    session = get_session_factory()()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def _resolve_principal(request: Request, db: AsyncSession,
                             settings: Settings) -> Principal | None:
    """Bearer JWT first, then X-API-Key. None if no credentials present.

    Raises UnauthenticatedError only for *invalid* credentials (wrong key,
    bad token) — absence is not an error here so public routes can rate-limit
    before auth.
    """
    auth = AuthService(db, settings)
    creds: HTTPAuthorizationCredentials | None = await _bearer(request)
    if creds is not None and creds.scheme.lower() == "bearer" and creds.credentials:
        return await auth.principal_from_access_token(creds.credentials)
    api_key = request.headers.get("X-API-Key")
    if api_key:
        principal = await auth.authenticate_api_key(api_key)
        if principal is not None:
            return principal
        raise errors.UnauthenticatedError("invalid api key")
    return None


async def get_principal(request: Request,
                        db: AsyncSession = Depends(get_db),
                        settings: Settings = Depends(get_settings)) -> Principal:
    """401 if no valid credential. Public routes must NOT depend on this."""
    principal = await _resolve_principal(request, db, settings)
    if principal is None:
        raise errors.UnauthenticatedError("missing credentials")
    return principal


async def get_tenant_context(
        principal: Principal = Depends(get_principal)) -> TenantContext:
    return principal_to_tenant_context(principal)


async def get_tenant_db(request: Request,
                        principal: Principal = Depends(get_principal),
                        db: AsyncSession = Depends(get_db)) -> AsyncIterator[AsyncSession]:
    """Tenant-scoped session: sets Postgres RLS variables, then yields."""
    await apply_tenant_rls(db, principal.tenant_id, principal.user_id)
    yield db


def require_roles(*roles: str) -> Callable[[Principal], Awaitable[Principal]]:
    async def _check(principal: Principal = Depends(get_principal)) -> Principal:
        if not has_any_role(principal.roles, *roles):
            raise errors.ForbiddenError(
                f"requires role: {'|'.join(roles)}",
                details={"roles": list(principal.roles)})
        return principal
    return _check


def require_permission(permission: str) -> Callable[[Principal], Awaitable[Principal]]:
    async def _check(principal: Principal = Depends(get_principal)) -> Principal:
        if not has_permission(principal.roles, permission):
            raise errors.ForbiddenError(
                f"missing permission: {permission}",
                details={"roles": list(principal.roles)})
        return principal
    return _check


async def rate_limit(request: Request,
                     db: AsyncSession = Depends(get_db),
                     settings: Settings = Depends(get_settings)) -> None:
    """Per-tenant (or per-IP for anonymous) sliding-window limit.

    Deliberately does NOT require authentication: public routes (register,
    login) are rate-limited by client IP, which also blunts credential
    stuffing. Authenticated callers are scoped by tenant+subject.
    """
    scope = request.url.path.rsplit("/", 1)[0] or "/"
    try:
        principal = await _resolve_principal(request, db, settings)
    except errors.UnauthenticatedError:
        principal = None  # invalid credential: still rate-limit, auth decides
    if principal is not None:
        key = rate_limit_key(principal.tenant_id,
                             principal.user_id or principal.api_key_id or "anon",
                             scope)
    else:
        ip = request.client.host if request.client else "unknown"
        key = rate_limit_key("anon", ip, scope)
    result = await _rate_limiter.check(key, settings.rate_limit_per_minute, 60)
    if not result.allowed:
        raise errors.RateLimitedError(
            "rate limit exceeded",
            details={"retry_after_seconds": round(result.retry_after_seconds, 1)})


# ------------------------------------------------------------ service factories
def get_auth_service(db: AsyncSession = Depends(get_db),
                     settings: Settings = Depends(get_settings)) -> AuthService:
    return AuthService(db, settings)


def _svc_tenant_db(tenant_db: AsyncSession = Depends(get_tenant_db),
                   settings: Settings = Depends(get_settings)
                   ) -> tuple[AsyncSession, Settings]:
    return tenant_db, settings


# Annotated alias: every tenant-scoped service factory takes the same pair.
_SvcDeps = Annotated[tuple[AsyncSession, Settings], Depends(_svc_tenant_db)]


def get_risk_scorer(deps: _SvcDeps) -> RiskScorer:
    db, settings = deps
    return RiskScorerImpl(db, settings)


def get_approval_store(deps: _SvcDeps) -> ApprovalStore:
    db, settings = deps
    return ApprovalStoreImpl(db, settings)


def get_policy_engine(deps: _SvcDeps,
                      risk_scorer: RiskScorer = Depends(get_risk_scorer),
                      approval_store: ApprovalStore = Depends(get_approval_store),
                      ) -> PolicyEngine:
    db, settings = deps
    return RulePolicyEngine(db, settings, risk_scorer, approval_store)


def get_audit_ledger(deps: _SvcDeps) -> AuditLedger:
    db, _ = deps
    return AuditLedgerImpl(db)


def get_budget_enforcer(deps: _SvcDeps) -> BudgetEnforcer:
    db, settings = deps
    return BudgetEnforcerImpl(db, settings)


def get_billing_service(deps: _SvcDeps) -> BillingService:
    db, settings = deps
    return BillingService(db, settings)


def get_credit_ledger(service: BillingService = Depends(get_billing_service)  # noqa: B008
                      ) -> CreditLedger:
    return service


def get_cost_recorder(deps: _SvcDeps,
                      credit_ledger: CreditLedger = Depends(get_credit_ledger)) -> CostRecorder:
    from app.core.events import default_bus
    db, _ = deps
    return CostRecorderImpl(db, credit_ledger, default_bus)


def get_idempotency_store(deps: _SvcDeps) -> IdempotencyStore:
    db, settings = deps
    return IdempotencyStore(db, settings)


# ------------------------------------------------------------ idempotency helper
async def idempotency_check(request: Request,
                            tenant: TenantContext = Depends(get_tenant_context),
                            store: IdempotencyStore = Depends(get_idempotency_store)
                            ) -> tuple[int, dict[str, Any]] | str | None:
    """Returns stored (status, body) on replay, the key on first use, None if
    no Idempotency-Key header was sent."""
    key = request.headers.get("Idempotency-Key")
    if not key or request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return None
    body = await request.body()
    result = await store.acquire(tenant.tenant_id, key, request.method,
                                 request.url.path, body)
    return result  # tuple on replay, str (the key) on first use


async def idempotency_store(request: Request,
                            key: str,
                            status_code: int,
                            response_body: dict[str, Any],
                            tenant: TenantContext,
                            store: IdempotencyStore) -> None:
    # Encode with the same serializer FastAPI uses for the live response, so a
    # replay is byte-identical to the original (datetimes ISO-8601, Decimal as
    # float). Storing the raw dict would re-encode via str() and diverge.
    from fastapi.encoders import jsonable_encoder
    encoded = jsonable_encoder(response_body)
    body = await request.body()
    await store.store(tenant.tenant_id, key, request.method, request.url.path,
                      body, status_code, encoded)


def replay_response(stored: tuple[int, dict[str, Any]]) -> JSONResponse:
    status_code, body = stored
    return JSONResponse(status_code=status_code, content=body)
