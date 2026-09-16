# comms — unified communication layer

**Status:** implemented (MVP). In-memory repository backs tests; Postgres
persistence is modeled (`models.py` + Alembic migration) and lands when the
core builder's `core/db` session factory exists.

## Boundary
Provider-neutral send layer for email/SMS/voice/chat/push. Owns channels,
message templates, the message log with delivery status, rate limiting, and
pass-through usage accounting. Raw transport lives behind the
`CommsProvider` protocol — business logic never touches a vendor SDK.

## Owned tables
`channels`, `conversations`, `messages`, `message_templates`, `comms_usage`.

## Public interfaces
- `CommsService` — channel/template/message CRUD, policy-gated `send`,
  `record_delivery`, `usage_summary`.
- `CommsPort` (protocol) — the seam `marketing/`, `finance/`, `support/` use
  via dependency injection. Implemented by `CommsService.send_message`.
- `CommsProvider` (protocol) + `LogOnlyProvider` (MVP default: logs, transmits
  nothing — honest, not fake; every message is marked `provider="log_only"`).
- `RateLimiter` (protocol) + `InMemoryRateLimiter` (fixed window; Redis-backed
  is the documented multi-replica step-up, ADR-003).
- `CommsRepository` (protocol) + `InMemoryCommsRepository`.

## Consumes
`core` (contracts only: `TenantContext`, `PolicyEngine`, `EventBus`).
Emits: `comms.message.sent`, `comms.message.delivery_updated`.

## Send path (binding)
idempotency dedupe → rate limit → **policy evaluation (fail closed)** →
provider send → persist + delivery log → domain event.

## Non-goals
Vendor SDKs. Twilio (SMS/voice) and SendGrid (email) are documented future
providers — each is a new `CommsProvider` implementation registered per
channel; see `providers.py` for config keys. HTML-email sanitization before
send is future work (templates render with `autoescape=False` for plaintext
channels; see `templates.py`).

## Rules for builders
1. Import other packages ONLY through `core/contracts.py` protocols (or this
   package's own public interface).
2. Every row carries `tenant_id`; every query filters by it.
3. Sends are externally visible → policy gate BEFORE execution, fail closed.
4. No secrets in code/logs/config — provider credentials resolve via
   `SecretBroker` refs stored in channel config, never raw values.
