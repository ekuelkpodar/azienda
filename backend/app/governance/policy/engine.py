"""Policy engine: Rego-like rule evaluation in Python.

Design (pattern reused from agent-control-plane's MinimalRuleEngine, reimplemented
for Azienda's contracts):
- Rules are DATA (DB ``policies`` table, JSON), evaluated structurally with
  ``fnmatch`` — never ``eval``/``exec``. Policy inputs are untrusted and treated
  as such: strict validation, size caps, fail-closed on anything malformed.
- Platform guardrails run first and cannot be overridden by tenant policy.
- First matching rule (highest priority) wins; default decision derives from the
  risk band (LOW allow / MEDIUM configurable / HIGH mandatory approval).
- Any evaluation error -> DENY (fail closed).

Rule JSON schema (see governance/README.md for the full reference)::

    {"name": str, "effect": "allow"|"deny"|"require_approval",
     "priority": int, "condition": {...}, "obligations": {...}, "reason": str}

Condition matchers: action (glob or list of globs), resource (glob|null),
min_score/max_score, roles_any, args_equals (subset match), autonomy_level_max.
"""
from __future__ import annotations

import fnmatch
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.contracts import (
    ActionRequest,
    ApprovalStore,
    PolicyDecision,
    PolicyEffect,
    PolicyEngine,
    RiskScorer,
    TenantContext,
)
from app.governance.models import Policy
from app.governance.policy.risk import risk_band

log = logging.getLogger(__name__)

_ACTION_RE = re.compile(r"^[A-Za-z0-9_.:/*?!\-]{1,512}$")
_MAX_ARGS_KEYS = 200
_MAX_ARGS_BYTES = 64 * 1024

EFFECTS = {"allow": PolicyEffect.ALLOW, "deny": PolicyEffect.DENY,
           "require_approval": PolicyEffect.REQUIRE_APPROVAL}


# ------------------------------------------------------------------ validation
def validate_request(request: ActionRequest) -> str | None:
    """Return an error reason if the request is malformed, else None."""
    if not _ACTION_RE.match(request.action or ""):
        return f"malformed action {request.action!r}: must match [A-Za-z0-9_.:/*?!-]<=512"
    if request.resource and len(request.resource) > 1024:
        return "resource exceeds 1024 chars"
    args = request.args or {}
    if not isinstance(args, dict):
        return "args must be a mapping"
    if len(args) > _MAX_ARGS_KEYS:
        return f"args exceeds {_MAX_ARGS_KEYS} keys"
    try:
        if len(json.dumps(args, default=str)) > _MAX_ARGS_BYTES:
            return "args exceed 64KB"
    except (TypeError, ValueError):
        return "args are not JSON-serializable"
    return None


# ------------------------------------------------------------------ rules
@dataclass
class CompiledRule:
    name: str
    effect: PolicyEffect
    priority: int
    condition: dict[str, Any]
    obligations: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    policy_id: str | None = None


def compile_rule(raw: dict[str, Any], policy_id: str | None = None) -> CompiledRule:
    effect = EFFECTS.get(str(raw.get("effect", "")).lower())
    if effect is None:
        raise ValueError(f"rule {raw.get('name')!r}: unknown effect {raw.get('effect')!r}")
    condition = raw.get("condition") or {}
    if not isinstance(condition, dict):
        raise ValueError(f"rule {raw.get('name')!r}: condition must be a mapping")
    return CompiledRule(
        name=str(raw.get("name", "unnamed")),
        effect=effect,
        priority=int(raw.get("priority", 100)),
        condition=condition,
        obligations=dict(raw.get("obligations") or {}),
        reason=str(raw.get("reason", "")),
        policy_id=policy_id)


def _glob_match(pattern: str | list[str] | None, value: str | None) -> bool:
    if pattern is None:
        return True
    if isinstance(pattern, str):
        # Accept comma-separated lists too (human-authored rule JSON).
        patterns = [p.strip() for p in pattern.split(",") if p.strip()]
    else:
        patterns = pattern
    return any(fnmatch.fnmatch(value or "", p) for p in patterns)


