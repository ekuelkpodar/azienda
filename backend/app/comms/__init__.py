"""Package: comms — unified communication layer.

Public interface (other packages import ONLY these):
  - ``CommsService`` — channel/template/message management; policy-gated sends.
  - ``CommsPort`` — protocol other packages (marketing, finance, support) consume
    via dependency injection to send messages.
  - ``CommsProvider`` / ``LogOnlyProvider`` — provider abstraction + MVP default.
  - ``RateLimiter`` / ``InMemoryRateLimiter`` — send rate limiting.
  - ``CommsRepository`` / ``InMemoryCommsRepository`` — persistence seam.
  - schemas (``Channel``, ``Message``, ``MessageTemplate``, ...).

See README.md for the boundary contract.
"""
from .providers import CommsProvider, LogOnlyProvider, OutboundPayload, ProviderResult
from .ratelimit import InMemoryRateLimiter, RateLimitedError, RateLimiter
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
    TemplateRenderRequest,
    UsageSummary,
)
from .service import (
    ApprovalRequiredError,
    ChannelNotFoundError,
    CommsError,
    CommsPort,
    CommsService,
    MessageNotFoundError,
    NoActiveChannelError,
    PolicyDeniedError,
    TemplateNotFoundError,
)
from .templates import TemplateRenderError, extract_variables, render_template_string

__all__ = [
    "ApprovalRequiredError",
    "Channel",
    "ChannelCreate",
    "ChannelKind",
    "ChannelNotFoundError",
    "ChannelUpdate",
    "CommsError",
    "CommsPort",
    "CommsProvider",
    "CommsRepository",
    "CommsService",
    "DeliveryUpdate",
    "InMemoryCommsRepository",
    "InMemoryRateLimiter",
    "LogOnlyProvider",
    "Message",
    "MessageDirection",
    "MessageNotFoundError",
    "MessageSend",
    "MessageStatus",
    "MessageTemplate",
    "MessageTemplateCreate",
    "NoActiveChannelError",
    "OutboundPayload",
    "PolicyDeniedError",
    "ProviderResult",
    "RateLimiter",
    "RateLimitedError",
    "TemplateNotFoundError",
    "TemplateRenderError",
    "TemplateRenderRequest",
    "UsageSummary",
    "extract_variables",
    "render_template_string",
]
