"""Repository protocols + in-memory implementations for marketing.

Persistence seam: ``MarketingRepository``. ``InMemoryMarketingRepository`` backs
tests and local dev; Postgres SQLAlchemy implementation (``models.py``) is
future work pending ``core/db``.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from .schemas import Audience, Campaign, CampaignSend, CampaignStep, ContentAsset


@runtime_checkable
class MarketingRepository(Protocol):
    # campaigns
    async def add_campaign(self, campaign: Campaign) -> Campaign: ...
    async def get_campaign(self, tenant_id: str, campaign_id: str) -> Campaign | None: ...
    async def update_campaign(self, campaign: Campaign) -> Campaign: ...
    async def list_campaigns(self, tenant_id: str, limit: int = 50,
                             offset: int = 0) -> list[Campaign]: ...
    # steps
    async def add_step(self, step: CampaignStep) -> CampaignStep: ...
    async def get_step(self, tenant_id: str, step_id: str) -> CampaignStep | None: ...
    async def update_step(self, step: CampaignStep) -> CampaignStep: ...
    async def delete_step(self, tenant_id: str, step_id: str) -> None: ...
    async def list_steps(self, tenant_id: str, campaign_id: str) -> list[CampaignStep]: ...
    # audiences
    async def add_audience(self, audience: Audience) -> Audience: ...
    async def get_audience(self, tenant_id: str, audience_id: str) -> Audience | None: ...
    async def update_audience(self, audience: Audience) -> Audience: ...
    async def list_audiences(self, tenant_id: str) -> list[Audience]: ...
    # assets
    async def add_asset(self, asset: ContentAsset) -> ContentAsset: ...
    async def get_asset(self, tenant_id: str, asset_id: str) -> ContentAsset | None: ...
    async def update_asset(self, asset: ContentAsset) -> ContentAsset: ...
    async def list_assets(self, tenant_id: str, kind: str | None = None) -> list[ContentAsset]: ...
    # sends
    async def add_send(self, send: CampaignSend) -> CampaignSend: ...
    async def update_send(self, send: CampaignSend) -> CampaignSend: ...
    async def list_sends(self, tenant_id: str, campaign_id: str) -> list[CampaignSend]: ...


class InMemoryMarketingRepository:
    """Process-local repository. Tenant isolation enforced on every access."""

    def __init__(self) -> None:
        self._campaigns: dict[tuple[str, str], Campaign] = {}
        self._steps: dict[tuple[str, str], CampaignStep] = {}
        self._audiences: dict[tuple[str, str], Audience] = {}
        self._assets: dict[tuple[str, str], ContentAsset] = {}
        self._sends: dict[tuple[str, str], CampaignSend] = {}

    async def add_campaign(self, campaign: Campaign) -> Campaign:
        self._campaigns[(campaign.tenant_id, campaign.id)] = campaign
        return campaign

    async def get_campaign(self, tenant_id: str, campaign_id: str) -> Campaign | None:
        return self._campaigns.get((tenant_id, campaign_id))

    async def update_campaign(self, campaign: Campaign) -> Campaign:
        self._campaigns[(campaign.tenant_id, campaign.id)] = campaign
        return campaign

    async def list_campaigns(self, tenant_id: str, limit: int = 50,
                             offset: int = 0) -> list[Campaign]:
        items = [c for (t, _), c in self._campaigns.items() if t == tenant_id]
        items.sort(key=lambda c: c.created_at)
        return items[offset:offset + limit]

    async def add_step(self, step: CampaignStep) -> CampaignStep:
        self._steps[(step.tenant_id, step.id)] = step
        return step

    async def get_step(self, tenant_id: str, step_id: str) -> CampaignStep | None:
        return self._steps.get((tenant_id, step_id))

    async def update_step(self, step: CampaignStep) -> CampaignStep:
        self._steps[(step.tenant_id, step.id)] = step
        return step

    async def delete_step(self, tenant_id: str, step_id: str) -> None:
        self._steps.pop((tenant_id, step_id), None)

    async def list_steps(self, tenant_id: str, campaign_id: str) -> list[CampaignStep]:
        items = [s for (t, _), s in self._steps.items()
                 if t == tenant_id and s.campaign_id == campaign_id]
        items.sort(key=lambda s: s.position)
        return items

    async def add_audience(self, audience: Audience) -> Audience:
        self._audiences[(audience.tenant_id, audience.id)] = audience
        return audience

    async def get_audience(self, tenant_id: str, audience_id: str) -> Audience | None:
        return self._audiences.get((tenant_id, audience_id))

    async def update_audience(self, audience: Audience) -> Audience:
        self._audiences[(audience.tenant_id, audience.id)] = audience
        return audience

    async def list_audiences(self, tenant_id: str) -> list[Audience]:
        return [a for (t, _), a in self._audiences.items() if t == tenant_id]

    async def add_asset(self, asset: ContentAsset) -> ContentAsset:
        self._assets[(asset.tenant_id, asset.id)] = asset
        return asset

    async def get_asset(self, tenant_id: str, asset_id: str) -> ContentAsset | None:
        return self._assets.get((tenant_id, asset_id))

    async def update_asset(self, asset: ContentAsset) -> ContentAsset:
        self._assets[(asset.tenant_id, asset.id)] = asset
        return asset

    async def list_assets(self, tenant_id: str, kind: str | None = None) -> list[ContentAsset]:
        return [a for (t, _), a in self._assets.items()
                if t == tenant_id and (kind is None or a.kind == kind)]

    async def add_send(self, send: CampaignSend) -> CampaignSend:
        self._sends[(send.tenant_id, send.id)] = send
        return send

    async def update_send(self, send: CampaignSend) -> CampaignSend:
        self._sends[(send.tenant_id, send.id)] = send
        return send

    async def list_sends(self, tenant_id: str, campaign_id: str) -> list[CampaignSend]:
        return [s for (t, _), s in self._sends.items()
                if t == tenant_id and s.campaign_id == campaign_id]