def _args_subset_match(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    for k, v in expected.items():
        if k not in actual:
            return False
        av = actual[k]
        if isinstance(v, dict) and isinstance(av, dict):
            if not _args_subset_match(v, av):
                return False
        elif isinstance(v, list):
            if av not in v and not (isinstance(av, list) and set(map(str, av)) <= set(map(str, v))):
                # expected list means "actual value is one of these" OR subset
                if not (isinstance(av, list) and all(str(i) in map(str, v) for i in av)):
                    return False
        elif av != v:
            return False
    return True


def rule_matches(rule: CompiledRule, request: ActionRequest, score: float) -> bool:
    c = rule.condition
    if not _glob_match(c.get("action"), request.action):
        return False
    if not _glob_match(c.get("resource"), request.resource):
        return False
    if score < float(c.get("min_score", 0)) or score > float(c.get("max_score", 100)):
        return False
    roles_any = c.get("roles_any")
    if roles_any and not any(r in request.tenant.roles for r in roles_any):
        return False
    args_equals = c.get("args_equals")
    if args_equals and not _args_subset_match(args_equals, request.args or {}):
        return False
    alm = c.get("autonomy_level_max")
    if alm is not None:
        autonomy = (request.risk_context or {}).get("autonomy_level")
        if not isinstance(autonomy, int) or autonomy > int(alm):
            return False
    return True


# ------------------------------------------------------------------ engine
class RulePolicyEngine(PolicyEngine):
    """Implements ``core.contracts.PolicyEngine`` against DB-backed rules."""

    def __init__(self, db: AsyncSession, settings: Settings,
                 risk_scorer: RiskScorer, approval_store: ApprovalStore) -> None:
        self.db = db
        self.settings = settings
        self.risk_scorer = risk_scorer
        self.approval_store = approval_store

    # -- public -------------------------------------------------------
    async def evaluate(self, request: ActionRequest) -> PolicyDecision:
        return await self._evaluate(request, dry_run=False)

    async def evaluate_dry_run(self, request: ActionRequest) -> PolicyDecision:
        """Same as evaluate but never creates approvals (admin testing)."""
        return await self._evaluate(request, dry_run=True)

    # -- internals ----------------------------------------------------
    async def _evaluate(self, request: ActionRequest, dry_run: bool) -> PolicyDecision:
        try:
            malformed = validate_request(request)
            if malformed:
                return PolicyDecision(PolicyEffect.DENY, None,
                                      reasons=(f"platform.guardrail.malformed: {malformed}",))

            risk = await self.risk_scorer.score(request)
            band = risk_band(risk.score, self.settings)

            # Platform guardrail 1: cross-tenant arg smuggling. Always DENY.
            for key in ("tenant_id", "tenant"):
                smuggled = (request.args or {}).get(key)
                if smuggled and str(smuggled) != request.tenant.tenant_id:
                    return PolicyDecision(
                        PolicyEffect.DENY, None,
                        reasons=(f"platform.guardrail.cross_tenant: arg {key}={smuggled!r} "
                                 f"!= authenticated tenant",))

            # Platform guardrail 2: cost estimate above threshold -> approval.
            cost_est = self._cost_estimate(request)
            if cost_est > self.settings.cost_approval_threshold_usd:
                return await self._require_approval(
                    request, None, risk.score, risk.factors,
                    (f"platform.guardrail.cost: estimated ${cost_est:.2f} exceeds "
                     f"approval threshold ${self.settings.cost_approval_threshold_usd:.2f}",),
                    {}, dry_run)

            # Tenant rules, highest priority first.
            for rule in await self._load_rules(request.tenant):
                if rule_matches(rule, request, risk.score):
                    return await self._apply_rule(request, rule, risk.score,
                                                  risk.factors, dry_run)

            # Default: risk-band decision.
            return await self._default_decision(request, band, risk.score,
                                                risk.factors, dry_run)
        except Exception as e:  # noqa: BLE001 — fail closed on ANY evaluation error
            log.exception("policy evaluation failed; denying",
                          extra={"action": getattr(request, "action", "?")})
            return PolicyDecision(PolicyEffect.DENY, None,
                                  reasons=(f"platform.guardrail.eval_error: {type(e).__name__}",))

    async def _apply_rule(self, request: ActionRequest, rule: CompiledRule,
                          score: float, factors: tuple[str, ...],
                          dry_run: bool) -> PolicyDecision:
        reasons: tuple[str, ...] = (f"rule:{rule.name} (priority {rule.priority})",)
        if rule.reason:
            reasons += (rule.reason,)
        reasons += tuple(f"risk:{f}" for f in factors)
        if rule.effect == PolicyEffect.REQUIRE_APPROVAL:
            return await self._require_approval(request, rule.policy_id, score,
                                                factors, reasons, rule.obligations,
                                                dry_run)
        return PolicyDecision(rule.effect, rule.policy_id, reasons=reasons,
                              obligations=rule.obligations)

    async def _default_decision(self, request: ActionRequest, band: str, score: float,
                                factors: tuple[str, ...], dry_run: bool) -> PolicyDecision:
        reasons = (f"default:risk-band:{band} (score {score:.1f})",) + \
                  tuple(f"risk:{f}" for f in factors)
        if band == "low":
            return PolicyDecision(PolicyEffect.ALLOW, None, reasons=reasons)
        if band == "medium" and not self.settings.approval_medium_require:
            return PolicyDecision(PolicyEffect.ALLOW, None, reasons=reasons,
                                  obligations={"audit_level": "elevated"})
        return await self._require_approval(request, None, score, factors, reasons,
                                            {}, dry_run)

    async def _require_approval(self, request: ActionRequest, policy_id: str | None,
                                score: float, factors: tuple[str, ...],
                                reasons: tuple[str, ...], obligations: dict[str, Any],
                                dry_run: bool) -> PolicyDecision:
        decision = PolicyDecision(PolicyEffect.REQUIRE_APPROVAL, policy_id,
                                  reasons=reasons, obligations=obligations)
        if dry_run:
            return decision
        approval = await self.approval_store.request(decision, request,
                                                     score, factors)
        return PolicyDecision(PolicyEffect.REQUIRE_APPROVAL, policy_id,
                              reasons=reasons, approval_id=approval.approval_id,
                              obligations=obligations)

    async def _load_rules(self, tenant: TenantContext) -> list[CompiledRule]:
        rows = (await self.db.execute(
            select(Policy).where(Policy.tenant_id == tenant.tenant_id,
                                 Policy.is_active.is_(True))
            .order_by(Policy.priority.desc()))).scalars().all()
        compiled: list[CompiledRule] = []
        for policy in rows:
            for raw in policy.rules or []:
                try:
                    compiled.append(compile_rule(raw, policy_id=policy.id))
                except (ValueError, TypeError, AttributeError) as e:
                    # A malformed tenant rule must not break evaluation; skip loudly.
                    log.warning("skipping malformed policy rule",
                                extra={"policy_id": policy.id, "error": str(e)})
        compiled.sort(key=lambda r: r.priority, reverse=True)
        return compiled

    def _cost_estimate(self, request: ActionRequest) -> float:
        ctx = request.risk_context or {}
        for key in ("estimated_cost_usd", "cost_estimate_usd", "estimated_cost"):
            val = ctx.get(key, (request.args or {}).get(key))
            try:
                if val is not None:
                    return max(0.0, float(val))
            except (TypeError, ValueError):
                continue
        return 0.0


# ------------------------------------------------------------------ seed data
DEFAULT_TENANT_RULES: list[dict[str, Any]] = [
    {
        "name": "deny-permission-changes",
        "effect": "deny",
        "priority": 1000,
        "condition": {"action": "*permission*"},
        "reason": "Permission changes are never auto-executable (orchestrator policy).",
    },
    {
        "name": "deny-ad-spend-over-500",
        "effect": "deny",
        "priority": 990,
        "condition": {"action": "*ad*spend*", "min_score": 0},
        "reason": "Ad spend above $500 is never permitted without a dedicated workflow.",
    },
    {
        "name": "approve-bulk-sends",
        "effect": "require_approval",
        "priority": 500,
        "condition": {"action": "*bulk*send*"},
        "reason": "Bulk sends are externally visible and hard to recall.",
    },
    {
        "name": "approve-deletes",
        "effect": "require_approval",
        "priority": 500,
        "condition": {"action": "*delete*"},
        "reason": "Deletes are irreversible.",
    },
    {
        "name": "allow-reads",
        "effect": "allow",
        "priority": 10,
        "condition": {"action": ["*read*", "*list*", "*get*", "*search*"]},
        "reason": "Read-only actions are low risk.",
    },
]
