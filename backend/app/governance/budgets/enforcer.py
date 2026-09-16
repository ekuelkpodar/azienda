"""Budget enforcement: reserve-before-spend with per-tenant kill switch.

P0 business risk: unbounded agent cost. Every cost-bearing action must
``reserve()`` first; ``settle()`` reconciles the actual. Exhaustion freezes NEW
work — in-flight work settles normally. The kill switch (``spend_frozen`` on the
tenant) blocks all new reservations immediately and is audited.

Periods: daily | weekly | monthly, computed in UTC. Reservations expire after
24h (a crashed worker cannot hold budget forever); ``sweep_expired_reservations``
releases them (run from the ARQ worker / CLI).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.contracts import (
    BudgetDecision,
    BudgetEnforcer,
    BudgetView,
    CostLedgerView,
    SpendAlertView,
    TenantContext,
)
from app.core.models import Tenant
from app.governance.models import Budget, BudgetPeriod, BudgetReservation, SpendAlert

_RESERVATION_TTL = timedelta(hours=24)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def period_bounds(period: str, now: datetime) -> tuple[datetime, datetime]:
    if period == "daily":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, start + timedelta(days=1)
    if period == "weekly":
        start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0)
        return start, start + timedelta(weeks=1)
    # monthly (default)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


class BudgetEnforcerImpl(BudgetEnforcer):
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    # -- protocol ------------------------------------------------------
    async def reserve(self, tenant: TenantContext, estimated_credits: Decimal,
                      purpose: str) -> BudgetDecision:
        if estimated_credits < 0:
            return BudgetDecision(False, "estimated_credits must be >= 0")
        tenant_row = await self.db.get(Tenant, tenant.tenant_id)
        if tenant_row is None:
            return BudgetDecision(False, "unknown tenant")
        if tenant_row.spend_frozen:
            return BudgetDecision(False, "spend frozen by kill switch",
                                  remaining_credits=Decimal("0"))

        budgets = (await self.db.execute(
            select(Budget).where(Budget.tenant_id == tenant.tenant_id,
                                 Budget.is_active.is_(True),
                                 Budget.scope == "tenant")
        )).scalars().all()
        if not budgets:
            # No budget configured: allow but report zero remaining so callers
            # can distinguish "unlimited" from "tracked". Documented behavior.
            return BudgetDecision(True, "no active budget: unmetered",
                                  remaining_credits=Decimal("0"))

        now = _utcnow()
        worst_remaining: Decimal | None = None
        chosen: tuple[Budget, BudgetPeriod] | None = None
        for budget in budgets:
            period = await self._current_period(budget, now)
            held = await self._held_credits(budget.id, period.id)
            used = period.credits_used + held
            remaining = budget.credit_limit - used
            if worst_remaining is None or remaining < worst_remaining:
                worst_remaining = remaining
            if used + estimated_credits > budget.credit_limit:
                await self._maybe_fire_alert(budget, used, now)
                return BudgetDecision(
                    False,
                    f"budget '{budget.name}' exhausted: "
                    f"{used} + {estimated_credits} > {budget.credit_limit} credits",
                    remaining_credits=max(Decimal("0"), remaining))
            if chosen is None:
                chosen = (budget, period)

        assert chosen is not None
        budget, period = chosen
        reservation = BudgetReservation(
            tenant_id=tenant.tenant_id, budget_id=budget.id, period_id=period.id,
            credits_reserved=estimated_credits, purpose=purpose[:512],
            status="held", expires_at=now + _RESERVATION_TTL)
        self.db.add(reservation)
        await self.db.flush()
        await self._maybe_fire_alert(budget, period.credits_used, now)
        # Remaining AFTER this reservation: callers decide whether to proceed
        # with follow-on work based on what is actually left.
        return BudgetDecision(True, "reserved",
                              remaining_credits=max(
                                  Decimal("0"),
                                  (worst_remaining or Decimal("0"))
                                  - estimated_credits),
                              reservation_id=reservation.id)

    async def settle(self, tenant: TenantContext, reservation_id: str,
                     actual_credits: Decimal) -> None:
        reservation = (await self.db.execute(
            select(BudgetReservation).where(
                BudgetReservation.id == reservation_id,
                BudgetReservation.tenant_id == tenant.tenant_id)
        )).scalar_one_or_none()
        if reservation is None or reservation.status != "held":
            return  # idempotent: unknown or already settled -> no-op
        period = await self.db.get(BudgetPeriod, reservation.period_id)
        if period is not None:
            period.credits_used = period.credits_used + max(Decimal("0"), actual_credits)
        reservation.status = "settled"
        await self.db.flush()

    async def kill_switch(self, tenant: TenantContext, reason: str) -> None:
        tenant_row = await self.db.get(Tenant, tenant.tenant_id)
        if tenant_row is None:
            return
        tenant_row.spend_frozen = True
        tenant_row.spend_frozen_at = _utcnow()
        tenant_row.spend_frozen_reason = reason[:1024]
        await self.db.flush()

    # -- management ----------------------------------------------------
    async def release_kill_switch(self, tenant: TenantContext) -> None:
        tenant_row = await self.db.get(Tenant, tenant.tenant_id)
        if tenant_row is None:
            return
        tenant_row.spend_frozen = False
        tenant_row.spend_frozen_at = None
        tenant_row.spend_frozen_reason = None
        await self.db.flush()

    async def is_frozen(self, tenant: TenantContext) -> bool:
        tenant_row = await self.db.get(Tenant, tenant.tenant_id)
        return bool(tenant_row and tenant_row.spend_frozen)

    async def create_budget(self, tenant: TenantContext, name: str,
                            credit_limit: Decimal, period: str = "monthly",
                            scope: str = "tenant",
                            scope_ref: str | None = None) -> BudgetView:
        if period not in ("daily", "weekly", "monthly"):
            raise ValueError(f"unknown period {period!r}")
        budget = Budget(tenant_id=tenant.tenant_id, name=name, scope=scope,
                        scope_ref=scope_ref, credit_limit=credit_limit,
                        period=period,
                        alert_thresholds=self.settings.budget_alert_threshold_list,
                        is_active=True)
        self.db.add(budget)
        await self.db.flush()
        return BudgetView(id=budget.id, name=budget.name, scope=budget.scope,
                          scope_ref=budget.scope_ref,
                          credit_limit=budget.credit_limit, period=budget.period,
                          is_active=budget.is_active)

    async def list_budgets(self, tenant: TenantContext) -> list[dict[str, Any]]:
        budgets = (await self.db.execute(
            select(Budget).where(Budget.tenant_id == tenant.tenant_id)
            .order_by(Budget.created_at)
        )).scalars().all()
        now = _utcnow()
        out = []
        for budget in budgets:
            period = await self._current_period(budget, now) if budget.is_active else None
            held = await self._held_credits(budget.id, period.id) if period else Decimal("0")
            used = (period.credits_used + held) if period else Decimal("0")
            out.append({
                "id": budget.id, "name": budget.name, "scope": budget.scope,
                "scope_ref": budget.scope_ref, "credit_limit": budget.credit_limit,
                "period": budget.period, "is_active": budget.is_active,
                "credits_used": used,
                "remaining": max(Decimal("0"), budget.credit_limit - used),
                "period_starts_at": period.starts_at if period else None,
                "period_ends_at": period.ends_at if period else None,
            })
        return out

    async def sweep_expired_reservations(self) -> int:
        rows = (await self.db.execute(
            select(BudgetReservation).where(
                BudgetReservation.status == "held",
                BudgetReservation.expires_at <= _utcnow())
        )).scalars().all()
        for row in rows:
            row.status = "released"
        if rows:
            await self.db.flush()
        return len(rows)

    async def cost_ledger_entries(self, tenant: TenantContext, limit: int = 50,
                                  offset: int = 0,
                                  ) -> tuple[list[CostLedgerView], int]:
        from app.governance.models import CostLedgerEntry
        q = select(CostLedgerEntry).where(
            CostLedgerEntry.tenant_id == tenant.tenant_id)
        total = (await self.db.execute(
            select(func.count()).select_from(q.subquery()))).scalar_one()
        rows = (await self.db.execute(
            q.order_by(CostLedgerEntry.recorded_at.desc()).limit(limit).offset(offset)
        )).scalars().all()
        return [CostLedgerView(
            id=r.id, task_id=r.task_id, run_id=r.run_id, model=r.model,
            input_tokens=r.input_tokens, output_tokens=r.output_tokens,
            cost_usd=r.cost_usd, credits_drawn=r.credits_drawn,
            recorded_at=r.recorded_at) for r in rows], total

    async def list_alerts(self, tenant: TenantContext,
                          limit: int = 100) -> list[SpendAlertView]:
        rows = (await self.db.execute(
            select(SpendAlert).where(SpendAlert.tenant_id == tenant.tenant_id)
            .order_by(SpendAlert.fired_at.desc()).limit(limit)
        )).scalars().all()
        return [SpendAlertView(
            id=r.id, budget_id=r.budget_id, threshold=r.threshold,
            fired_at=r.fired_at, acknowledged_at=r.acknowledged_at)
            for r in rows]

    # -- internals -----------------------------------------------------
    async def _current_period(self, budget: Budget, now: datetime) -> BudgetPeriod:
        starts_at, ends_at = period_bounds(budget.period, now)
        period = (await self.db.execute(
            select(BudgetPeriod).where(
                BudgetPeriod.budget_id == budget.id,
                BudgetPeriod.starts_at == starts_at)
        )).scalar_one_or_none()
        if period is None:
            period = BudgetPeriod(budget_id=budget.id, tenant_id=budget.tenant_id,
                                  starts_at=starts_at, ends_at=ends_at,
                                  credits_used=Decimal("0"))
            self.db.add(period)
            await self.db.flush()
        return period

    async def _held_credits(self, budget_id: str, period_id: str) -> Decimal:
        total = (await self.db.execute(
            select(func.coalesce(func.sum(BudgetReservation.credits_reserved), 0)).where(
                BudgetReservation.budget_id == budget_id,
                BudgetReservation.period_id == period_id,
                BudgetReservation.status == "held")
        )).scalar_one()
        return Decimal(str(total))

    async def _maybe_fire_alert(self, budget: Budget, used: Decimal,
                                now: datetime) -> None:
        if budget.credit_limit <= 0:
            return
        fraction = float(used / budget.credit_limit)
        for threshold in (budget.alert_thresholds or []):
            t = float(threshold)
            if fraction >= t:
                existing = (await self.db.execute(
                    select(SpendAlert).where(
                        SpendAlert.budget_id == budget.id,
                        SpendAlert.threshold == Decimal(str(t)),
                        SpendAlert.acknowledged_at.is_(None))
                )).scalar_one_or_none()
                if existing is None:
                    self.db.add(SpendAlert(
                        tenant_id=budget.tenant_id, budget_id=budget.id,
                        threshold=Decimal(str(t)), fired_at=now))
        await self.db.flush()
