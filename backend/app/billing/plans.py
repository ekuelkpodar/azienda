"""Plan catalogue as DATA (seeded into the DB by the CLI, never hard-coded logic).

Dimensions (all values JSON; money in USD):
- platform_fee_usd      — flat fee per billing period
- seats_included / seat_price_usd
- credits_included      — agent-execution credits per period (1 credit = $0.01)
- credit_overage_rate_usd — USD charged per credit over the included pool
- agents_included / agent_price_usd
- executions_included   — governed task executions per period (metered, not billed)
- comms_markup_bps      — basis points mark-up on pass-through comms cost
- storage_gb_included

Commercial figures below are ASSUMED seed values for development — real pricing
is a business decision (commercialization research). They are data, editable in
the DB or via a future pricing admin, not code constants the service depends on.
"""
from __future__ import annotations

from typing import Any

DEFAULT_PLANS: list[dict[str, Any]] = [
    {
        "slug": "starter",
        "name": "Starter",
        "description": "Solo operators getting their first agents to work.",
        "dimensions": {
            "platform_fee_usd": 49,
            "seats_included": 3,
            "seat_price_usd": 15,
            "credits_included": 2000,
            "credit_overage_rate_usd": 0.012,
            "agents_included": 2,
            "agent_price_usd": 25,
            "executions_included": 500,
            "comms_markup_bps": 1500,
            "storage_gb_included": 10,
        },
    },
    {
        "slug": "growth",
        "name": "Growth",
        "description": "Teams running agents across departments.",
        "dimensions": {
            "platform_fee_usd": 249,
            "seats_included": 15,
            "seat_price_usd": 12,
            "credits_included": 15000,
            "credit_overage_rate_usd": 0.010,
            "agents_included": 10,
            "agent_price_usd": 20,
            "executions_included": 5000,
            "comms_markup_bps": 1000,
            "storage_gb_included": 100,
        },
    },
    {
        "slug": "scale",
        "name": "Scale",
        "description": "Businesses where agents are the operating layer.",
        "dimensions": {
            "platform_fee_usd": 999,
            "seats_included": 50,
            "seat_price_usd": 10,
            "credits_included": 80000,
            "credit_overage_rate_usd": 0.008,
            "agents_included": 50,
            "agent_price_usd": 15,
            "executions_included": 50000,
            "comms_markup_bps": 500,
            "storage_gb_included": 1000,
        },
    },
    {
        "slug": "enterprise",
        "name": "Enterprise",
        "description": "Regulated tenants: custom terms, dedicated review.",
        "dimensions": {
            "platform_fee_usd": 0,  # negotiated; 0 = custom quote
            "seats_included": 0,
            "seat_price_usd": 0,
            "credits_included": 0,
            "credit_overage_rate_usd": 0,
            "agents_included": 0,
            "agent_price_usd": 0,
            "executions_included": 0,
            "comms_markup_bps": 0,
            "storage_gb_included": 0,
        },
    },
]
