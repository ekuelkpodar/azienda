"""Risk scoring: deterministic, explainable, bounded 0-100.

Bands (settings-driven): score < risk_low_max -> LOW, >= risk_high_min -> HIGH,
else MEDIUM. Every factor is recorded so a decision can be explained and audited.
No ML, no hidden weights — a human can re-derive any score by hand.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.contracts import ActionRequest, RiskScore
from app.governance.models import RiskScoreRecord

# (keyword, points, factor label, reversible?)
_DESTRUCTIVE = [
    ("delete", 35, "destructive:delete", False),
    ("destroy", 35, "destructive:destroy", False),
    ("drop", 35, "destructive:drop", False),
    ("terminate", 30, "destructive:terminate", False),
    ("revoke", 25, "destructive:revoke", False),
]
_BULK = [
    ("bulk", 20, "bulk-operation", True),
    ("mass", 20, "bulk-operation", True),
    ("all_", 15, "broad-scope", True),
]
_EXTERNAL = [
    ("send", 15, "externally-visible:send", True),
    ("publish", 15, "externally-visible:publish", True),
    ("post", 10, "externally-visible:post", True),
    ("email", 12, "externally-visible:email", True),
    ("sms", 12, "externally-visible:sms", True),
]
_FINANCIAL = [
    ("pay", 25, "financial", True),
    ("refund", 30, "financial:refund", False),
    ("invoice", 20, "financial:invoice", True),
    ("transfer", 30, "financial:transfer", False),
    ("payout", 30, "financial:payout", False),
    ("charge", 25, "financial:charge", True),
    ("billing", 15, "financial:billing", True),
]
_DATA = [
    ("export", 15, "data:export", True),
    ("pii", 15, "data:pii", True),
    ("permission", 40, "security:permission-change", False),
    ("role", 25, "security:role-change", True),
    ("secret", 30, "security:secret-access", True),
]


def risk_band(score: float, settings: Settings) -> str:
    if score >= settings.risk_high_min:
        return "high"
    if score >= settings.risk_low_max:
        return "medium"
    return "low"


@dataclass
class _Accumulator:
    score: float = 0.0
    factors: list[str] = field(default_factory=list)
    reversible: bool = True
    financial_impact_usd: Decimal = Decimal("0")

    def add(self, points: float, factor: str, reversible: bool = True) -> None:
        self.score += points
        self.factors.append(factor)
        if not reversible:
            self.reversible = False


def _score_action_text(acc: _Accumulator, action: str, resource: str | None) -> None:
    hay = f"{action} {resource or ''}".lower()
    for table in (_DESTRUCTIVE, _BULK, _EXTERNAL, _FINANCIAL, _DATA):
        for keyword, points, factor, reversible in table:
            if keyword in hay:
                acc.add(points, factor, reversible)


def _score_financials(acc: _Accumulator, request: ActionRequest) -> None:
    args = request.args or {}
    amount = args.get("amount_usd", args.get("amount"))
    try:
        amt = Decimal(str(amount)) if amount is not None else Decimal("0")
    except Exception:  # noqa: BLE001 — untrusted input; treat as zero, don't crash
        amt = Decimal("0")
    if amt > 0:
        acc.financial_impact_usd = amt
        if amt >= 1000:
            acc.add(20, f"financial-impact:>=1000usd ({amt})", reversible=False)
        elif amt >= 100:
            acc.add(10, f"financial-impact:>=100usd ({amt})")


def _score_context(acc: _Accumulator, request: ActionRequest) -> None:
    ctx = request.risk_context or {}
    if ctx.get("data_sensitivity") in ("pii", "phi", "financial", "secret"):
        acc.add(15, f"data-sensitivity:{ctx['data_sensitivity']}")
    autonomy = ctx.get("autonomy_level")
    if isinstance(autonomy, int) and autonomy >= 4:
        acc.add(10, f"high-autonomy:L{autonomy}")
    if ctx.get("reversible") is False:
        acc.reversible = False
        acc.add(10, "explicitly-irreversible")


class RiskScorerImpl:
    """Implements ``core.contracts.RiskScorer``. Persists each score for audit."""

    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    async def score(self, request: ActionRequest) -> RiskScore:
        acc = _Accumulator()
        _score_action_text(acc, request.action, request.resource)
        _score_financials(acc, request)
        _score_context(acc, request)
        final = max(0.0, min(100.0, acc.score))
        risk = RiskScore(score=final, factors=tuple(acc.factors or []),
                         reversible=acc.reversible,
                         financial_impact_usd=acc.financial_impact_usd)
        self.db.add(RiskScoreRecord(
            tenant_id=request.tenant.tenant_id,
            action_ref=f"{request.action}:{request.resource or '-'}",
            score=Decimal(str(round(final, 2))),
            factors=list(risk.factors),
            reversible=risk.reversible,
            financial_impact_usd=risk.financial_impact_usd))
        await self.db.flush()
        return risk

    def band(self, score: float) -> str:
        return risk_band(score, self.settings)
