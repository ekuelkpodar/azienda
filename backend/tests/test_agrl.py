"""Tests: AGRL — one append-only hash-chained ledger, fold-derived projections."""
from __future__ import annotations

import pytest

from app.memory.agrl.ledger import AGRLLedger, EventTypes
from app.memory.agrl.projections import project


async def _write_story(agrl, tenant):
    """A goal → constraint → resource → plan → outcome story, all in one ledger."""
    await agrl.append(tenant, EventTypes.GOAL_CREATED, "goal-1",
                      {"title": "Reduce churn 5%", "priority": 0.9}, "ekue")
    await agrl.append(tenant, EventTypes.CONSTRAINT_ADDED, "goal-1",
                      {"goal_id": "goal-1",
                       "constraint": "no price changes without approval"}, "ekue")
    await agrl.append(tenant, EventTypes.RESOURCE_REGISTERED, "res-1",
                      {"resource_type": "budget", "resource_ref": "q3-growth",
                       "amount": "5000", "unit": "USD"}, "system")
    await agrl.append(tenant, EventTypes.RESOURCE_ALLOCATED, "res-1",
                      {"goal_id": "goal-1", "amount": "1200"}, "system")
    await agrl.append(tenant, EventTypes.PLAN_PROPOSED, "goal-1",
                      {"plan_id": "plan-1",
                       "steps": [{"step_id": "s-1", "tool": "knowledge.search"}]},
                      "orchestrator")
    await agrl.append(tenant, EventTypes.OUTCOME_RECORDED, "goal-1",
                      {"status": "completed", "plan_id": "plan-1",
                       "cost_usd": "0.42", "retries": 0,
                       "human_interventions": 0, "policy_denies": 0},
                      "orchestrator")
    return "goal-1"


async def test_agrl_append_is_hash_chained(svc, tenant):
    agrl: AGRLLedger = svc["agrl"]
    e1 = await agrl.append(tenant, EventTypes.GOAL_CREATED, "goal-1",
                           {"title": "t"}, "ekue")
    e2 = await agrl.append(tenant, EventTypes.GOAL_UPDATED, "goal-1",
                           {"priority": 0.7}, "ekue")
    assert e1.seq == 1 and e2.seq == 2
    assert e2.prev_hash == e1.hash
    assert e1.hash != e2.hash
    assert await agrl.verify_chain(tenant) is True


async def test_agrl_chain_detects_tampering(svc, tenant):
    agrl: AGRLLedger = svc["agrl"]
    await agrl.append(tenant, EventTypes.GOAL_CREATED, "goal-1", {"title": "t"}, "a")
    await agrl.append(tenant, EventTypes.GOAL_UPDATED, "goal-1", {"x": 1}, "a")
    chain = agrl._events[tenant.tenant_id]
    chain[0] = type(chain[0])(**{**chain[0].__dict__,
                                 "payload": {"title": "TAMPERED"}})
    assert await agrl.verify_chain(tenant) is False


async def test_agrl_goals_projection_is_fold_derived(svc, tenant):
    agrl: AGRLLedger = svc["agrl"]
    await _write_story(agrl, tenant)
    events = await agrl.read_all(tenant)
    proj = project("goals", "*", events)
    assert len(proj["goals"]) == 1
    g = proj["goals"][0]
    assert g["goal_id"] == "goal-1"
    assert g["title"] == "Reduce churn 5%"
    assert g["status"] == "active"
    assert g["constraints"] == ["no price changes without approval"]


async def test_agrl_resources_and_constraints_projections(svc, tenant):
    agrl: AGRLLedger = svc["agrl"]
    await _write_story(agrl, tenant)
    events = await agrl.read_all(tenant)
    res = project("resources", "*", events)
    assert len(res["resources"]) == 1
    r = res["resources"][0]
    assert r["resource_type"] == "budget"
    assert r["allocated"] == 1200.0
    assert r["available"] == 3800.0
    cons = project("constraints", "*", events)
    assert [c["constraint"] for c in cons["constraints"]] == \
        ["no price changes without approval"]


async def test_agrl_plan_and_outcome_projections(svc, tenant):
    agrl: AGRLLedger = svc["agrl"]
    await _write_story(agrl, tenant)
    events = await agrl.read_all(tenant)
    plan = project("plan", "plan-1", events)
    assert plan["plan"]["plan_id"] == "plan-1"
    assert plan["approved"] is False
    outcomes = project("outcome_summary", "*", events)
    assert outcomes["count"] == 1
    assert outcomes["by_status"] == {"completed": 1}
    assert outcomes["total_cost_usd"] == 0.42


async def test_agrl_what_next_projection(svc, tenant):
    agrl: AGRLLedger = svc["agrl"]
    await _write_story(agrl, tenant)
    events = await agrl.read_all(tenant)
    nxt = project("what_next", "*", events)
    assert nxt["action"] == "advance_goal"
    assert nxt["goal_id"] == "goal-1"
    assert nxt["active_goal_count"] == 1


async def test_agrl_get_projection_contract(svc, tenant):
    """contracts.AGRLLedger.get_projection works for every projection name."""
    agrl: AGRLLedger = svc["agrl"]
    await _write_story(agrl, tenant)
    for name, agg in (("goal", "goal-1"), ("goals", "*"), ("resource", "res-1"),
                      ("resources", "*"), ("constraints", "*"),
                      ("plan", "plan-1"), ("outcome_summary", "*"),
                      ("what_next", "*")):
        proj = await agrl.get_projection(tenant, name, agg)
        assert isinstance(proj, dict), name
    with pytest.raises(ValueError):
        await agrl.get_projection(tenant, "nope", "*")


async def test_agrl_tenant_isolation(svc, tenant, tenant_b):
    agrl: AGRLLedger = svc["agrl"]
    await _write_story(agrl, tenant)
    assert await agrl.read(tenant_b, "goal-1") == []
    assert await agrl.verify_chain(tenant_b) is True  # empty chain verifies
    proj = await agrl.get_projection(tenant_b, "goals", "*")
    assert proj["goals"] == []


async def test_agrl_goal_lifecycle_events(svc, tenant):
    agrl: AGRLLedger = svc["agrl"]
    await agrl.append(tenant, EventTypes.GOAL_CREATED, "goal-9",
                      {"title": "Launch", "priority": 0.5}, "ekue")
    await agrl.append(tenant, EventTypes.GOAL_SUSPENDED, "goal-9",
                      {"reason": "waiting on funds"}, "ekue")
    await agrl.append(tenant, EventTypes.GOAL_REPRIORITIZED, "goal-9",
                      {"priority": 0.95}, "ekue")
    await agrl.append(tenant, EventTypes.GOAL_COMPLETED, "goal-9", {}, "ekue")
    proj = project("goals", "*", await agrl.read_all(tenant))
    (g,) = proj["goals"]
    assert g["status"] == "completed"
    assert g["priority"] == 0.95


async def test_agrl_single_ledger_for_goals_and_resources(svc, tenant):
    """AGRL is ONE ledger: goal and resource events share the same chain."""
    agrl: AGRLLedger = svc["agrl"]
    await _write_story(agrl, tenant)
    chain = agrl._events[tenant.tenant_id]
    types = {e.event_type for e in chain}
    assert "goal.created" in types and "resource.registered" in types
    seqs = [e.seq for e in chain]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    assert await agrl.verify_chain(tenant) is True
