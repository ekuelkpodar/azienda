"""Comms provider abstraction.

A provider is the only thing that touches a real network. Business logic depends
on the ``CommsProvider`` protocol, never on a vendor SDK.

MVP ships exactly one provider: ``LogOnlyProvider``. It records the outbound
message to the message log and returns success WITHOUT transmitting anything.
Twilio (SMS/voice), SendGrid (email), etc. are documented future providers —
each is a new class implementing this protocol, configured per-channel.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from .schemas import ChannelKind

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OutboundPayload:
    """What the service hands to a provider. No tenant internals beyond addressing."""

    to_address: str
    body: str
    kind: ChannelKind
    subject: str | None = None
    from_address: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderResult:
    ok: bool
    provider_message_id: str | None = None
    cost_usd: Decimal = Decimal("0")
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class CommsProvider(Protocol):
    """Vendor-neutral send contract. Implementations own all vendor SDK usage."""

    @property
    def name(self) -> str: ...

    async def send(self, payload: OutboundPayload) -> ProviderResult: ...


class LogOnlyProvider:
    """MVP default provider. Records the send to the log; transmits nothing.

    This is NOT fake functionality: it honestly logs instead of sending, which is
    the correct MVP behavior until a real provider is configured. Every message
    sent through it carries ``provider="log_only"`` and a ``log-`` message id so
    downstream analytics can distinguish logged from transmitted traffic.
    """

    @property
    def name(self) -> str:
        return "log_only"

    async def send(self, payload: OutboundPayload) -> ProviderResult:
        message_id = f"log-{uuid.uuid4().hex[:16]}"
        logger.info(
            "comms.log_only.send",
            extra={
                "provider_message_id": message_id,
                "kind": payload.kind.value,
                "to": payload.to_address,
                "body_chars": len(payload.body),
                "at": datetime.now(UTC).isoformat(),
            },
        )
        return ProviderResult(ok=True, provider_message_id=message_id)


# ------------------------------------------------------------------ future providers
# Documented future (do NOT implement vendor SDKs here without an ADR):
#   - TwilioProvider(name="twilio"): SMS + voice via Twilio REST API. Config keys:
#       account_sid_ref, auth_token_ref (SecretBroker refs), from_number per channel.
#   - SendGridProvider(name="sendgrid"): email via SendGrid v3 API. Config keys:
#       api_key_ref (SecretBroker ref), default from identity per channel.
#   - Console/chat providers follow the same shape. Each new provider is registered
#     in the service's provider registry and selected per-channel by name.
