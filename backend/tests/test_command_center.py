"""Tests: Command Center — attention aggregation, intent parsing, HTTP layer."""
from __future__ import annotations

import dataclasses

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents.orchestrator import Orchestrator, TaskStatus
from app.api.routers import command_center as cc
from app.api.routers.command_center import AttentionDeps, AttentionService, IntentParser
from app.core import contracts
from app.memory.agrl.ledger import EventTypes


def _deps(svc) -> AttentionDeps:
    return AttentionDeps(
        approvals=svc["approvals"], tasks=svc["tasks"], budgets=svc["budgets"],
        audit=svc["audit"], agrl=svc["agrl"],
        policy_decisions=svc["policy"].decisions)


async def _failed_task(svc, tenant):
    orch: Orchestrator = svc["orchestrator"]
    task = await orch.submit(tenant, title="boom", objective="Find contact Ada",
                             capability="contact.lookup")
    failed = dataclasses.replace(task, status=TaskStatus.FAILED, error="boom")
    await svc["tasks"].save(failed)
    return failed


# ---------------------------------------------------------------------------
# Attention aggregation
# ---------------------------------------------------------------------------
async def test_attention_empty_state(svc, tenant):
    out = await AttentionService(_deps(svc)).attention(tenant)
    assert out["total_items"] == 0
    assert set(out["sections"]) == {"pending_approvals", "failed_tasks",
                                    "blocked_tasks", "overdue_tasks",
                                    "budget_alerts", "anomalies",
                                    "recent_outcomes"}
    assert out["sources"]


async def test_attention_surfaces_failures_with_evidence(svc, tenant):
    failed = await _failed_task(svc, tenant)
    svc["policy"].require_approval_actions.add("tool.comms.send_email")
    orch: Orchestrator = svc["orchestrator"]
    task = await orch.submit(tenant, title="email",
                             objective="Send an email to the team",
                             capability="email.triage")
    await orch.run_task(tenant, task.task_id)  # → WAITING_APPROVAL
    await svc["budgets"].kill_switch(tenant, reason="test")

    out = await AttentionService(_deps(svc)).attention(tenant)
    assert out["total_items"] >= 3
    failed_items = out["sections"]["failed_tasks"]
    assert any(i["links"]["task_id"] == failed.task_id for i in failed_items)
    appr = out["sections"]["pending_approvals"]
    assert appr and all("approval_id" in i["links"] for i in appr)
    assert any(i["kind"] == "budget_alert" and i["severity"] == "critical"
               for i in out["sections"]["budget_alerts"])


async def test_attention_anomaly_on_deny_spike(svc, tenant):
    svc["policy"].decisions.extend(
        [{"action": f"tool.x.{i}", "effect": "deny"} for i in range(4)] +
        [{"action": "tool.y", "effect": "allow"}])
    out = await AttentionService(_deps(svc)).attention(tenant)
    anomalies = out["sections"]["anomalies"]
    assert any("deny rate" in a["title"] for a in anomalies)


async def test_attention_recent_outcomes_from_agrl(svc, tenant):
    await svc["agrl"].append(tenant, EventTypes.OUTCOME_RECORDED, "task-1",
                             {"status": "completed", "cost_usd": "0.10"}, "orchestrator")
    out = await AttentionService(_deps(svc)).attention(tenant)
    outcomes = out["sections"]["recent_outcomes"]
    assert any(i["links"]["task_id"] == "task-1" for i in outcomes)


# ---------------------------------------------------------------------------
# Intent parser (rule-based-mvp)
# ---------------------------------------------------------------------------
def test_intent_parser_routes_known_phrases():
    p = IntentParser()
    assert p.parse("what needs my attention").operation == "get_attention"
    assert p.parse("show pending approvals").operation == "list_approvals"
    pi = p.parse("pause agent crm-agent")
    assert pi.operation == "pause_agent" and pi.params["agent_id"] == "crm-agent"
    pi = p.parse("create task Follow up with Acme")
    assert pi.operation == "create_task" and "acme" in pi.params["title"].lower()
    assert p.parse("what's next").operation == "what_next"
    assert all(p.parse(t).parser_kind == "rule-based-mvp"
               for t in ["what needs my attention", "show pending approvals"])


def test_intent_parser_unknown_is_safe():
    p = IntentParser()
    pi = p.parse("asdfqwer zxcv teleport the moon")
    assert pi.operation == "unknown"
    assert pi.confidence == 0.0
    assert pi.policy_note  # suggests valid phrases; never executes


# ---------------------------------------------------------------------------
# HTTP layer (thin): routes, tenant scoping, intent endpoint
# ---------------------------------------------------------------------------
@pytest.fixture
def client(svc, tenant):
    cc.configure_services(
        attention=AttentionService(_deps(svc)),
        intent_parser=IntentParser(),
        agrl=svc["agrl"], tasks=svc["tasks"], approvals=svc["approvals"],
        cost_ledger=svc["costs"].entries,
        policy_decisions=lambda t: svc["policy"].decisions)
    app = FastAPI()
    app.include_router(cc.router)
    app.dependency_overrides[cc._tenant] = lambda: tenant
    with TestClient(app) as c:
        yield c
    cc.configure_services()  # reset global


async def test_http_attention_endpoint(svc, tenant, client):
    await _failed_task(svc, tenant)
    r = client.get("/command-center/attention")
    assert r.status_code == 200
    body = r.json()
    assert body["total_items"] >= 1
    assert body["sections"]["failed_tasks"]


async def test_http_intent_endpoint_parses_only(client):
    r = client.post("/command-center/intent",
                    json={"text": "show pending approvals"})
    assert r.status_code == 200
    body = r.json()
    assert body["operation"] == "list_approvals"
    assert body["parser"] == "rule-based-mvp"
    assert "governed path" in body["note"]


async def test_http_goals_from_agrl_projection(svc, tenant, client):
    await svc["agrl"].append(tenant, EventTypes.GOAL_CREATED, "goal-7",
                             {"title": "Ship MVP", "priority": 0.8}, "ekue")
    r = client.get("/command-center/goals")
    assert r.status_code == 200
    items = r.json()["items"]
    assert any(g["goal_id"] == "goal-7" for g in items)


async def test_http_summary_counts(svc, tenant, client):
    await _failed_task(svc, tenant)
    await svc["agrl"].append(tenant, EventTypes.GOAL_CREATED, "goal-7",
                             {"title": "Ship MVP"}, "ekue")
    r = client.get("/command-center/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["active_goals"] == 1
    assert body["outcomes_30d"] >= 0


async def test_http_policy_activity(svc, tenant, client):
    svc["policy"].deny_actions.add("tool.comms.send_bulk_sms")
    await svc["executor"].execute(
        tenant, contracts.ToolCall(tool_name="comms.send_email",
                                   arguments={"to": "a@b.c", "subject": "s",
                                              "body": "b"},
                                   grant_id="g-1"))
    r = client.get("/command-center/policy-activity")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    assert "allow" in body["by_effect"]
