"""crm package public interface."""

from app.core.models import Base as CRMBase  # shared metadata (alembic/env.py)

from .scoring import WEIGHTS, LeadFacts, ScoreResult, score_lead
from .service import (
    CRMDuplicate,
    CRMError,
    CRMNotFound,
    CRMService,
    CRMValidationError,
    PolicyDeniedError,
)

__all__ = [
    "CRMBase",
    "CRMError",
    "CRMDuplicate",
    "CRMNotFound",
    "CRMService",
    "CRMValidationError",
    "PolicyDeniedError",
    "WEIGHTS",
    "LeadFacts",
    "ScoreResult",
    "score_lead",
]
