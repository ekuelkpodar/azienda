"""Repository protocols + in-memory implementations for comms.

The service depends on the ``CommsRepository`` protocol. ``InMemoryCommsRepository``
backs tests and local development. A Postgres-backed implementation (SQLAlchemy,
see ``models.py``) is future work pending the core builder's ``core/db`` session
factory — the protocol is the seam, so swapping it changes no callers.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from .schemas import Channel, ChannelKind, Message, MessageStatus, MessageTemplate


@runtime_checkable
class CommsRepository(Protocol):
    # channels
    async def add_channel(self, channel: Channel) -> Channel: ...
    async def get_channel(self, tenant_id: str, channel_id: str) -> Channel | None: ...
    async def update_channel(self, channel: Channel) -> Channel: ...
    async def list_channels(
        self, tenant_id: str, kind: ChannelKind | None = None
    ) -> list[Channel]: ...
    # templates
    async def add_template(self, template: MessageTemplate) -> MessageTemplate: ...
    async def get_template(self, tenant_id: str, template_id: str) -> MessageTemplate | None: ...
    async def list_templates(
        self, tenant_id: str, kind: ChannelKind | None = None
    ) -> list[MessageTemplate]: ...
    # messages
    async def add_message(self, message: Message) -> Message: ...
    async def get_message(self, tenant_id: str, message_id: str) -> Message | None: ...
    async def update_message(self, message: Message) -> Message: ...
    async def get_by_idempotency_key(self, tenant_id: str, key: str) -> Message | None: ...
    async def list_messages(
        self, tenant_id: str, kind: ChannelKind | None = None,
        status: MessageStatus | None = None, limit: int = 50, offset: int = 0,
    ) -> list[Message]: ...


class InMemoryCommsRepository:
    """Process-local repository. Tenant isolation enforced on every access."""

    def __init__(self) -> None:
        self._channels: dict[tuple[str, str], Channel] = {}
        self._templates: dict[tuple[str, str], MessageTemplate] = {}
        self._messages: dict[tuple[str, str], Message] = {}
        self._by_idempotency: dict[tuple[str, str], str] = {}

    # -- channels -----------------------------------------------------------
    async def add_channel(self, channel: Channel) -> Channel:
        self._channels[(channel.tenant_id, channel.id)] = channel
        return channel

    async def get_channel(self, tenant_id: str, channel_id: str) -> Channel | None:
        return self._channels.get((tenant_id, channel_id))

    async def update_channel(self, channel: Channel) -> Channel:
        self._channels[(channel.tenant_id, channel.id)] = channel
        return channel

    async def list_channels(self, tenant_id: str, kind: ChannelKind | None = None) -> list[Channel]:
        return [
            c for (t, _), c in self._channels.items()
            if t == tenant_id and (kind is None or c.kind == kind)
        ]

    # -- templates ----------------------------------------------------------
    async def add_template(self, template: MessageTemplate) -> MessageTemplate:
        self._templates[(template.tenant_id, template.id)] = template
        return template

    async def get_template(self, tenant_id: str, template_id: str) -> MessageTemplate | None:
        return self._templates.get((tenant_id, template_id))

    async def list_templates(
        self, tenant_id: str, kind: ChannelKind | None = None
    ) -> list[MessageTemplate]:
        return [
            t for (ten, _), t in self._templates.items()
            if ten == tenant_id and (kind is None or t.kind == kind)
        ]

    # -- messages -----------------------------------------------------------
    async def add_message(self, message: Message) -> Message:
        self._messages[(message.tenant_id, message.id)] = message
        if message.idempotency_key:
            self._by_idempotency[(message.tenant_id, message.idempotency_key)] = message.id
        return message

    async def get_message(self, tenant_id: str, message_id: str) -> Message | None:
        return self._messages.get((tenant_id, message_id))

    async def update_message(self, message: Message) -> Message:
        self._messages[(message.tenant_id, message.id)] = message
        return message

    async def get_by_idempotency_key(self, tenant_id: str, key: str) -> Message | None:
        message_id = self._by_idempotency.get((tenant_id, key))
        return self._messages.get((tenant_id, message_id)) if message_id else None

    async def list_messages(
        self, tenant_id: str, kind: ChannelKind | None = None,
        status: MessageStatus | None = None, limit: int = 50, offset: int = 0,
    ) -> list[Message]:
        items = [
            m for (t, _), m in self._messages.items()
            if t == tenant_id
            and (kind is None or m.kind == kind)
            and (status is None or m.status == status)
        ]
        items.sort(key=lambda m: m.created_at)
        return items[offset:offset + limit]
