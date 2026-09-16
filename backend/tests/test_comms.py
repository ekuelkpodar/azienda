"""Tests for the comms package: policy-gated sends, providers, rate limits,
templates, idempotency, and cross-tenant isolation."""
from __future__ import annotations

import pytest

from app.comms import schemas as S
from app.comms.service import (
    ApprovalRequiredError,
    CommsError,
    CommsService,
    PolicyDeniedError,
)
from app.comms.templates import TemplateRenderError
from app.core.contracts import DomainEvent, TenantContext
from tests.conftest import FakePolicyEngine


class _Bus:
    def __init__(self) -> None:
        self.events: list[DomainEvent] = []

    async def publish(self, event: DomainEvent) -> None:
        self.events.append(event)

    def subscribe(self, topic: str, handler):  # noqa: ANN001, ANN202
        return None


def _svc(policy=None, bus=None) -> CommsService:  # noqa: ANN001
    return CommsService(policy=policy if policy is not None else FakePolicyEngine(),
                        events=bus if bus is not None else _Bus())


async def _channel(svc: CommsService, tenant: TenantContext, kind=S.ChannelKind.EMAIL):
    return await svc.create_channel(
        tenant, S.ChannelCreate(kind=kind, name="main", provider="log_only", config={}))


# ---------------------------------------------------------------- happy path
async def test_send_email_policy_allow_emits_event(tenant: TenantContext) -> None:
    bus, policy = _Bus(), FakePolicyEngine()
    svc = _svc(policy, bus)
    await _channel(svc, tenant)
    msg = await svc.send(tenant, S.MessageSend(
        channel_kind=S.ChannelKind.EMAIL, to_address="a@example.com",
        subject="Hi", body="Hello {{ name }}!"))
    assert msg.status == S.MessageStatus.SENT
    assert msg.provider == "log_only"
    assert msg.provider_message_id is not None
    assert msg.provider_message_id.startswith("log-")
    assert any(e.topic == "comms.message.sent" for e in bus.events)


async def test_send_uses_explicit_channel(tenant: TenantContext) -> None:
    svc = _svc()
    ch = await _channel(svc, tenant, S.ChannelKind.SMS)
    msg = await svc.send(tenant, S.MessageSend(
        channel_kind=S.ChannelKind.SMS, to_address="+15551234567",
        body="ping", channel_id=ch.id))
    assert msg.channel_id == ch.id


# ---------------------------------------------------------------- policy gates
async def test_send_denied_by_policy(tenant: TenantContext) -> None:
    policy = FakePolicyEngine()
    policy.deny_actions.add("comms.message.send")
    svc = _svc(policy)
    await _channel(svc, tenant)
    with pytest.raises(PolicyDeniedError):
        await svc.send(tenant, S.MessageSend(
            channel_kind=S.ChannelKind.EMAIL, to_address="a@example.com", body="x"))
    assert await svc.list_messages(tenant) == []


async def test_send_require_approval_raises_before_send(tenant: TenantContext) -> None:
    policy = FakePolicyEngine()
    policy.require_approval_actions.add("comms.message.send")
    bus = _Bus()
    svc = _svc(policy, bus)
    await _channel(svc, tenant)
    with pytest.raises(ApprovalRequiredError):
        await svc.send(tenant, S.MessageSend(
            channel_kind=S.ChannelKind.EMAIL, to_address="a@example.com", body="x"))
    # fail-closed: nothing persisted, nothing transmitted while approval pending
    assert await svc.list_messages(tenant) == []


async def test_send_fail_closed_without_policy_engine(tenant: TenantContext) -> None:
    svc = CommsService(policy=None, events=_Bus())
    await _channel(svc, tenant)
    with pytest.raises(PolicyDeniedError, match="no policy engine"):
        await svc.send(tenant, S.MessageSend(
            channel_kind=S.ChannelKind.EMAIL, to_address="a@example.com", body="x"))


async def test_send_fail_closed_when_policy_raises(tenant: TenantContext) -> None:
    class _Boom:
        async def evaluate(self, request):  # noqa: ANN001, ANN202
            raise RuntimeError("rail down")

    svc = CommsService(policy=_Boom(), events=_Bus())  # type: ignore[arg-type]
    await _channel(svc, tenant)
    with pytest.raises(PolicyDeniedError, match="policy evaluation failed"):
        await svc.send(tenant, S.MessageSend(
            channel_kind=S.ChannelKind.EMAIL, to_address="a@example.com", body="x"))


