"""Package: marketing — campaign orchestration and measurement.

Public interface (other packages/agents import ONLY these):
  - ``MarketingService`` — campaign/step/audience/asset CRUD, governed launch,
    analytics rollups.
  - ``MarketingRepository`` / ``InMemoryMarketingRepository`` — persistence seam.
  - ``evaluate_filter`` / ``segment_contacts`` — pure segmentation engine.
  - ``render_campaign_content`` — Jinja-sandboxed content rendering.
  - schemas (``Campaign``, ``Audience``, ``ContentAsset``, ...).

See README.md for the boundary contract.
"""
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
from .segmentation import SegmentFilterError, evaluate_filter, segment_contacts, validate_filter
from .service import (
    AssetNotFoundError,
    AttributionNotImplementedError,
    AudienceNotFoundError,
    CampaignNotFoundError,
    CampaignStateError,
    CommsSenderPort,
    MarketingError,
    MarketingService,
    PolicyDeniedError,
    StepNotFoundError,
)
from .templates import TemplateRenderError, render_campaign_content

__all__ = [
    "AssetNotFoundError",
    "AssetStatus",
    "AttributionNotImplementedError",
    "Audience",
    "AudienceCreate",
    "AudienceNotFoundError",
    "AudienceUpdate",
    "Campaign",
    "CampaignAnalytics",
    "CampaignCreate",
    "CampaignNotFoundError",
    "CampaignSend",
    "CampaignStateError",
    "CommsSenderPort",
    "CampaignStatus",
    "CampaignStep",
    "CampaignStepCreate",
    "CampaignStepUpdate",
    "CampaignUpdate",
    "ContentAsset",
    "ContentAssetCreate",
    "LaunchResult",
    "MarketingError",
    "MarketingRepository",
    "MarketingService",
    "PolicyDeniedError",
    "SegmentFilterError",
    "SendStatus",
    "StepAction",
    "StepNotFoundError",
    "TemplateRenderError",
    "evaluate_filter",
    "InMemoryMarketingRepository",
    "render_campaign_content",
    "segment_contacts",
    "validate_filter",
]
