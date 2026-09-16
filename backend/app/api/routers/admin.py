"""Router: admin console backend. Covers the governance + identity surfaces of
the admin console: overview aggregates, policy CRUD, policy dry-run evaluation,
and governance configuration.

Note: "teams" appear in the mandate but DATABASE.md defines no teams table;
team management is a documented future (see governance/README.md). This router
ships no fake team endpoints.
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import (
    get_audit_ledger,
    get_auth_service,
    get_billing_service,
    get_budget_enforcer,
    get_policy_engine,
    get_settings,
    get_tenant_context,
    require_roles,
)
from app.api.schemas import Page, PaginationParams, paginate
from app.billing.service import BillingService
from app.core import errors
from app.core.auth import AuthService
from app.core.config import Settings
from app.core.contracts import ActionRequest, AuditLedger, BudgetEnforcer, TenantContext
from app.governance.models import ApprovalRecord, Policy, PolicyVersion
from app.governance.models import AuditEntry as AuditRow
from app.governance.policy.engine import RulePolicyEngine, compile_rule

router = APIRouter(prefix="/admin", tags=["admin"],
                   dependencies=[Depends(require_roles("owner", "admin"))])


# ------------------------------------------------------------- overview
@router.get("/overview")
async def overview(tenant: TenantContext = Depends(get_tenant_context),
                   auth: AuthService = Depends(get_auth_service),
                   enforcer: BudgetEnforcer = Depends(get_budget_enforcer),
                   billing: BillingService = Depends(get_billing_service),
                   audit: AuditLedger = Depends(get_audit_ledger)) -> dict[str, Any]:
    db = auth.db
    users = len(await auth.list_users(tenant.tenant_id))
    pending = (await db.execute(
        select(func.count()).select_from(ApprovalRecord).where(
            ApprovalRecord.tenant_id == tenant.tenant_id,
            ApprovalRecord.status == "pending"))).scalar_one()
    audit_total = (await db.execute(
        select(func.count()).select_from(AuditRow).where(
            AuditRow.tenant_id == tenant.tenant_id))).scalar_one()
    budgets = await enforcer.list_budgets(tenant)
    sub = await billing.get_subscription(tenant)
    policies = (await db.execute(
        select(func.count()).select_from(Policy).where(
            Policy.tenant_id == tenant.tenant_id,
            Policy.is_active.is_(True)))).scalar_one()
    return {
        "tenant_id": tenant.tenant_id,
        "users": users,
        "pending_approvals": pending,
        "audit_entries": audit_total,
        "active_policies": policies,
        "spend_frozen": await enforcer.is_frozen(tenant),
        "budgets": budgets,
        "subscription": sub,
    }


# ------------------------------------------------------------- policies
class PolicyIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=2000)
    rules: list[dict[str, Any]] = Field(default_factory=list)
    rego_source: str | None = None
    priority: int = Field(default=100, ge=0, le=10000)
    is_active: bool = True


class PolicyPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    rules: list[dict[str, Any]] | None = None
    is_active: bool | None = None
    priority: int | None = Field(default=None, ge=0, le=10000)


class EvaluateIn(BaseModel):
    action: str = Field(min_length=1, max_length=512)
    resource: str | None = Field(default=None, max_length=1024)
    args: dict[str, Any] = Field(default_factory=dict)
    risk_context: dict[str, Any] = Field(default_factory=dict)


def _policy_view(p: Policy) -> dict[str, Any]:
    return {"id": p.id, "name": p.name, "description": p.description,
            "rules": p.rules, "rego_source": p.rego_source,
            "is_active": p.is_active, "priority": p.priority,
            "version": p.version, "created_at": p.created_at}


@router.get("/policies")
async def list_policies(p: Annotated[PaginationParams, Depends()],
                        tenant: TenantContext = Depends(get_tenant_context),
                        auth: AuthService = Depends(get_auth_service)) -> Page[dict[str, Any]]:
    rows = (await auth.db.execute(
        select(Policy).where(Policy.tenant_id == tenant.tenant_id)
        .order_by(Policy.priority.desc()))).scalars().all()
    return paginate([_policy_view(r) for r in rows], p.page_size, p.offset())


@router.post("/policies")
async def create_policy(body: PolicyIn,
                        tenant: TenantContext = Depends(get_tenant_context),
                        auth: AuthService = Depends(get_auth_service),
                        audit: AuditLedger = Depends(get_audit_ledger)) -> dict[str, Any]:
    # Validate every rule compiles before persisting (fail fast, no half-rules).
    for raw in body.rules:
        try:
            compile_rule(raw)
        except (ValueError, TypeError, AttributeError) as e:
            raise errors.BadRequestError(f"invalid rule: {e}") from e
    policy = Policy(tenant_id=tenant.tenant_id, name=body.name,
                    description=body.description, rules=body.rules,
                    rego_source=body.rego_source, priority=body.priority,
                    is_active=body.is_active, version=1,
                    updated_by=tenant.user_id)
    auth.db.add(policy)
    await auth.db.flush()
    auth.db.add(PolicyVersion(policy_id=policy.id, tenant_id=tenant.tenant_id,
                              version=1, rules=body.rules))
    await auth.db.flush()
    await audit.append(tenant, tenant.user_id or "system", "policy.created",
                       {"policy_id": policy.id, "name": body.name})
    return _policy_view(policy)


@router.get("/policies/{policy_id}")
async def get_policy(policy_id: str,
                     tenant: TenantContext = Depends(get_tenant_context),
                     auth: AuthService = Depends(get_auth_service)) -> dict[str, Any]:
    policy = await _get_policy(auth, tenant, policy_id)
    return _policy_view(policy)


@router.patch("/policies/{policy_id}")
async def update_policy(policy_id: str, body: PolicyPatch,
                        tenant: TenantContext = Depends(get_tenant_context),
                        auth: AuthService = Depends(get_auth_service),
                        audit: AuditLedger = Depends(get_audit_ledger)) -> dict[str, Any]:
    policy = await _get_policy(auth, tenant, policy_id)
    if body.name is not None:
        policy.name = body.name
    if body.description is not None:
        policy.description = body.description
    if body.priority is not None:
        policy.priority = body.priority
    if body.is_active is not None:
        policy.is_active = body.is_active
    if body.rules is not None:
        for raw in body.rules:
            try:
                compile_rule(raw)
            except (ValueError, TypeError, AttributeError) as e:
                raise errors.BadRequestError(f"invalid rule: {e}") from e
        policy.rules = body.rules
        policy.version += 1
        auth.db.add(PolicyVersion(policy_id=policy.id,
                                  tenant_id=tenant.tenant_id,
                                  version=policy.version, rules=body.rules))
    policy.updated_by = tenant.user_id
    await auth.db.flush()
    await audit.append(tenant, tenant.user_id or "system", "policy.updated",
                       {"policy_id": policy.id, "version": policy.version})
    return _policy_view(policy)


@router.post("/policies/evaluate")
async def evaluate_policy(body: EvaluateIn,
                          tenant: TenantContext = Depends(get_tenant_context),
                          engine: RulePolicyEngine = Depends(get_policy_engine)) -> dict[str, Any]:
    """Dry-run: evaluate an action against policy WITHOUT creating approvals."""
    request = ActionRequest(tenant=tenant, action=body.action,
                            resource=body.resource, args=body.args,
                            risk_context=body.risk_context)
    decision = await engine.evaluate_dry_run(request)
    return {"effect": decision.effect.value, "policy_id": decision.policy_id,
            "reasons": list(decision.reasons),
            "obligations": decision.obligations,
            "note": "dry-run: no approval was created"}


async def _get_policy(auth: AuthService, tenant: TenantContext,
                      policy_id: str) -> Policy:
    policy = (await auth.db.execute(
        select(Policy).where(Policy.id == policy_id,
                             Policy.tenant_id == tenant.tenant_id)
    )).scalar_one_or_none()
    if policy is None:
        raise errors.NotFoundError("policy not found")
    return policy


# ------------------------------------------------------------- governance config
@router.get("/governance-config")
async def governance_config(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """Read-only view of the governance knobs (all settings-driven)."""
    return {
        "risk": {"low_max": settings.risk_low_max,
                 "high_min": settings.risk_high_min},
        "approvals": {"ttl_seconds": settings.approval_ttl_seconds,
                      "medium_requires_approval": settings.approval_medium_require,
                      "timeout_behavior": "DENY (fail closed)"},
        "cost": {"approval_threshold_usd": settings.cost_approval_threshold_usd},
        "rate_limit": {"per_minute": settings.rate_limit_per_minute,
                       "burst": settings.rate_limit_burst,
                       "backend": "in-memory (Redis documented step-up)"},
        "dlp": {"enabled": settings.dlp_enabled},
        "budgets": {"default_monthly_credits": settings.budget_default_credits_monthly,
                    "alert_thresholds": settings.budget_alert_threshold_list,
                    "exhaustion_behavior": "freeze new work; kill switch available"},
    }
