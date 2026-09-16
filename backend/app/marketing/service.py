"""MarketingService — campaign orchestration with governed bulk sends.

Launch flow (binding order):
  1. Validate: campaign in a launchable state, has steps, audience resolves > 0.
  2. Render each step's content per contact (Jinja-sandboxed).
  3. Policy evaluation BEFORE any send, with bulk risk context
     (``audience_size``). High-impact/bulk launches are expected to return
     REQUIRE_APPROVAL from tenant policy — the service then parks the campaign
     in ``awaiting_approval`` instead of sending.
  4. Sends go through the injected ``CommsSenderPort`` — a local structural
     protocol satisfied by the comms package's service (wired by the
     composition root). Marketing never imports another business package's
     internals — core/contracts.py is the only cross-package seam — and
     never touches providers directly.
  5. Every send is recorded in the local send log; analytics rollups read the
     log. Delivery-status sync from comms webhooks is future work.
  6. Domain events: ``marketing.campaign.launched``,
     ``marketing.campaign.send_completed``, ``marketing.campaign.approval_requested``.

AI hooks (content generation, send-time optimization) belong to the agents
package and are consumed via protocols — NOT implemented here.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
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

from .repository import InMemoryMarketingRepository, MarketingRepository
from .schemas import (
    AssetStatus,
    Audience,
    AudienceCreate,
    AudienceUpdate,
    Campaign,
    CampaignAnalytics,
    CampaignCreate,
    CampaignSend,
    CampaignStatus,
    CampaignStep,
    CampaignStepCreate,
    CampaignStepUpdate,
    CampaignUpdate,
    ContentAsset,
    ContentAssetCreate,
    LaunchResult,
    SendStatus,
    StepAction,
)
from .segmentation import SegmentFilterError, segment_contacts, validate_filter
from .templates import TemplateRenderError, render_campaign_content


# ------------------------------------------------------------------ ports
@runtime_checkable
class CommsSenderPort(Protocol):
    """Local structural port for campaign message sends.

    Structurally mirrors the comms package's send capability (satisfied by its
    service) but is defined HERE so marketing never imports another business
    package's internals — core/contracts.py is the only cross-package seam.
    The composition root (API layer) injects the real comms service; ``Any``
    return keeps the comms message type local to that package.
    """

    async def send_message(
        self, tenant: TenantContext, *, channel_kind: str, to_address: str,
        body: str, subject: str | None = None, from_address: str | None = None,
        idempotency_key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Any: ...


# ------------------------------------------------------------------ errors
class MarketingError(Exception):
    """Base for marketing domain errors."""


class CampaignNotFoundError(MarketingError):
    pass


class AudienceNotFoundError(MarketingError):
    pass


class AssetNotFoundError(MarketingError):
    pass


class StepNotFoundError(MarketingError):
    pass


class CampaignStateError(MarketingError):
    """Illegal operation for the campaign's current status (409)."""


class PolicyDeniedError(MarketingError):
    def __init__(self, reasons: tuple[str, ...]):
        self.reasons = reasons
        super().__init__(f"campaign action denied by policy: {'; '.join(reasons) or 'no reason'}")


class AttributionNotImplementedError(MarketingError):
    """Attribution is a documented stub — see README. Never silently fake numbers."""


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid.uuid4().hex


_LAUNCHABLE = {CampaignStatus.DRAFT, CampaignStatus.SCHEDULED}
_EDITABLE = {CampaignStatus.DRAFT, CampaignStatus.SCHEDULED, CampaignStatus.PAUSED}
# Campaigns in these states accept status-only transitions via update.
_STATUS_TRANSITIONS: dict[CampaignStatus, set[CampaignStatus]] = {
    CampaignStatus.DRAFT: {CampaignStatus.SCHEDULED, CampaignStatus.CANCELLED},
    CampaignStatus.SCHEDULED: {CampaignStatus.DRAFT, CampaignStatus.PAUSED,
                               CampaignStatus.CANCELLED},
    CampaignStatus.PAUSED: {CampaignStatus.SCHEDULED, CampaignStatus.CANCELLED},
    CampaignStatus.CANCELLED: {CampaignStatus.DRAFT},  # revive as draft
}