# --------------------------------------------------------------- idempotency
async def test_send_idempotent_replay(tenant: TenantContext) -> None:
    svc = _svc()
    await _channel(svc, tenant)
    data = S.MessageSend(channel_kind=S.ChannelKind.EMAIL,
                         to_address="a@example.com", body="x",
                         idempotency_key="key-1")
    first = await svc.send(tenant, data)
    second = await svc.send(tenant, data)
    assert first.id == second.id
    assert len(await svc.list_messages(tenant)) == 1


# --------------------------------------------------------------- rate limits
async def test_rate_limit_blocks_burst(tenant: TenantContext) -> None:
    from app.comms.ratelimit import InMemoryRateLimiter, RateLimitedError
    svc = CommsService(policy=FakePolicyEngine(), events=_Bus(),
                       rate_limiter=InMemoryRateLimiter(limit_per_minute=2))
    await _channel(svc, tenant)
    for _ in range(2):
        await svc.send(tenant, S.MessageSend(
            channel_kind=S.ChannelKind.EMAIL, to_address="a@example.com", body="x"))
    with pytest.raises(RateLimitedError):
        await svc.send(tenant, S.MessageSend(
            channel_kind=S.ChannelKind.EMAIL, to_address="a@example.com", body="x"))


# ----------------------------------------------------------------- templates
async def test_template_render_strict_undefined(tenant: TenantContext) -> None:
    svc = _svc()
    tpl = await svc.create_template(tenant, S.MessageTemplateCreate(
        name="t", kind=S.ChannelKind.EMAIL, subject="Hi {{ name }}",
        body="Hello {{ name }}", variables=["name"]))
    rendered = await svc.render_template(tenant, tpl.id, {"name": "Ada"})
    assert rendered == "Hello Ada"
    with pytest.raises(TemplateRenderError):
        await svc.render_template(tenant, tpl.id, {})


async def test_unsafe_template_blocked_at_render(tenant: TenantContext) -> None:
    """The Jinja sandbox blocks unsafe attribute access at render time."""
    svc = _svc()
    tpl = await svc.create_template(tenant, S.MessageTemplateCreate(
        name="evil", kind=S.ChannelKind.EMAIL, subject="x",
        body="{{ ().__class__ }}", variables=[]))
    with pytest.raises(TemplateRenderError, match="unsafe"):
        await svc.render_template(tenant, tpl.id, {})


async def test_log_only_provider_honest(tenant: TenantContext) -> None:
    """LogOnlyProvider logs; it must not claim real transmission."""
    from app.comms.providers import LogOnlyProvider, OutboundPayload
    result = await LogOnlyProvider().send(OutboundPayload(
        to_address="a@example.com", body="x", kind=S.ChannelKind.EMAIL))
    assert result.ok is True
    assert result.provider_message_id is not None
    assert result.provider_message_id.startswith("log-")


# ------------------------------------------------------- cross-tenant safety
async def test_cross_tenant_invisibility(tenant: TenantContext,
                                         tenant_b: TenantContext) -> None:
    svc = _svc()
    ch = await _channel(svc, tenant)
    await svc.send(tenant, S.MessageSend(
        channel_kind=S.ChannelKind.EMAIL, to_address="a@example.com", body="x"))
    assert await svc.list_channels(tenant_b) == []
    assert await svc.list_messages(tenant_b) == []
    with pytest.raises(CommsError):
        await svc.get_channel(tenant_b, ch.id)


async def test_usage_summary(tenant: TenantContext) -> None:
    from datetime import UTC, datetime, timedelta
    svc = _svc()
    await _channel(svc, tenant)
    await svc.send(tenant, S.MessageSend(
        channel_kind=S.ChannelKind.EMAIL, to_address="a@example.com", body="x"))
    now = datetime.now(UTC)
    summary = await svc.usage_summary(tenant, now - timedelta(days=1),
                                      now + timedelta(days=1))
    assert summary.by_kind["email"]["units"] == 1
