"""Billing tests: plans as data, subscriptions, credits, overage, caps, invoices."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.billing.service import BillingService
from app.core import errors
from app.core.contracts import TenantContext


def _tenant(tenant_id: str = "t-bill-1") -> TenantContext:
    return TenantContext(tenant_id=tenant_id, user_id="u1", roles=("owner",))


@pytest.mark.asyncio
async def test_seed_plans_idempotent(db_session, test_settings):
    svc = BillingService(db_session, test_settings)
    n1 = await svc.seed_plans()
    n2 = await svc.seed_plans()  # upsert: touches all, duplicates none
    assert n1 == n2 >= 4
    plans = await svc.list_plans()
    slugs = [p["slug"] for p in plans]
    assert len(slugs) == len(set(slugs)) == n1  # no duplicates
    assert {"starter", "growth", "scale", "enterprise"} <= set(slugs)
    # Pricing is DATA: every plan carries dimensions, none hard-coded in logic.
    for p in plans:
        assert "dimensions" in p and "credits_included" in p["dimensions"]


@pytest.mark.asyncio
async def test_subscribe_and_change_plan(db_session, test_settings):
    t = _tenant()
    svc = BillingService(db_session, test_settings)
    await svc.seed_plans()
    sub = await svc.subscribe(t, "starter")
    assert sub["plan_slug"] == "starter"
    assert sub["status"] in ("trialing", "active")

    with pytest.raises(errors.ConflictError):
        await svc.subscribe(t, "growth")  # already subscribed

    changed = await svc.change_plan(t, "growth")
    assert changed["plan_slug"] == "growth"


@pytest.mark.asyncio
async def test_subscribe_unknown_plan_rejected(db_session, test_settings):
    svc = BillingService(db_session, test_settings)
    await svc.seed_plans()
    with pytest.raises(errors.NotFoundError):
        await svc.subscribe(_tenant(), "nonexistent-plan")


@pytest.mark.asyncio
async def test_credit_draw_and_pool_balance(db_session, test_settings):
    t = _tenant()
    svc = BillingService(db_session, test_settings)
    await svc.seed_plans()
    await svc.subscribe(t, "starter")  # 2000 credits included

    await svc.draw(t, Decimal("500"), "task-1", task_id="task-1")
    overage = await svc.compute_overage(t.tenant_id)
    assert Decimal(overage["credits_used"]) == Decimal("500")
    assert Decimal(overage["credits_overage"]) == Decimal("0")

    # Drawing past the included credits produces overage, not a block.
    await svc.draw(t, Decimal("2000"), "task-2")
    overage = await svc.compute_overage(t.tenant_id)
    assert Decimal(overage["credits_overage"]) == Decimal("500")
    assert Decimal(overage["credits_overage_usd"]) > Decimal("0")


@pytest.mark.asyncio
async def test_spend_cap_is_hard_stop(db_session, test_settings):
    t = _tenant()
    svc = BillingService(db_session, test_settings)
    await svc.seed_plans()
    await svc.subscribe(t, "starter")
    await svc.set_spend_cap(t, Decimal("1.00"))
    # Starter base price alone exceeds a $1 cap -> the draw must refuse.
    with pytest.raises(errors.SpendCapHitError):
        await svc.draw(t, Decimal("10"), "task-1")


@pytest.mark.asyncio
async def test_spend_cap_removal(db_session, test_settings):
    t = _tenant()
    svc = BillingService(db_session, test_settings)
    await svc.seed_plans()
    await svc.subscribe(t, "starter")
    await svc.set_spend_cap(t, Decimal("100000"))
    await svc.set_spend_cap(t, None)
    sub = await svc.get_subscription(t)
    assert sub is not None and sub["spend_cap_usd"] is None


@pytest.mark.asyncio
async def test_usage_metering_and_invoice(db_session, test_settings):
    t = _tenant()
    svc = BillingService(db_session, test_settings)
    await svc.seed_plans()
    await svc.subscribe(t, "growth")

    today = date.today()
    await svc.record_usage(t, "seats", Decimal("3"), cost_usd=Decimal("36"))
    usage = await svc.get_usage(t, today.replace(day=1), today)
    dims = {u["dimension"]: u for u in usage}
    assert Decimal(dims["seats"]["quantity"]) == Decimal("3")

    invoice = await svc.create_invoice(t)
    assert invoice["status"] == "draft"
    assert Decimal(invoice["total_usd"]) >= Decimal("0")
    assert len(invoice["lines"]) >= 1

    invoices = await svc.list_invoices(t)
    assert len(invoices) >= 1


@pytest.mark.asyncio
async def test_billing_tenant_isolation(db_session, test_settings):
    svc = BillingService(db_session, test_settings)
    await svc.seed_plans()
    await svc.subscribe(_tenant("t-a"), "starter")
    assert await svc.get_subscription(_tenant("t-b")) is None
    await svc.draw(_tenant("t-a"), Decimal("100"), "x")
    overage_b = await svc.compute_overage("t-b")
    assert Decimal(overage_b["credits_used"]) == Decimal("0")
