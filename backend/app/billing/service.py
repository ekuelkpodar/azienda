"""BillingService: plans, subscriptions, metering, overage, spend caps, invoices.

Also implements ``core.contracts.CreditLedger`` (``draw``) — the money-movement
side of cost recording. All pricing comes from ``plan_dimensions`` rows.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.models import (
    BillingInvoice,
    BillingPlan,
    CreditPool,
    CreditTransaction,
    PlanDimension,
    Subscription,
    UsageMeter,
)
from app.billing.plans import DEFAULT_PLANS
from app.core import errors
from app.core.config import Settings
from app.core.contracts import CreditLedger, TenantContext


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _billing_date() -> date:
    """Canonical period bucket for usage metering: the UTC date.

    All period-bucketed metering rows are keyed by this date, so callers
    must query with UTC dates too (never ``date.today()``, which is the
    server's local timezone and can disagree with UTC around midnight)."""
    return _utcnow().date()


def _dec(value: object) -> Decimal:
    return Decimal(str(value))


class BillingService(CreditLedger):
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    # ---------------------------------------------------------------- plans
    async def seed_plans(self) -> int:
        """Insert/update the catalogue from plans.py. Returns plans touched."""
        count = 0
        for spec in DEFAULT_PLANS:
            plan = (await self.db.execute(
                select(BillingPlan).where(BillingPlan.slug == spec["slug"])
            )).scalar_one_or_none()
            if plan is None:
                plan = BillingPlan(slug=spec["slug"], name=spec["name"],
                                   description=spec["description"], is_active=True)
                self.db.add(plan)
                await self.db.flush()
            else:
                plan.name = spec["name"]
                plan.description = spec["description"]
                plan.is_active = True
            for dim, value in spec["dimensions"].items():
                row = (await self.db.execute(
                    select(PlanDimension).where(
                        PlanDimension.plan_id == plan.id,
                        PlanDimension.dimension == dim)
                )).scalar_one_or_none()
                if row is None:
                    self.db.add(PlanDimension(plan_id=plan.id, dimension=dim,
                                              value={"value": value}))
                else:
                    row.value = {"value": value}
            count += 1
        await self.db.flush()
        return count

    async def list_plans(self) -> list[dict[str, Any]]:
        plans = (await self.db.execute(
            select(BillingPlan).where(BillingPlan.is_active.is_(True))
            .order_by(BillingPlan.created_at))).scalars().all()
        out = []
        for plan in plans:
            dims = (await self.db.execute(
                select(PlanDimension).where(PlanDimension.plan_id == plan.id)
            )).scalars().all()
            out.append({
                "slug": plan.slug, "name": plan.name,
                "description": plan.description,
                "dimensions": {d.dimension: d.value.get("value") for d in dims}})
        return out

    async def _plan_by_slug(self, slug: str) -> BillingPlan:
        plan = (await self.db.execute(
            select(BillingPlan).where(BillingPlan.slug == slug,
                                      BillingPlan.is_active.is_(True))
        )).scalar_one_or_none()
        if plan is None:
            raise errors.NotFoundError(f"plan '{slug}' not found")
        return plan

    async def _dimensions(self, plan_id: str) -> dict[str, Decimal | int]:
        rows = (await self.db.execute(
            select(PlanDimension).where(PlanDimension.plan_id == plan_id)
        )).scalars().all()
        return {r.dimension: _dec(r.value.get("value", 0)) for r in rows}

    # ---------------------------------------------------------- subscriptions
    async def get_subscription(self, tenant: TenantContext) -> dict[str, Any] | None:
        sub = await self._get_sub(tenant)
        if sub is None:
            return None
        return await self._sub_view(sub)

    async def subscribe(self, tenant: TenantContext, plan_slug: str,
                        trial_days: int = 14) -> dict[str, Any]:
        existing = await self._get_sub(tenant)
        if existing is not None:
            raise errors.ConflictError("tenant already has a subscription; "
                                        "use change_plan")
        plan = await self._plan_by_slug(plan_slug)
        now = _utcnow()
        sub = Subscription(
            tenant_id=tenant.tenant_id, plan_id=plan.id, status="trialing",
            current_period_start=now,
            current_period_end=now + timedelta(days=trial_days))
        self.db.add(sub)
        await self.db.flush()
        await self._ensure_pool(sub, await self._dimensions(plan.id))
        await self.db.flush()
        return await self._sub_view(sub)

    async def change_plan(self, tenant: TenantContext, plan_slug: str) -> dict[str, Any]:
        """Idempotent-by-intent plan change; proration is a documented future:
        the new plan takes effect at the next period boundary for platform fee,
        immediately for credit grants (grants are additive, never clawed back)."""
        sub = await self._get_sub(tenant)
        if sub is None:
            return await self.subscribe(tenant, plan_slug)
        plan = await self._plan_by_slug(plan_slug)
        sub.plan_id = plan.id
        sub.status = "active" if sub.status == "trialing" else sub.status
        await self._ensure_pool(sub, await self._dimensions(plan.id))
        await self.db.flush()
        return await self._sub_view(sub)

    async def set_spend_cap(self, tenant: TenantContext,
                            cap_usd: Decimal | None) -> dict[str, Any]:
        sub = await self._require_sub(tenant)
        if cap_usd is not None and cap_usd < 0:
            raise errors.BadRequestError("spend cap must be >= 0")
        sub.spend_cap_usd = cap_usd
        await self.db.flush()
        return await self._sub_view(sub)

    async def _get_sub(self, tenant: TenantContext) -> Subscription | None:
        return (await self.db.execute(
            select(Subscription).where(
                Subscription.tenant_id == tenant.tenant_id)
        )).scalar_one_or_none()

    async def _require_sub(self, tenant: TenantContext) -> Subscription:
        sub = await self._get_sub(tenant)
        if sub is None:
            raise errors.NotFoundError("no subscription for tenant")
        return sub

    async def _ensure_subscription(self, tenant: TenantContext) -> Subscription:
        """Lazy default-plan subscription so cost recording never dead-ends."""
        sub = await self._get_sub(tenant)
        if sub is None:
            await self.subscribe(tenant, self.settings.billing_default_plan)
            sub = await self._require_sub(tenant)
            assert sub is not None
            return sub
        return sub

    async def _sub_view(self, sub: Subscription) -> dict[str, Any]:
        plan = await self.db.get(BillingPlan, sub.plan_id)
        dims = await self._dimensions(sub.plan_id)
        pool = await self._ensure_pool(sub, dims)
        return {
            "id": sub.id,
            "plan_slug": plan.slug if plan else "unknown",
            "status": sub.status,
            "current_period_start": sub.current_period_start,
            "current_period_end": sub.current_period_end,
            "spend_cap_usd": sub.spend_cap_usd,
            "credit_pool": {"granted": pool.granted, "drawn": pool.drawn,
                            "remaining": max(Decimal("0"), pool.granted - pool.drawn)},
            "dimensions": {k: str(v) for k, v in dims.items()},
        }

    async def _ensure_pool(self, sub: Subscription,
                           dims: dict[str, Decimal | int]) -> CreditPool:
        pool = (await self.db.execute(
            select(CreditPool).where(
                CreditPool.subscription_id == sub.id,
                CreditPool.period_start == sub.current_period_start)
        )).scalar_one_or_none()
        if pool is None:
            pool = CreditPool(
                tenant_id=sub.tenant_id,
                subscription_id=sub.id,
                period_start=sub.current_period_start,
                period_end=sub.current_period_end,
                granted=_dec(dims.get("credits_included", 0)),
                drawn=Decimal("0"))
            self.db.add(pool)
            await self.db.flush()
        return pool

    # ------------------------------------------------------- CreditLedger
    async def draw(self, tenant: TenantContext, credits: Decimal, reason: str,
                   task_id: str | None = None) -> None:
        """Draw credits from the current pool. Enforces the spend cap.

        Pool exhaustion does NOT block here — overage is billed at
        ``credit_overage_rate_usd``. The spend CAP is the hard stop (customer
        opted in explicitly, API.md §13).
        """
        if credits <= 0:
            return
        sub = await self._ensure_subscription(tenant)
        dims = await self._dimensions(sub.plan_id)
        pool = await self._ensure_pool(sub, dims)
        pool.drawn = pool.drawn + credits
        self.db.add(CreditTransaction(tenant_id=sub.tenant_id, pool_id=pool.id,
                                      task_id=task_id, delta=-credits,
                                      reason=reason[:512]))
        await self.record_usage(tenant, "credits", credits,
                                cost_usd=Decimal("0"))
        await self.db.flush()
        # Spend-cap enforcement on the projected billable total.
        if sub.spend_cap_usd is not None:
            billable = await self._projected_billable_usd(sub, dims, pool)
            if billable > sub.spend_cap_usd:
                raise errors.SpendCapHitError(
                    f"spend cap ${sub.spend_cap_usd} would be exceeded "
                    f"(projected ${billable})",
                    details={"spend_cap_usd": str(sub.spend_cap_usd),
                             "projected_usd": str(billable)})

    async def _projected_billable_usd(self, sub: Subscription,
                                      dims: dict[str, Any], pool: CreditPool) -> Decimal:
        overage = await self.compute_overage(sub.tenant_id, dims, pool)
        return (_dec(dims.get("platform_fee_usd", 0))
                + _dec(overage["credits_overage_usd"]))

    # ---------------------------------------------------------------- metering
    async def record_usage(self, tenant: TenantContext, dimension: str,
                           quantity: Decimal,
                           cost_usd: Decimal = Decimal("0"),
                           period: date | None = None) -> None:
        period = period or _billing_date()
        row = (await self.db.execute(
            select(UsageMeter).where(
                UsageMeter.tenant_id == tenant.tenant_id,
                UsageMeter.period == period,
                UsageMeter.dimension == dimension)
        )).scalar_one_or_none()
        if row is None:
            row = UsageMeter(tenant_id=tenant.tenant_id, period=period,
                             dimension=dimension, quantity=Decimal("0"),
                             cost_usd=Decimal("0"))
            self.db.add(row)
        row.quantity = row.quantity + quantity
        row.cost_usd = row.cost_usd + cost_usd
        await self.db.flush()

    async def get_usage(self, tenant: TenantContext, start: date,
                        end: date) -> list[dict[str, Any]]:
        rows = (await self.db.execute(
            select(UsageMeter).where(
                UsageMeter.tenant_id == tenant.tenant_id,
                UsageMeter.period >= start, UsageMeter.period <= end)
            .order_by(UsageMeter.period, UsageMeter.dimension)
        )).scalars().all()
        return [{"period": r.period.isoformat(), "dimension": r.dimension,
                 "quantity": str(r.quantity), "cost_usd": str(r.cost_usd)}
                for r in rows]

    async def compute_overage(self, tenant_id: str, dims: dict[str, Any] | None = None,
                              pool: CreditPool | None = None) -> dict[str, Any]:
        """Overage for the current subscription period, from plan data."""
        sub = (await self.db.execute(
            select(Subscription).where(Subscription.tenant_id == tenant_id)
        )).scalar_one_or_none()
        if sub is None:
            return {"credits_used": "0", "credits_included": "0",
                    "credits_overage": "0", "credits_overage_usd": "0"}
        dims = dims or await self._dimensions(sub.plan_id)
        pool = pool or await self._ensure_pool(sub, dims)
        included = _dec(dims.get("credits_included", 0))
        rate = _dec(dims.get("credit_overage_rate_usd", 0))
        over = max(Decimal("0"), pool.drawn - included)
        return {"credits_used": str(pool.drawn),
                "credits_included": str(included),
                "credits_overage": str(over),
                "credits_overage_usd": str((over * rate).quantize(Decimal("0.0001")))}

    # ---------------------------------------------------------------- invoices
    async def create_invoice(self, tenant: TenantContext) -> dict[str, Any]:
        """Draft invoice for the current period from metered usage + overage."""
        sub = await self._require_sub(tenant)
        dims = await self._dimensions(sub.plan_id)
        pool = await self._ensure_pool(sub, dims)
        overage = await self.compute_overage(tenant.tenant_id, dims, pool)
        start = sub.current_period_start.date()
        end = sub.current_period_end.date()
        usage = await self.get_usage(tenant, start, end)
        lines = [
            {"description": f"Platform fee ({sub.current_period_start.date()}–{end})",
             "amount_usd": str(_dec(dims.get("platform_fee_usd", 0)))},
            {"description": (f"Credit overage: {overage['credits_overage']} credits "
                              f"@ ${dims.get('credit_overage_rate_usd', 0)}/credit"),
             "amount_usd": overage["credits_overage_usd"]},
        ]
        for u in usage:
            if u["dimension"] != "credits" and Decimal(u["cost_usd"]) > 0:
                lines.append({"description": f"Usage: {u['dimension']} "
                                             f"({u['period']}) x{u['quantity']}",
                              "amount_usd": u["cost_usd"]})
        total = sum((_dec(line["amount_usd"]) for line in lines), Decimal("0"))
        invoice = BillingInvoice(
            tenant_id=tenant.tenant_id, subscription_id=sub.id,
            period_start=sub.current_period_start,
            period_end=sub.current_period_end,
            lines=lines, total_usd=total, status="draft")
        self.db.add(invoice)
        await self.db.flush()
        return {"id": invoice.id, "lines": lines, "total_usd": str(total),
                "status": invoice.status,
                "period_start": invoice.period_start,
                "period_end": invoice.period_end}

    async def list_invoices(self, tenant: TenantContext,
                            limit: int = 20) -> list[dict[str, Any]]:
        rows = (await self.db.execute(
            select(BillingInvoice).where(
                BillingInvoice.tenant_id == tenant.tenant_id)
            .order_by(BillingInvoice.created_at.desc()).limit(limit)
        )).scalars().all()
        return [{"id": r.id, "lines": r.lines, "total_usd": str(r.total_usd),
                 "status": r.status,
                 "period_start": r.period_start, "period_end": r.period_end}
                for r in rows]
