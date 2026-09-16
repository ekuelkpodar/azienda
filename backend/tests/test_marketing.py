"""Tests for the marketing package: segmentation, governed launches,
template safety, honest attribution stub, and cross-tenant isolation."""
from __future__ import annotations

import pytest

from app.core.contracts import DomainEvent, TenantContext
from app.marketing import schemas as S
from app.marketing.segmentation import SegmentFilterError, segment_contacts
from app.marketing.service import (
    AttributionNotImplementedError,
    MarketingService,
    PolicyDeniedError,
)
from tests.conftest import FakePolicyEngine


class _Bus:
    def __init__(self) -> None:
        self.events: list[DomainEvent] = []

    async def publish(self, event: DomainEvent) -> None:
        self.events.append(event)

    def subscribe(self, topic: str, handler):  # noqa: ANN001, ANN202
        return None


class _FakeComms:
    """Structural stand-in for the CommsSenderPort (no cross-package import)."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_message(self, tenant, *, channel_kind, to_address,  # noqa: ANN001
                           body, subject=None, from_address=None,
                           idempotency_key=None, metadata=None):
        from types import SimpleNamespace
        self.sent.append({"to": to_address, "body": body, "subject": subject})
        return SimpleNamespace(id=f"msg-{len(self.sent)}")


def _svc(policy=None, comms=None, bus=None, **kw) -> MarketingService:  # noqa: ANN001
    return MarketingService(
        policy=policy if policy is not None else FakePolicyEngine(),
        comms=comms if comms is not None else _FakeComms(),
        events=bus if bus is not None else _Bus(), **kw)


_CONTACTS = [
    {"id": "c1", "email": "vip@example.com", "tags": ["vip"], "custom": {"plan": "pro"}},
    {"id": "c2", "email": "basic@example.com", "tags": ["new"], "custom": {"plan": "free"}},
    {"id": "c3", "email": "pro@example.com", "tags": [], "custom": {"plan": "pro"}},
]


# --------------------------------------------------------------- segmentation
def test_segmentation_rule_tree() -> None:
    filt = {"all": [
        {"field": "custom.plan", "op": "eq", "value": "pro"},
        {"any": [
            {"field": "tags", "op": "contains", "value": "vip"},
            {"field": "email", "op": "ends_with", "value": "pro@example.com"},
        ]},
    ]}
    matched = segment_contacts(filt, _CONTACTS)
    assert {c["id"] for c in matched} == {"c1", "c3"}


def test_segmentation_not_and_missing_field() -> None:
    filt = {"not": {"field": "tags", "op": "contains", "value": "vip"}}
    matched = segment_contacts(filt, _CONTACTS)
    assert {c["id"] for c in matched} == {"c2", "c3"}
    # missing field evaluates as None -> no crash, no match on eq
    assert segment_contacts({"field": "nope", "op": "eq", "value": "x"}, _CONTACTS) == []


def test_segmentation_malformed_filter_rejected() -> None:
    with pytest.raises(SegmentFilterError):
        segment_contacts({"field": "x", "op": "bogus_op"}, _CONTACTS)


# ---------------------------------------------------------------- campaigns
async def test_campaign_crud_and_steps(tenant: TenantContext) -> None:
    svc = _svc()
    aud = await svc.create_audience(tenant, S.AudienceCreate(name="VIPs"))
    campaign = await svc.create_campaign(tenant, S.CampaignCreate(
        name="Launch", audience_id=aud.id))
    assert campaign.status == S.CampaignStatus.DRAFT
    step = await svc.add_step(tenant, campaign.id, S.CampaignStepCreate(
        position=0, action=S.StepAction.EMAIL, name="welcome"))
    steps = await svc.list_steps(tenant, campaign.id)
    assert len(steps) == 1 and steps[0].id == step.id


async def test_resolve_audience_applies_filter(tenant: TenantContext) -> None:
    svc = _svc()
    aud = await svc.create_audience(tenant, S.AudienceCreate(
        name="Pros", filter={"field": "custom.plan", "op": "eq", "value": "pro"}))
    matched = await svc.resolve_audience(tenant, aud.id, _CONTACTS)
    assert {c["id"] for c in matched} == {"c1", "c3"}


# ------------------------------------------------------------------- launch
async def _launchable(tenant, svc, comms):  # noqa: ANN001
    aud = await svc.create_audience(tenant, S.AudienceCreate(name="All"))
    campaign = await svc.create_campaign(tenant, S.CampaignCreate(
        name="Blast", audience_id=aud.id))
    await svc.add_step(tenant, campaign.id, S.CampaignStepCreate(
        position=0, action=S.StepAction.EMAIL, name="step1"))
    return campaign


async def test_launch_policy_allow_sends(tenant: TenantContext) -> None:
    bus, comms = _Bus(), _FakeComms()
    svc = _svc(comms=comms, bus=bus)
    campaign = await _launchable(tenant, svc, comms)
    result = await svc.launch_campaign(tenant, campaign.id, contacts=_CONTACTS)
    assert result.status == S.CampaignStatus.SENT
    assert result.sends_queued == len(_CONTACTS)
    assert len(comms.sent) == len(_CONTACTS)
    topics = {e.topic for e in bus.events}
    assert "marketing.campaign.launched" in topics
    assert "marketing.campaign.send_completed" in topics


async def test_launch_denied_by_policy(tenant: TenantContext) -> None:
    policy = FakePolicyEngine()
    policy.deny_actions.add("marketing.campaign.launch")
    svc = _svc(policy=policy)
    campaign = await _launchable(tenant, svc, None)
    with pytest.raises(PolicyDeniedError):
        await svc.launch_campaign(tenant, campaign.id, contacts=_CONTACTS)


async def test_launch_require_approval_parks(tenant: TenantContext) -> None:
    policy = FakePolicyEngine()
    policy.require_approval_actions.add("marketing.campaign.launch")
    comms, bus = _FakeComms(), _Bus()
    svc = _svc(policy=policy, comms=comms, bus=bus)
    campaign = await _launchable(tenant, svc, comms)
    result = await svc.launch_campaign(tenant, campaign.id, contacts=_CONTACTS)
    assert result.status == S.CampaignStatus.AWAITING_APPROVAL
    assert result.sends_queued == 0
    assert comms.sent == []
    # campaign itself is parked awaiting approval
    parked = await svc.get_campaign(tenant, campaign.id)
    assert parked.status == S.CampaignStatus.AWAITING_APPROVAL


async def test_launch_fail_closed_without_policy(tenant: TenantContext) -> None:
    svc = MarketingService(policy=None, comms=_FakeComms(), events=_Bus())
    campaign = await _launchable(tenant, svc, None)
    with pytest.raises(PolicyDeniedError, match="no policy engine"):
        await svc.launch_campaign(tenant, campaign.id, contacts=_CONTACTS)


async def test_launch_idempotent(tenant: TenantContext) -> None:
    comms = _FakeComms()
    svc = _svc(comms=comms)
    campaign = await _launchable(tenant, svc, comms)
    r1 = await svc.launch_campaign(tenant, campaign.id, contacts=_CONTACTS,
                                   idempotency_key="launch-1")
    # reset campaign to draft-equivalent? second call with same key short-circuits
    svc2_campaign_status = (await svc.get_campaign(tenant, campaign.id)).status
    assert svc2_campaign_status == S.CampaignStatus.SENT
    r2 = await svc.launch_campaign(tenant, campaign.id, contacts=_CONTACTS,
                                   idempotency_key="launch-1")
    assert r1.campaign_id == r2.campaign_id
    assert len(comms.sent) == len(_CONTACTS)  # no duplicate sends


async def test_launch_bulk_threshold_flags_risk_context(tenant: TenantContext) -> None:
    policy = FakePolicyEngine()
    svc = _svc(policy=policy, bulk_approval_threshold=2)
    campaign = await _launchable(tenant, svc, None)
    await svc.launch_campaign(tenant, campaign.id, contacts=_CONTACTS)
    assert policy.evaluations
    req = policy.evaluations[-1]
    assert req.risk_context.get("bulk") is True


# ------------------------------------------------------- attribution honesty
async def test_attribution_not_implemented(tenant: TenantContext) -> None:
    svc = _svc()
    campaign = await svc.create_campaign(tenant, S.CampaignCreate(name="X"))
    with pytest.raises(AttributionNotImplementedError):
        await svc.attribution_report(tenant, campaign.id)


# ------------------------------------------------------- cross-tenant safety
async def test_cross_tenant_invisibility(tenant: TenantContext,
                                         tenant_b: TenantContext) -> None:
    svc = _svc()
    campaign = await svc.create_campaign(tenant, S.CampaignCreate(name="Secret"))
    assert await svc.list_campaigns(tenant_b) == []
    assert await svc.list_audiences(tenant_b) == []
    from app.marketing.service import CampaignNotFoundError
    with pytest.raises(CampaignNotFoundError):
        await svc.get_campaign(tenant_b, campaign.id)


async def test_analytics_counts_sends(tenant: TenantContext) -> None:
    svc = _svc()
    campaign = await _launchable(tenant, svc, None)
    await svc.launch_campaign(tenant, campaign.id, contacts=_CONTACTS)
    analytics = await svc.campaign_analytics(tenant, campaign.id)
    assert analytics.sent == len(_CONTACTS)