class MarketingService:
    def __init__(
        self,
        repo: MarketingRepository | None = None,
        comms: CommsSenderPort | None = None,
        policy: PolicyEngine | None = None,
        events: EventBus | None = None,
        bulk_approval_threshold: int = 100,
    ) -> None:
        self._repo = repo or InMemoryMarketingRepository()
        self._comms = comms
        self._policy = policy
        self._events = events
        # ASSUMED default: launches at/above this audience size are flagged as
        # bulk in the policy risk context. Real value is tenant policy data.
        self._bulk_threshold = bulk_approval_threshold
        self._launch_idempotency: dict[tuple[str, str], LaunchResult] = {}

    # ---------------------------------------------------------------- campaigns
    async def create_campaign(self, tenant: TenantContext, data: CampaignCreate) -> Campaign:
        if data.audience_id:
            await self.get_audience(tenant, data.audience_id)
        now = _utcnow()
        campaign = Campaign(
            id=_new_id(), tenant_id=tenant.tenant_id, name=data.name,
            status=CampaignStatus.DRAFT, audience_id=data.audience_id,
            scheduled_at=data.scheduled_at, created_by=tenant.user_id,
            created_at=now, updated_at=now,
        )
        return await self._repo.add_campaign(campaign)

    async def get_campaign(self, tenant: TenantContext, campaign_id: str) -> Campaign:
        campaign = await self._repo.get_campaign(tenant.tenant_id, campaign_id)
        if campaign is None:
            raise CampaignNotFoundError(campaign_id)
        return campaign

    async def list_campaigns(self, tenant: TenantContext, limit: int = 50,
                             offset: int = 0) -> list[Campaign]:
        return await self._repo.list_campaigns(tenant.tenant_id, limit, offset)

    async def update_campaign(
        self, tenant: TenantContext, campaign_id: str, data: CampaignUpdate
    ) -> Campaign:
        campaign = await self.get_campaign(tenant, campaign_id)
        if campaign.status not in _EDITABLE and campaign.status != CampaignStatus.CANCELLED:
            raise CampaignStateError(
                f"cannot edit campaign in status {campaign.status.value}")
        patch = data.model_dump(exclude_unset=True)
        new_status = patch.pop("status", None)
        if new_status is not None:
            new_status = CampaignStatus(new_status)
            allowed = _STATUS_TRANSITIONS.get(campaign.status, set())
            if new_status not in allowed:
                raise CampaignStateError(
                    f"illegal transition {campaign.status.value} -> {new_status.value}")
            patch["status"] = new_status
        if patch.get("audience_id"):
            await self.get_audience(tenant, patch["audience_id"])
        patch["updated_at"] = _utcnow()
        return await self._repo.update_campaign(campaign.model_copy(update=patch))

    async def archive_campaign(self, tenant: TenantContext, campaign_id: str) -> Campaign:
        campaign = await self.get_campaign(tenant, campaign_id)
        if campaign.status in (CampaignStatus.SENDING, CampaignStatus.AWAITING_APPROVAL):
            raise CampaignStateError(
                f"cannot archive campaign while {campaign.status.value}")
        return await self._repo.update_campaign(
            campaign.model_copy(update={"status": CampaignStatus.ARCHIVED,
                                         "updated_at": _utcnow()}))

    # ---------------------------------------------------------------- steps
    async def add_step(
        self, tenant: TenantContext, campaign_id: str, data: CampaignStepCreate
    ) -> CampaignStep:
        campaign = await self.get_campaign(tenant, campaign_id)
        if campaign.status not in _EDITABLE:
            raise CampaignStateError(
                f"cannot modify steps of campaign in status {campaign.status.value}")
        step = CampaignStep(
            id=_new_id(), tenant_id=tenant.tenant_id, campaign_id=campaign_id,
            position=data.position, action=data.action, name=data.name,
            template_id=data.template_id, asset_id=data.asset_id,
            delay_minutes=data.delay_minutes, created_at=_utcnow(),
        )
        return await self._repo.add_step(step)

    async def list_steps(self, tenant: TenantContext, campaign_id: str) -> list[CampaignStep]:
        await self.get_campaign(tenant, campaign_id)
        return await self._repo.list_steps(tenant.tenant_id, campaign_id)

    async def update_step(
        self, tenant: TenantContext, step_id: str, data: CampaignStepUpdate
    ) -> CampaignStep:
        step = await self._repo.get_step(tenant.tenant_id, step_id)
        if step is None:
            raise StepNotFoundError(step_id)
        campaign = await self.get_campaign(tenant, step.campaign_id)
        if campaign.status not in _EDITABLE:
            raise CampaignStateError("cannot modify steps of a launched campaign")
        patch = data.model_dump(exclude_unset=True)
        return await self._repo.update_step(step.model_copy(update=patch))

    async def delete_step(self, tenant: TenantContext, step_id: str) -> None:
        step = await self._repo.get_step(tenant.tenant_id, step_id)
        if step is None:
            raise StepNotFoundError(step_id)
        campaign = await self.get_campaign(tenant, step.campaign_id)
        if campaign.status not in _EDITABLE:
            raise CampaignStateError("cannot modify steps of a launched campaign")
        await self._repo.delete_step(tenant.tenant_id, step_id)

    # ---------------------------------------------------------------- audiences
    async def create_audience(self, tenant: TenantContext, data: AudienceCreate) -> Audience:
        try:
            validate_filter(data.filter)
        except SegmentFilterError as exc:
            raise MarketingError(f"invalid audience filter: {exc}") from exc
        now = _utcnow()
        audience = Audience(
            id=_new_id(), tenant_id=tenant.tenant_id, name=data.name,
            description=data.description, filter=dict(data.filter),
            created_at=now, updated_at=now,
        )
        return await self._repo.add_audience(audience)

    async def get_audience(self, tenant: TenantContext, audience_id: str) -> Audience:
        audience = await self._repo.get_audience(tenant.tenant_id, audience_id)
        if audience is None:
            raise AudienceNotFoundError(audience_id)
        return audience

    async def list_audiences(self, tenant: TenantContext) -> list[Audience]:
        return await self._repo.list_audiences(tenant.tenant_id)

    async def update_audience(
        self, tenant: TenantContext, audience_id: str, data: AudienceUpdate
    ) -> Audience:
        audience = await self.get_audience(tenant, audience_id)
        patch = data.model_dump(exclude_unset=True)
        if "filter" in patch:
            try:
                validate_filter(patch["filter"])
            except SegmentFilterError as exc:
                raise MarketingError(f"invalid audience filter: {exc}") from exc
        patch["updated_at"] = _utcnow()
        return await self._repo.update_audience(audience.model_copy(update=patch))

    async def resolve_audience(
        self, tenant: TenantContext, audience_id: str,
        contacts: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Evaluate the audience filter against caller-supplied contacts.

        Contacts come from the CRM package (not yet built); the caller supplies
        them so marketing never reaches across the package boundary.
        """
        audience = await self.get_audience(tenant, audience_id)
        matched = segment_contacts(audience.filter, contacts)
        await self._repo.update_audience(
            audience.model_copy(update={"member_count": len(matched),
                                         "updated_at": _utcnow()}))
        return matched

    # ---------------------------------------------------------------- assets
    async def create_asset(self, tenant: TenantContext, data: ContentAssetCreate) -> ContentAsset:
        now = _utcnow()
        asset = ContentAsset(
            id=_new_id(), tenant_id=tenant.tenant_id, kind=data.kind,
            title=data.title, body=data.body, status=AssetStatus.DRAFT,
            created_by=tenant.user_id, created_at=now, updated_at=now,
        )
        return await self._repo.add_asset(asset)

    async def get_asset(self, tenant: TenantContext, asset_id: str) -> ContentAsset:
        asset = await self._repo.get_asset(tenant.tenant_id, asset_id)
        if asset is None:
            raise AssetNotFoundError(asset_id)
        return asset

    async def list_assets(self, tenant: TenantContext, kind: str | None = None
                          ) -> list[ContentAsset]:
        return await self._repo.list_assets(tenant.tenant_id, kind)

    async def publish_asset(self, tenant: TenantContext, asset_id: str) -> ContentAsset:
        asset = await self.get_asset(tenant, asset_id)
        if asset.status != AssetStatus.DRAFT:
            raise CampaignStateError(
                f"only draft assets can be published (now {asset.status.value})")
        # Brand-safety policy check for AI/business-generated content happens
        # here once the governance content policy exists; for now the publish
        # records an explicit human approval marker.
        return await self._repo.update_asset(asset.model_copy(update={
            "status": AssetStatus.APPROVED,
            "brand_check": {"checked_at": _utcnow().isoformat(),
                            "by": tenant.user_id or "system",
                            "result": "approved",
                            "note": "automated brand-safety policy: future (governance)"},
            "updated_at": _utcnow()}))

    # ---------------------------------------------------------------- launch
    async def launch_campaign(
        self, tenant: TenantContext, campaign_id: str,
        contacts: list[dict[str, Any]] | None = None,
        idempotency_key: str | None = None,
    ) -> LaunchResult:
        """Launch a campaign: policy-gated, bulk-aware, fully logged.

        ``contacts`` are the candidate recipients (from CRM); when omitted the
        audience must already have a cached ``member_count`` — in that case the
        launch resolves against an empty contact list and refuses to send to
        zero recipients. Callers (router/agents) supply contacts explicitly.
        """
        if idempotency_key:
            cached = self._launch_idempotency.get((tenant.tenant_id, idempotency_key))
            if cached is not None:
                return cached

        campaign = await self.get_campaign(tenant, campaign_id)
        if campaign.status not in _LAUNCHABLE:
            raise CampaignStateError(
                f"campaign cannot be launched from status {campaign.status.value}")
        if not campaign.audience_id:
            raise CampaignStateError("campaign has no audience assigned")
        steps = await self._repo.list_steps(tenant.tenant_id, campaign_id)
        send_steps = [s for s in steps if s.action != StepAction.WAIT]
        if not send_steps:
            raise CampaignStateError("campaign has no send steps")

        audience = await self.get_audience(tenant, campaign.audience_id)
        recipients = segment_contacts(audience.filter, contacts or [])
        if not recipients:
            raise CampaignStateError("audience resolves to zero recipients; refusing to launch")
        await self._repo.update_audience(audience.model_copy(
            update={"member_count": len(recipients), "updated_at": _utcnow()}))

        # Policy gate BEFORE any send. Bulk context lets tenant policy require
        # human approval for high-impact launches.
        decision = await self._enforce_policy(
            tenant, action="marketing.campaign.launch",
            resource=f"campaign:{campaign_id}",
            args={"campaign_id": campaign_id, "audience_size": len(recipients),
                  "step_count": len(send_steps)},
            risk_context={
                "externally_visible": True, "reversible": False,
                "bulk": len(recipients) >= self._bulk_threshold,
                "audience_size": len(recipients),
            },
        )
        if decision.effect == PolicyEffect.REQUIRE_APPROVAL:
            parked = await self._repo.update_campaign(campaign.model_copy(update={
                "status": CampaignStatus.AWAITING_APPROVAL,
                "approval_id": decision.approval_id, "updated_at": _utcnow()}))
            await self._emit(tenant, "marketing.campaign.approval_requested", campaign_id, {
                "campaign_id": campaign_id, "approval_id": decision.approval_id,
                "audience_size": len(recipients)})
            result = LaunchResult(
                campaign_id=campaign_id, status=parked.status,
                approval_id=decision.approval_id, audience_size=len(recipients),
                note="launch parked: human approval required by policy")
            if idempotency_key:
                self._launch_idempotency[(tenant.tenant_id, idempotency_key)] = result
            return result

        if self._comms is None:
            raise MarketingError("no comms port configured — cannot send")

        campaign = await self._repo.update_campaign(campaign.model_copy(update={
            "status": CampaignStatus.SENDING, "launched_at": _utcnow(),
            "updated_at": _utcnow()}))
        await self._emit(tenant, "marketing.campaign.launched", campaign_id, {
            "campaign_id": campaign_id, "audience_size": len(recipients),
            "step_count": len(send_steps)})

        queued = 0
        for step in sorted(send_steps, key=lambda s: s.position):
            for contact in recipients:
                send = await self._send_step(tenant, campaign, step, contact,
                                             idempotency_key)
                if send.status == SendStatus.SENT:
                    queued += 1

        campaign = await self._repo.update_campaign(campaign.model_copy(update={
            "status": CampaignStatus.SENT, "completed_at": _utcnow(),
            "updated_at": _utcnow()}))
        await self._emit(tenant, "marketing.campaign.send_completed", campaign_id, {
            "campaign_id": campaign_id, "sends_queued": queued,
            "audience_size": len(recipients)})
        result = LaunchResult(
            campaign_id=campaign_id, status=campaign.status,
            audience_size=len(recipients), sends_queued=queued)
        if idempotency_key:
            self._launch_idempotency[(tenant.tenant_id, idempotency_key)] = result
        return result

    async def _send_step(
        self, tenant: TenantContext, campaign: Campaign, step: CampaignStep,
        contact: dict[str, Any], launch_key: str | None,
    ) -> CampaignSend:
        contact_ref = str(contact.get("id") or contact.get("email") or "unknown")
        body_source = ""
        if step.asset_id:
            asset = await self._repo.get_asset(tenant.tenant_id, step.asset_id)
            if asset is None:
                raise AssetNotFoundError(step.asset_id)
            body_source = asset.body
        # Per-contact variables: contact attributes are the template context.
        try:
            body = render_campaign_content(body_source, dict(contact))
        except TemplateRenderError as exc:
            send = await self._repo.add_send(CampaignSend(
                id=_new_id(), tenant_id=tenant.tenant_id, campaign_id=campaign.id,
                step_id=step.id, contact_ref=contact_ref, status=SendStatus.SKIPPED,
                error=f"template render failed: {exc}"))
            return send

        kind = ("email" if step.action == StepAction.EMAIL
                else "sms" if step.action == StepAction.SMS else "chat")
        to_address = contact.get("email") if kind == "email" else contact.get("phone")
        if not to_address:
            return await self._repo.add_send(CampaignSend(
                id=_new_id(), tenant_id=tenant.tenant_id, campaign_id=campaign.id,
                step_id=step.id, contact_ref=contact_ref, status=SendStatus.SKIPPED,
                error=f"contact has no address for {kind}"))

        idem = (f"{launch_key}:{step.id}:{contact_ref}" if launch_key else None)
        try:
            assert self._comms is not None
            message = await self._comms.send_message(
                tenant, channel_kind=kind, to_address=str(to_address), body=body,
                idempotency_key=idem,
                metadata={"campaign_id": campaign.id, "step_id": step.id})
        except Exception as exc:  # policy/rate-limit/provider failures -> logged, not lost
            return await self._repo.add_send(CampaignSend(
                id=_new_id(), tenant_id=tenant.tenant_id, campaign_id=campaign.id,
                step_id=step.id, contact_ref=contact_ref, status=SendStatus.FAILED,
                error=str(exc)))
        return await self._repo.add_send(CampaignSend(
            id=_new_id(), tenant_id=tenant.tenant_id, campaign_id=campaign.id,
            step_id=step.id, contact_ref=contact_ref, message_id=message.id,
            status=SendStatus.SENT, sent_at=_utcnow()))

    async def _enforce_policy(self, tenant: TenantContext, *, action: str,
                              resource: str | None, args: dict[str, Any],
                              risk_context: dict[str, Any]) -> PolicyDecision:
        if self._policy is None:
            raise PolicyDeniedError(("no policy engine configured — refusing bulk send",))
        try:
            decision = await self._policy.evaluate(ActionRequest(
                tenant=tenant, action=action, resource=resource, args=args,
                risk_context=risk_context))
        except Exception as exc:
            raise PolicyDeniedError((f"policy evaluation failed: {exc}",)) from exc
        if decision.effect == PolicyEffect.DENY:
            raise PolicyDeniedError(decision.reasons)
        return decision

    # ---------------------------------------------------------------- analytics
    async def campaign_analytics(
        self, tenant: TenantContext, campaign_id: str
    ) -> CampaignAnalytics:
        campaign = await self.get_campaign(tenant, campaign_id)
        sends = await self._repo.list_sends(tenant.tenant_id, campaign_id)
        audience = (await self.get_audience(tenant, campaign.audience_id)
                    if campaign.audience_id else None)
        by_step: dict[str, dict[str, int]] = {}
        for s in sends:
            bucket = by_step.setdefault(s.step_id, {"sent": 0, "failed": 0, "skipped": 0})
            bucket[s.status.value] = bucket.get(s.status.value, 0) + 1
        return CampaignAnalytics(
            campaign_id=campaign_id, tenant_id=tenant.tenant_id,
            audience_size=audience.member_count if audience else 0,
            sent=sum(1 for s in sends if s.status == SendStatus.SENT),
            failed=sum(1 for s in sends if s.status == SendStatus.FAILED),
            skipped=sum(1 for s in sends if s.status == SendStatus.SKIPPED),
            by_step=by_step)

    async def attribution_report(self, tenant: TenantContext, campaign_id: str) -> dict[str, Any]:
        """Attribution is a DOCUMENTED STUB — it refuses to invent numbers.

        Real attribution needs touchpoint tracking + CRM opportunity linkage,
        which do not exist yet. Returns a 501-style payload instead of fake
        metrics. See README for the build-out plan.
        """
        raise AttributionNotImplementedError(
            "attribution reporting is not implemented; see marketing README § Attribution")

    # ---------------------------------------------------------------- events
    async def _emit(self, tenant: TenantContext, topic: str, aggregate_id: str,
                    payload: dict[str, Any]) -> None:
        if self._events is None:
            return
        await self._events.publish(DomainEvent(
            topic=topic, tenant_id=tenant.tenant_id, aggregate_id=aggregate_id,
            payload=payload, event_id=_new_id(), occurred_at=_utcnow()))
