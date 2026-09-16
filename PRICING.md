# PRICING.md — Azienda commercial pricing

> Derived from `docs/research/commercialization.md` §3 (2026-09-15 research;
> **do not edit the research conclusions** — this page is the go-to-market
> expression of them). Evidence labels follow the research doc.

## Model: platform fee + included credits + overages + pass-through comms

The vendor's real cost driver is *agent execution*, not seats — so price tracks
it. Pure seat pricing disconnects price from cost (HubSpot's problem); pure
usage pricing terrifies SMB buyers (Twilio's problem). The hybrid gives buyers a
predictable base and the vendor a cost-tracking meter.

**Pricing dimensions** (stored as data in `billing/`, never hardcoded —
AGENTS.md §3):

- `platform_fee` — base subscription per tier, per month
- `seats` — per-seat fee beyond included seats
- `agent_entitlements` — active agents / workflows included per tier
- `execution_credits` — included metered units per month
- `credit_overage_rate` — price per credit beyond included
- `model_usage` — sub-meter blended into credits (frontier models draw more)
- `communications` — SMS/voice/email at provider cost + 10–20% handling
  (**ASSUMED** markup), itemized separately, never bundled
- `storage`, `integrations` (premium connectors as add-ons), `support_tier`

**Credit definition (ASSUMED, must be instrumented):** 1 execution credit = one
normalized agent task unit. Routine task on a cheap model ≈ 1 credit;
reasoning-heavy task on a frontier model ≈ 3–5 credits; each retry/loop draws
more. The bill tracks the vendor's cost curve.

## Tiers (USD, from research §3.2)

| | Starter | Professional | Scale | Enterprise |
|---|---|---|---|---|
| Platform fee | $49/mo | $149/mo | $399/mo | Custom |
| Included seats | 3 | 10 | 25 | Unlimited/custom |
| Extra seat | $9/mo | $12/mo | $15/mo | Custom |
| Execution credits included | 500/mo | 2,500/mo | 10,000/mo | Custom pool |
| Credit overage | $0.15 | $0.12 | $0.10 | Custom |
| Active agents | 3 | 10 | 30 | Unlimited |
| Human-approval workflows | — | ✓ | ✓ | ✓ |
| Governance rail (OPA policies, audit export) | — | Basic | Full | Full + custom |
| Premium integrations | — | 2 included | 5 included | Unlimited |
| Support | Community + docs | Email, 1 business day | Priority + onboarding | SLA + CSM |
| Communications | Pass-through | Pass-through | Pass-through | Pass-through |

## Design notes

- **No free tier at launch (ASSUMED decision):** a free tier for an agent
  product is an unbounded-cost attack surface. 14-day trial with a capped
  credit pool instead.
- Annual prepay: 2 months free.
- Overage requires an explicit customer spend cap / budget — the kill switch in
  `governance/budgets/` is the enforcement point.
- Unit-economics anchors (CONFIRMED provider pricing, observed 2026-09-15):
  Anthropic Sonnet 5 $3/$15 per 1M tokens; OpenAI GPT-5.6 Sol $4/$20
  (promotional, Reuters 2026-08-21). Multi-step agentic workflows ≈ $0.75+ on
  Sonnet-class pricing (Vista Equity, May 2026). The credit meter must be tuned
  so margin holds at these costs.

## Status

Pricing is **research-complete, implementation-pending** (ROADMAP.md #14):
`billing/` has plans/subscriptions/usage models but no provider integration and
no proration (CONFIRMED gaps).
