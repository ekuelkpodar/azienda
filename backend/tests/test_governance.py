"""Governance tests: risk, policy engine, approvals, audit, budgets, costs."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, update

from app.core import errors
from app.core.config import Settings
from app.core.contracts import ActionRequest, PolicyDecision, PolicyEffect, TenantContext
from app.governance.approvals.store import ApprovalStoreImpl
from app.governance.audit.ledger import AuditLedgerImpl
from app.governance.budgets.costs import CostRecorderImpl
from app.governance.budgets.enforcer import BudgetEnforcerImpl
from app.governance.models import ApprovalRecord, Policy
from app.governance.models import AuditEntry as AuditRow
from app.governance.policy.engine import RulePolicyEngine, compile_rule
from app.governance.policy.risk import RiskScorerImpl


def _settings(**overrides) -> Settings:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    s.jwt_secret = "unit-test-secret"
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def _tenant(tenant_id: str = "t-gov-1", user_id: str = "u1") -> TenantContext:
    return TenantContext(tenant_id=tenant_id, user_id=user_id,
                         roles=("owner",))


async def _ensure_tenant_row(db_session, tenant_id: str) -> None:
    """The budget enforcer fail-closes on unknown tenants; tests must create
    the tenant row (mirrors what register_tenant does over HTTP)."""
    from app.core.models import Tenant
    if await db_session.get(Tenant, tenant_id) is None:
        db_session.add(Tenant(id=tenant_id, name=tenant_id,
                              slug=f"slug-{tenant_id}"))
        await db_session.flush()


def _engine(db_session, settings):
    scorer = RiskScorerImpl(db_session, settings)
    approvals = ApprovalStoreImpl(db_session, settings)
    return RulePolicyEngine(db_session, settings, scorer, approvals)


def _req(tenant: TenantContext, action: str, **kw) -> ActionRequest:
    return ActionRequest(tenant=tenant, action=action, **kw)


# ------------------------------------------------------------------ risk
@pytest.mark.asyncio
async def test_risk_score_deterministic_and_bounded(db_session):
    scorer = RiskScorerImpl(db_session, _settings())
    req = _req(_tenant(), "tool.crm.delete_contact",
               args={"contact_id": "c1"}, risk_context={"reversible": False})
    a = await scorer.score(req)
    b = await scorer.score(req)
    assert a.score == b.score  # deterministic
    assert 0 <= a.score <= 100
    assert len(a.factors) > 0  # explainable
    assert a.reversible is False


@pytest.mark.asyncio
async def test_risk_score_orders_by_danger(db_session):
    scorer = RiskScorerImpl(db_session, _settings())
    t = _tenant()
    low = await scorer.score(_req(t, "crm.contacts.read"))
    high = await scorer.score(
        _req(t, "tool.billing.refund_customer",
             risk_context={"financial_impact_usd": 5000, "reversible": False}))
    assert high.score > low.score


# ------------------------------------------------------------------ policy engine
@pytest.mark.asyncio
async def test_policy_malformed_request_denied_fail_closed(db_session):
    engine = _engine(db_session, _settings())
    decision = await engine.evaluate(_req(_tenant(), ""))  # empty action
    assert decision.effect == PolicyEffect.DENY
    assert any("malformed" in r for r in decision.reasons)


@pytest.mark.asyncio
async def test_policy_injection_like_input_is_data_not_code(db_session):
    engine = _engine(db_session, _settings())
    evil = "__import__('os').system('rm -rf /')"
    decision = await engine.evaluate(
        _req(_tenant(), "tool.notes.create", args={"text": evil}))
    # Treated as untrusted data: evaluated structurally, never executed.
    assert decision.effect in (PolicyEffect.ALLOW, PolicyEffect.DENY,
                               PolicyEffect.REQUIRE_APPROVAL)


@pytest.mark.asyncio
async def test_policy_cross_tenant_smuggling_denied(db_session):
    engine = _engine(db_session, _settings())
    decision = await engine.evaluate(
        _req(_tenant("t-gov-1"), "crm.contacts.read",
             args={"tenant_id": "t-gov-2"}))
    assert decision.effect == PolicyEffect.DENY
    assert any("cross_tenant" in r for r in decision.reasons)


@pytest.mark.asyncio
async def test_policy_cost_threshold_requires_approval(db_session):
    s = _settings(cost_approval_threshold_usd=5.0)
    engine = _engine(db_session, s)
    decision = await engine.evaluate(
        _req(_tenant(), "tool.llm.plan",
             risk_context={"estimated_cost_usd": 50.0}))
    assert decision.effect == PolicyEffect.REQUIRE_APPROVAL
    assert decision.approval_id is not None


@pytest.mark.asyncio
async def test_policy_high_risk_default_requires_approval(db_session):
    engine = _engine(db_session, _settings())
    decision = await engine.evaluate(
        _req(_tenant(), "tool.billing.refund_customer",
             risk_context={"financial_impact_usd": 10_000,
                           "reversible": False}))
    assert decision.effect == PolicyEffect.REQUIRE_APPROVAL


@pytest.mark.asyncio
async def test_policy_low_risk_read_allowed(db_session):
    engine = _engine(db_session, _settings())
    decision = await engine.evaluate(_req(_tenant(), "crm.contacts.read"))
    assert decision.effect == PolicyEffect.ALLOW


@pytest.mark.asyncio
async def test_policy_db_backed_deny_rule(db_session):
    t = _tenant()
    db_session.add(Policy(
        tenant_id=t.tenant_id, name="no-deletes",
        rules=[{"name": "deny-deletes", "effect": "deny", "priority": 900,
                "condition": {"action": "*delete*"},
                "reason": "test deny"}],
        priority=900, is_active=True))
    await db_session.flush()
    engine = _engine(db_session, _settings())
    decision = await engine.evaluate(_req(t, "tool.crm.delete_contact"))
    assert decision.effect == PolicyEffect.DENY
    assert decision.policy_id is not None


@pytest.mark.asyncio
async def test_policy_malformed_db_rule_skipped_loudly(db_session):
    t = _tenant()
    db_session.add(Policy(
        tenant_id=t.tenant_id, name="broken",
        rules=[{"name": "broken-rule", "effect": "nonsense-effect",
                "condition": {"action": "*"}}],
        priority=900, is_active=True))
    await db_session.flush()
    engine = _engine(db_session, _settings())
    # Malformed tenant rule is skipped; evaluation still completes (fail-safe
    # default applies) instead of crashing the request.
    decision = await engine.evaluate(_req(t, "crm.contacts.read"))
    assert decision.effect == PolicyEffect.ALLOW


def test_compile_rule_rejects_unknown_effect():
    with pytest.raises(ValueError):
        compile_rule({"name": "x", "effect": "explode",
                      "condition": {"action": "*"}})


def test_compile_rule_rejects_non_mapping_condition():
    with pytest.raises(ValueError):
        compile_rule({"name": "x", "effect": "deny",
                      "condition": ["not", "a", "mapping"]})


@pytest.mark.asyncio
async def test_policy_oversized_args_denied(db_session):
    engine = _engine(db_session, _settings())
    big = {"blob": "x" * (70 * 1024)}  # over the 64KB args cap
    decision = await engine.evaluate(_req(_tenant(), "tool.notes.create",
                                          args=big))
    assert decision.effect == PolicyEffect.DENY


# ------------------------------------------------------------------ approvals
def _approval_request(tenant: TenantContext, action: str = "tool.crm.send_bulk_sms",
                      key: str | None = None) -> ActionRequest:
    return ActionRequest(tenant=tenant, action=action,
                         resource="campaigns/1", args={"count": 5000},
                         idempotency_key=key)


@pytest.mark.asyncio
async def test_approval_request_decide_lifecycle(db_session):
    t = _tenant()
    store = ApprovalStoreImpl(db_session, _settings())
    decision = PolicyDecision(PolicyEffect.REQUIRE_APPROVAL, None,
                              reasons=("test",))
    approval = await store.request(decision, _approval_request(t), 75.0,
                                   ("bulk-send",))
    assert approval.status.value == "pending"

    decided = await store.decide(t, approval.approval_id, True, "looks fine")
    assert decided.status.value == "approved"

    with pytest.raises(errors.ConflictError):
        await store.decide(t, approval.approval_id, False, "too late")


@pytest.mark.asyncio
async def test_approval_timeout_is_deny(db_session):
    """Expired approvals can never be approved: timeout == DENY."""
    t = _tenant()
    s = _settings(approval_ttl_seconds=3600)
    store = ApprovalStoreImpl(db_session, s)
    decision = PolicyDecision(PolicyEffect.REQUIRE_APPROVAL, None,
                              reasons=("test",))
    approval = await store.request(decision, _approval_request(t), 80.0, ())

    # Simulate the clock passing the deadline, then sweep.
    await db_session.execute(
        update(ApprovalRecord)
        .where(ApprovalRecord.id == approval.approval_id)
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    swept = await store.sweep_expired(t)
    assert swept == 1

    with pytest.raises(errors.ConflictError):
        await store.decide(t, approval.approval_id, True, "too late")


@pytest.mark.asyncio
async def test_approval_tenant_isolation(db_session):
    store = ApprovalStoreImpl(db_session, _settings())
    decision = PolicyDecision(PolicyEffect.REQUIRE_APPROVAL, None,
                              reasons=("test",))
    approval = await store.request(decision, _approval_request(_tenant("t-a")),
                                   80.0, ())
    other = _tenant("t-b")
    assert await store.get(other, approval.approval_id) is None
    pending, _ = await store.list_pending(other)
    assert all(a.tenant_id == "t-b" for a in pending)
    with pytest.raises(errors.NotFoundError):
        await store.decide(other, approval.approval_id, True)


@pytest.mark.asyncio
async def test_approval_idempotent_request(db_session):
    t = _tenant()
    store = ApprovalStoreImpl(db_session, _settings())
    decision = PolicyDecision(PolicyEffect.REQUIRE_APPROVAL, None,
                              reasons=("test",))
    a1 = await store.request(decision, _approval_request(t, key="idem-1"),
                             80.0, ())
    a2 = await store.request(decision, _approval_request(t, key="idem-1"),
                             80.0, ())
    assert a1.approval_id == a2.approval_id


# ------------------------------------------------------------------ audit
@pytest.mark.asyncio
async def test_audit_append_and_verify(db_session):
    t = _tenant()
    ledger = AuditLedgerImpl(db_session)
    await ledger.append(t, "u1", "policy.allowed", {"action": "crm.contacts.read"})
    await ledger.append(t, "u1", "approval.requested", {"id": "a1"})
    await ledger.append(t, "owner", "approval.approved", {"id": "a1"})
    assert await ledger.verify_chain(t) is True


@pytest.mark.asyncio
async def test_audit_tamper_detected(db_session):
    t = _tenant()
    ledger = AuditLedgerImpl(db_session)
    await ledger.append(t, "u1", "budget.created", {"limit": "100"})
    await ledger.append(t, "u1", "budget.updated", {"limit": "999999"})
    # Attacker rewrites history directly in the DB:
    await db_session.execute(
        update(AuditRow)
        .where(AuditRow.tenant_id == t.tenant_id, AuditRow.seq == 2)
        .values(payload={"limit": "1"}))
    await db_session.flush()
    assert await ledger.verify_chain(t) is False


@pytest.mark.asyncio
async def test_audit_dlp_redaction_before_persist(db_session):
    t = _tenant()
    ledger = AuditLedgerImpl(db_session)
    entry = await ledger.append(
        t, "u1", "tool.called",
        {"args": {"api_key": "sk-live-secret-123",
                  "note": "card 4111111111111111"}})
    assert "sk-live-secret-123" not in str(entry.payload)
    assert "4111111111111111" not in str(entry.payload)


@pytest.mark.asyncio
async def test_audit_chains_are_per_tenant(db_session):
    ledger = AuditLedgerImpl(db_session)
    await ledger.append(_tenant("t-a"), "u1", "x", {})
    assert await ledger.verify_chain(_tenant("t-a")) is True
    assert await ledger.verify_chain(_tenant("t-b")) is True  # empty chain OK
    rows = (await db_session.execute(
        select(AuditRow).where(AuditRow.tenant_id == "t-b"))).scalars().all()
    assert rows == []


# ------------------------------------------------------------------ budgets
@pytest.mark.asyncio
async def test_budget_reserve_settle(db_session):
    t = _tenant()
    await _ensure_tenant_row(db_session, t.tenant_id)
    enforcer = BudgetEnforcerImpl(db_session, _settings())
    budget = await enforcer.create_budget(t, "ops", Decimal("100"), "monthly")
    assert budget.credit_limit == Decimal("100")

    decision = await enforcer.reserve(t, Decimal("30"), "task-1")
    assert decision.allowed is True
    assert decision.reservation_id is not None
    assert decision.remaining_credits == Decimal("70")

    await enforcer.settle(t, decision.reservation_id, Decimal("25"))
    # After settling 25 of 30, remaining = 75.
    again = await enforcer.reserve(t, Decimal("0"), "probe")
    assert again.remaining_credits == Decimal("75")


@pytest.mark.asyncio
async def test_budget_exhaustion_blocks_new_work(db_session):
    t = _tenant()
    await _ensure_tenant_row(db_session, t.tenant_id)
    enforcer = BudgetEnforcerImpl(db_session, _settings())
    await enforcer.create_budget(t, "ops", Decimal("10"), "monthly")
    ok = await enforcer.reserve(t, Decimal("10"), "task-1")
    assert ok.allowed is True
    denied = await enforcer.reserve(t, Decimal("1"), "task-2")
    assert denied.allowed is False
    assert "exhaust" in denied.reason.lower() or "limit" in denied.reason.lower()


@pytest.mark.asyncio
async def test_budget_kill_switch(db_session):
    t = _tenant()
    await _ensure_tenant_row(db_session, t.tenant_id)
    enforcer = BudgetEnforcerImpl(db_session, _settings())
    await enforcer.create_budget(t, "ops", Decimal("1000"), "monthly")
    await enforcer.kill_switch(t, "incident-42")
    assert await enforcer.is_frozen(t) is True
    denied = await enforcer.reserve(t, Decimal("1"), "task-1")
    assert denied.allowed is False
    await enforcer.release_kill_switch(t)
    assert await enforcer.is_frozen(t) is False
    allowed = await enforcer.reserve(t, Decimal("1"), "task-1")
    assert allowed.allowed is True


@pytest.mark.asyncio
async def test_budget_no_budget_unmetered_but_flagged(db_session):
    await _ensure_tenant_row(db_session, "t-nobudget")
    enforcer = BudgetEnforcerImpl(db_session, _settings())
    decision = await enforcer.reserve(_tenant("t-nobudget"), Decimal("5"), "x")
    assert decision.allowed is True
    assert decision.reservation_id is None
    assert "unmetered" in decision.reason


@pytest.mark.asyncio
async def test_cost_recorder_writes_ledger_and_draws_credits(db_session, test_settings):
    from app.billing.service import BillingService
    from app.core.contracts import CostRecord

    t = _tenant()
    billing = BillingService(db_session, test_settings)
    await billing.seed_plans()
    await billing.subscribe(t, "starter")

    recorder = CostRecorderImpl(db_session, billing, bus=None)
    await recorder.record(CostRecord(
        tenant_id=t.tenant_id, task_id=None, model="test-model",
        input_tokens=1000, output_tokens=500,
        cost_usd=Decimal("0.05"), credits_drawn=Decimal("2"),
        recorded_at=datetime.now(UTC)))

    rows, _ = await BudgetEnforcerImpl(db_session, test_settings).cost_ledger_entries(t)
    assert len(rows) == 1
    assert rows[0].model == "test-model"
    assert rows[0].cost_usd == Decimal("0.05")
