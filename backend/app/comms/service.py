"""CommsService — governed, provider-neutral outbound messaging.

Send path (binding order):
  1. Idempotency dedupe (replay within the retention window returns the original).
  2. Rate-limit check (429-style ``RateLimitedError`` before any policy spend).
  3. Policy evaluation via the injected ``PolicyEngine`` — externally visible
     effects MUST be allow/deny/require_approval BEFORE execution. Fail closed:
     an engine exception is treated as DENY.
  4. Provider send (the only network-touching step).
  5. Persist message record + delivery status; emit ``comms.message.sent``.

Delivery receipts (``record_delivery``) update status and emit
``comms.message.delivery_updated``. Pass-through cost is recorded on the message
for the billing package's usage meters.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from app.core.contracts import (
    ActionRequest,
    DomainEvent,
    EventBus,
    PolicyDecision,
    PolicyEffect,
    PolicyEngine,
    TenantContext,
)

from .providers import CommsProvider, LogOnlyProvider, OutboundPayload
from .ratelimit import InMemoryRateLimiter, RateLimiter
from .repository import CommsRepository, InMemoryCommsRepository
from .schemas import (
    Channel,
    ChannelCreate,
    ChannelKind,
    ChannelUpdate,
    DeliveryUpdate,
    Message,
    MessageDirection,
    MessageSend,
    MessageStatus,
    MessageTemplate,
    MessageTemplateCreate,
    UsageSummary,
)
from .templates import extract_variables, render_template_string


# ------------------------------------------------------------------ errors
class CommsError(Exception):
    """Base for comms domain errors."""


class ChannelNotFoundError(CommsError):
    pass


class MessageNotFoundError(CommsError):
    pass


class TemplateNotFoundError(CommsError):
    pass


class NoActiveChannelError(CommsError):
    """No active channel exists for the requested kind."""


class PolicyDeniedError(CommsError):
    def __init__(self, reasons: tuple[str, ...]):
        self.reasons = reasons
        super().__init__(f"send denied by policy: {'; '.join(reasons) or 'no reason given'}")


class ApprovalRequiredError(CommsError):
    def __init__(self, approval_id: str | None):
        self.approval_id = approval_id
        super().__init__("send requires human approval before it can proceed")


# ------------------------------------------------------------------ cross-package port
@runtime_checkable
class CommsPort(Protocol):
    """Public seam other packages (marketing, finance, support) use to send.

    Implemented by ``CommsService``. Consumed via dependency injection —
    never by importing this package's internals.
    """

    async def send_message(self, tenant: TenantContext, *, channel_kind: ChannelKind,
                         to_address: str, body: str, subject: str | None = None,
                         from_address: str | None = None,
                         idempotency_key: str | None = None,
                         metadata: dict[str, Any] | None = None) -> Message: ...


# ------------------------------------------------------------------ service
def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid.uuid4().hex


class CommsService:
    """Unified communication layer. Provider-neutral; policy-gated sends."""

    def __init__(
        self,
        repo: CommsRepository | None = None,
        providers: dict[str, CommsProvider] | None = None,
        policy: PolicyEngine | None = None,
        events: EventBus | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self._repo = repo or InMemoryCommsRepository()
        self._providers: dict[str, CommsProvider] = providers or {"log_only": LogOnlyProvider()}
        self._policy = policy
        self._events = events
        self._rate_limiter = rate_limiter or InMemoryRateLimiter()

    # -- channels ------------------------------------------------------
    async def create_channel(self, tenant: TenantContext, data: ChannelCreate) -> Channel:
        if data.provider not in self._providers:
            raise CommsError(f"unknown provider {data.provider!r}; "
                             f"registered: {sorted(self._providers)}")
        channel = Channel(
            id=_new_id(), tenant_id=tenant.tenant_id, kind=data.kind, name=data.name,
            provider=data.provider, config=dict(data.config), is_active=True,
            created_at=_utcnow(),
        )
        return await self._repo.add_channel(channel)

    async def get_channel(self, tenant: TenantContext, channel_id: str) -> Channel:
        channel = await self._repo.get_channel(tenant.tenant_id, channel_id)
        if channel is None:
            raise ChannelNotFoundError(channel_id)
        return channel

    async def list_channels(
        self, tenant: TenantContext, kind: ChannelKind | None = None
    ) -> list[Channel]:
        return await self._repo.list_channels(tenant.tenant_id, kind)

    async def update_channel(
        self, tenant: TenantContext, channel_id: str, data: ChannelUpdate
    ) -> Channel:
        channel = await self.get_channel(tenant, channel_id)
        patch = data.model_dump(exclude_unset=True)
        updated = channel.model_copy(update=patch)
        return await self._repo.update_channel(updated)

    async def deactivate_channel(self, tenant: TenantContext, channel_id: str) -> Channel:
        return await self.update_channel(tenant, channel_id, ChannelUpdate(is_active=False))

    async def _default_channel(self, tenant: TenantContext, kind: ChannelKind) -> Channel:
        channels = [c for c in await self._repo.list_channels(tenant.tenant_id, kind)
                    if c.is_active]
        if not channels:
            raise NoActiveChannelError(
                f"no active {kind.value} channel for tenant; create one first")
        return channels[0]

    # -- templates -----------------------------------------------------
    async def create_template(
        self, tenant: TenantContext, data: MessageTemplateCreate
    ) -> MessageTemplate:
        variables = data.variables or extract_variables(data.body)
        template = MessageTemplate(
            id=_new_id(), tenant_id=tenant.tenant_id, name=data.name, kind=data.kind,
            subject=data.subject, body=data.body, variables=variables,
            created_at=_utcnow(),
        )
        return await self._repo.add_template(template)

    async def list_templates(
        self, tenant: TenantContext, kind: ChannelKind | None = None
    ) -> list[MessageTemplate]:
        return await self._repo.list_templates(tenant.tenant_id, kind)

    async def render_template(
        self, tenant: TenantContext, template_id: str, variables: dict[str, Any]
    ) -> str:
        template = await self._repo.get_template(tenant.tenant_id, template_id)
        if template is None:
            raise TemplateNotFoundError(template_id)
        return render_template_string(template.body, variables)

    # -- send (policy-gated) -------------------------------------------
    async def send(self, tenant: TenantContext, data: MessageSend) -> Message:
        # 1. idempotency: replay returns the original message
        if data.idempotency_key:
            existing = await self._repo.get_by_idempotency_key(
                tenant.tenant_id, data.idempotency_key)
            if existing is not None:
                return existing

        channel = (await self.get_channel(tenant, data.channel_id)
                   if data.channel_id else await self._default_channel(tenant, data.channel_kind))
        if not channel.is_active:
            raise NoActiveChannelError(f"channel {channel.id} is not active")

        body = data.body
        if data.template_id:
            rendered = await self.render_template(tenant, data.template_id,
                                                  data.template_variables)
            # Template wins over the raw body when both are supplied.
            body = rendered

        # 2. rate limit before policy spend
        await self._rate_limiter.check(tenant.tenant_id, channel.kind.value)

        # 3. policy gate — externally visible effect
        await self._enforce_policy(
            tenant, action="comms.message.send",
            resource=f"channel:{channel.id}",
            args={"channel_kind": channel.kind.value, "to_address": data.to_address,
                  "provider": channel.provider},
            risk_context={"externally_visible": True, "reversible": False},
        )

        provider = self._providers.get(channel.provider)
        if provider is None:  # pragma: no cover - guarded at channel creation
            raise CommsError(f"provider {channel.provider!r} is not registered")

        message = Message(
            id=_new_id(), tenant_id=tenant.tenant_id,
            conversation_id=data.conversation_id, channel_id=channel.id,
            kind=channel.kind, direction=MessageDirection.OUTBOUND,
            to_address=data.to_address, from_address=data.from_address,
            subject=data.subject, body=body, template_id=data.template_id,
            status=MessageStatus.SENDING, provider=channel.provider,
            idempotency_key=data.idempotency_key, created_at=_utcnow(),
        )
        message = await self._repo.add_message(message)

        result = await provider.send(OutboundPayload(
            to_address=data.to_address, body=body, kind=channel.kind,
            subject=data.subject, from_address=data.from_address,
            metadata=dict(data.metadata),
        ))

        message = message.model_copy(update={
            "status": MessageStatus.SENT if result.ok else MessageStatus.FAILED,
            "provider_message_id": result.provider_message_id,
            "cost_usd": result.cost_usd,
            "error": result.error,
            "sent_at": _utcnow() if result.ok else None,
        })
        message = await self._repo.update_message(message)
        await self._emit(tenant, "comms.message.sent", message.id, {
            "message_id": message.id, "kind": message.kind.value,
            "to_address": message.to_address, "status": message.status.value,
            "provider": message.provider,
            "provider_message_id": message.provider_message_id,
            "cost_usd": str(message.cost_usd),
        })
        if not result.ok:
            raise CommsError(f"provider {channel.provider!r} failed: {result.error}")
        return message

    # CommsPort implementation (positional tenant per contracts convention)
    async def send_message(self, tenant: TenantContext, *, channel_kind: ChannelKind,
                           to_address: str, body: str, subject: str | None = None,
                           from_address: str | None = None,
                           idempotency_key: str | None = None,
                           metadata: dict[str, Any] | None = None) -> Message:
        return await self.send(tenant, MessageSend(
            channel_kind=channel_kind, to_address=to_address, body=body,
            subject=subject, from_address=from_address,
            idempotency_key=idempotency_key, metadata=metadata or {}))

    async def _enforce_policy(self, tenant: TenantContext, *, action: str,
                              resource: str | None,
                              args: dict[str, Any],
                              risk_context: dict[str, Any]) -> PolicyDecision:
        if self._policy is None:
            # No governance rail wired: fail closed for externally visible actions.
            raise PolicyDeniedError(("no policy engine configured — refusing to send",))
        try:
            decision = await self._policy.evaluate(ActionRequest(
                tenant=tenant, action=action, resource=resource, args=args,
                risk_context=risk_context))
        except Exception as exc:
            # Fail closed: a policy evaluation failure is a DENY, never a bypass.
            raise PolicyDeniedError((f"policy evaluation failed: {exc}",)) from exc
        if decision.effect == PolicyEffect.DENY:
            raise PolicyDeniedError(decision.reasons)
        if decision.effect == PolicyEffect.REQUIRE_APPROVAL:
            raise ApprovalRequiredError(decision.approval_id)
        return decision

    # -- delivery log --------------------------------------------------
    async def record_delivery(
        self, tenant: TenantContext, message_id: str, update: DeliveryUpdate
    ) -> Message:
        message = await self._repo.get_message(tenant.tenant_id, message_id)
        if message is None:
            raise MessageNotFoundError(message_id)
        patch: dict[str, Any] = {"status": update.status}
        if update.provider_message_id:
            patch["provider_message_id"] = update.provider_message_id
        if update.error is not None:
            patch["error"] = update.error
        if update.status == MessageStatus.DELIVERED:
            patch["delivered_at"] = _utcnow()
        message = message.model_copy(update=patch)
        message = await self._repo.update_message(message)
        await self._emit(tenant, "comms.message.delivery_updated", message.id, {
            "message_id": message.id, "status": message.status.value,
            "provider_message_id": message.provider_message_id,
        })
        return message

    async def get_message(self, tenant: TenantContext, message_id: str) -> Message:
        message = await self._repo.get_message(tenant.tenant_id, message_id)
        if message is None:
            raise MessageNotFoundError(message_id)
        return message

    async def list_messages(
        self, tenant: TenantContext, kind: ChannelKind | None = None,
        status: MessageStatus | None = None, limit: int = 50, offset: int = 0,
    ) -> list[Message]:
        return await self._repo.list_messages(tenant.tenant_id, kind, status, limit, offset)

    async def usage_summary(
        self, tenant: TenantContext, period_start: datetime, period_end: datetime
    ) -> UsageSummary:
        messages = await self._repo.list_messages(tenant.tenant_id, limit=100_000)
        by_kind: dict[str, dict[str, Any]] = {}
        for m in messages:
            if not (period_start <= m.created_at <= period_end):
                continue
            if m.direction != MessageDirection.OUTBOUND or m.status == MessageStatus.FAILED:
                continue
            bucket = by_kind.setdefault(m.kind.value, {"units": 0, "cost_usd": Decimal("0")})
            bucket["units"] += 1
            bucket["cost_usd"] += m.cost_usd
        return UsageSummary(
            tenant_id=tenant.tenant_id, period_start=period_start, period_end=period_end,
            by_kind={k: {"units": v["units"], "cost_usd": str(v["cost_usd"])}
                     for k, v in by_kind.items()},
        )

    # -- events --------------------------------------------------------
    async def _emit(self, tenant: TenantContext, topic: str, aggregate_id: str,
                    payload: dict[str, Any]) -> None:
        if self._events is None:
            return
        await self._events.publish(DomainEvent(
            topic=topic, tenant_id=tenant.tenant_id, aggregate_id=aggregate_id,
            payload=payload, event_id=_new_id(), occurred_at=_utcnow()))
