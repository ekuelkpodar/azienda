"""Transparent rule-based lead scoring.

Every point is explainable: score_lead returns the total plus a breakdown of
which rules fired and why. Weights are ASSUMED defaults (tuned judgment, not
measured data) and live here as data, not magic constants scattered in code.
Future: fit weights from closed-won history per tenant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

# Free-mail providers do not signal a business buyer. (ASSUMED list.)
FREE_EMAIL_DOMAINS = frozenset(
    {
        "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com",
        "icloud.com", "proton.me", "protonmail.com", "gmx.com", "mail.com",
        "yandex.com", "zoho.com",
    }
)

SENIOR_TITLE_KEYWORDS = (
    "chief", "cfo", "ceo", "cto", "coo", "cmo", "cio",
    "vp", "vice president", "president",
    "director", "head of", "founder", "co-founder", "owner", "partner",
)

JUNIOR_TITLE_KEYWORDS = ("intern", "student", "assistant")

# Weights: rule name -> points. Documented, transparent, adjustable.
WEIGHTS: dict[str, int] = {
    "corporate_email": 25,
    "senior_title": 15,
    "org_size_enterprise": 10,
    "org_size_mid": 6,
    "org_size_smb": 3,
    "source_referral_partner": 10,
    "source_inbound_event": 6,
    "source_outbound": 3,
    "source_purchased_list": -10,
    "has_phone": 5,
    "recent_activity_7d": 8,
    "org_industry_known": 5,
    "junior_title": -15,
}

REFERRAL_SOURCES = {"referral", "partner"}
INBOUND_SOURCES = {"inbound", "website", "event", "webinar", "content"}


@dataclass
class ScoreRule:
    rule: str
    points: int
    detail: str


@dataclass
class ScoreResult:
    score: int  # 0..100
    breakdown: list[ScoreRule] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "score": self.score,
            "breakdown": [
                {"rule": r.rule, "points": r.points, "detail": r.detail}
                for r in self.breakdown
            ],
            "weights": WEIGHTS,
        }


@dataclass
class LeadFacts:
    """Everything the scorer needs — the caller assembles this from CRM rows."""

    email: str | None = None
    phone: str | None = None
    title: str | None = None
    source: str | None = None
    org_size_band: str | None = None
    org_industry: str | None = None
    last_activity_at: datetime | None = None


def score_lead(facts: LeadFacts, *, now: datetime | None = None) -> ScoreResult:
    """Pure function: facts in, (score, explanation) out. No I/O."""
    now = now or datetime.now(UTC)
    rules: list[ScoreRule] = []
    total = 0

    def add(rule: str, detail: str) -> None:
        nonlocal total
        pts = WEIGHTS[rule]
        total += pts
        rules.append(ScoreRule(rule=rule, points=pts, detail=detail))

    if facts.email and "@" in facts.email:
        domain = facts.email.split("@", 1)[1].lower()
        if domain not in FREE_EMAIL_DOMAINS:
            add("corporate_email", f"business domain '{domain}' (+{WEIGHTS['corporate_email']})")

    title = (facts.title or "").lower()
    if any(k in title for k in SENIOR_TITLE_KEYWORDS):
        add("senior_title", f"senior title '{facts.title}' (+{WEIGHTS['senior_title']})")
    if any(k in title for k in JUNIOR_TITLE_KEYWORDS):
        add("junior_title", f"junior title '{facts.title}' ({WEIGHTS['junior_title']})")

    band = (facts.org_size_band or "").lower()
    if band == "enterprise":
        add("org_size_enterprise", "enterprise account (+10)")
    elif band == "mid":
        add("org_size_mid", "mid-market account (+6)")
    elif band == "smb":
        add("org_size_smb", "smb account (+3)")

    source = (facts.source or "").lower()
    if source in REFERRAL_SOURCES:
        add("source_referral_partner", f"high-trust source '{facts.source}' (+10)")
    elif source in INBOUND_SOURCES:
        add("source_inbound_event", f"inbound source '{facts.source}' (+6)")
    elif source == "outbound":
        add("source_outbound", "outbound prospecting (+3)")
    elif source == "purchased_list":
        add("source_purchased_list", "purchased list (-10)")

    if facts.phone:
        add("has_phone", "direct phone on file (+5)")

    # SQLite returns tz-naive datetimes; the DB convention is UTC (DATABASE.md),
    # so a naive timestamp is interpreted as UTC rather than crashing the
    # comparison against the tz-aware `now`.
    last_activity = facts.last_activity_at
    if last_activity is not None and last_activity.tzinfo is None:
        last_activity = last_activity.replace(tzinfo=UTC)
    if last_activity and (now - last_activity) <= timedelta(days=7):
        add("recent_activity_7d", "engaged in the last 7 days (+8)")

    if facts.org_industry:
        add("org_industry_known", f"industry known ({facts.org_industry}) (+5)")

    return ScoreResult(score=max(0, min(100, total)), breakdown=rules)
