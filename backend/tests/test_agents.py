"""Tests: agents registry/planner/router/tools/models/orchestrator/eval/MCP."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.agents.eval import compare_versions, regression_suite
from app.agents.mcp import MCPManager, MCPNotAuthorized, MCPServerStatus
from app.agents.models import ModelRouter, StubModelProvider, make_provider
from app.agents.orchestrator import Orchestrator, TaskStatus
from app.agents.planner import Planner
from app.agents.registry import AgentDefinitionRecord, AgentRegistry, AgentStatus
from app.agents.router import AgentRouter, NoRouteAvailable, RouteRequest
from app.agents.tools import new_grant_id, validate_args
from app.core import contracts
from tests.conftest import build_graph

TENANT = "tenant-acp-1"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
async def test_registry_seeds_ten_agents(svc, tenant):
    agents = await svc["registry"].list(tenant)
    assert len(agents) == 10
    ids = {a.agent_id for a in agents}
    assert {"executive-assistant", "sales-agent", "marketing-agent",
            "support-agent", "operations-agent", "research-agent",
            "scheduling-agent", "finance-assistant", "crm-agent",
            "analytics-agent"} <= ids


async def test_registry_honest_capability_declarations(svc, tenant):
    """Every agent declares what it can AND cannot do (no god-agents)."""
    for rec in await svc["registry"].list(tenant):
        assert rec.capabilities, rec.agent_id
        assert rec.cannot, f"{rec.agent_id} must declare explicit 'cannot'"
        assert rec.allowed_tools, rec.agent_id
        assert 0 <= rec.autonomy_level <= 5


async def test_registry_versioning(svc, tenant):
    reg: AgentRegistry = svc["registry"]
    current = await reg.get_record(tenant, "crm-agent")
    assert current is not None and current.version == 1
    import dataclasses
    updated = await reg.register(
        tenant, dataclasses.replace(current, description="v2", version=0))
    assert updated.version == 2
    latest = await reg.get_record(tenant, "crm-agent")
    assert latest is not None and latest.version == 2
    versions = await reg.versions(tenant, "crm-agent")
    assert len(versions) == 2


async def test_registry_pause_resume_excludes_from_routing(svc, tenant):
    reg: AgentRegistry = svc["registry"]
    await reg.set_status(tenant, "crm-agent", AgentStatus.PAUSED)
    resolved = await reg.resolve(tenant, "contact.lookup")
    assert all(a.agent_id != "crm-agent" for a in resolved)
    await reg.set_status(tenant, "crm-agent", AgentStatus.ACTIVE)
    resolved = await reg.resolve(tenant, "contact.lookup")
    assert any(a.agent_id == "crm-agent" for a in resolved)


async def test_registry_tenant_isolation(svc, tenant, tenant_b):
    mine = await svc["registry"].list(tenant)
    theirs = await svc["registry"].list(tenant_b)
    assert len(mine) == 10 and theirs == []
    assert await svc["registry"].get_record(tenant_b, "crm-agent") is None


# ---------------------------------------------------------------------------
# Planner (rule-based-mvp, deterministic)
# ---------------------------------------------------------------------------
async def test_planner_produces_inspectable_plan(svc, tenant):
    reg: AgentRegistry = svc["registry"]
    agent = await reg.get_record(tenant, "crm-agent")
    planner: Planner = svc["planner"]
    plan = await planner.plan(tenant, task_id="t-1", agent=agent,
                              objective="Find contact Ada Okafor")
    assert plan.steps, "plan must not be empty"
    assert plan.estimated_total_usd >= 0
    assert plan.planner_kind == "rule-based-mvp"
    tools = [s.tool_name for s in plan.steps]
    assert "crm.search_contacts" in tools
    assert planner.validate(plan) == []
    assert "rule-based" in plan.explain().lower()


async def test_planner_unplannable_objective_never_hallucinates_tools(svc, tenant):
    reg: AgentRegistry = svc["registry"]
    agent = await reg.get_record(tenant, "executive-assistant")
    planner: Planner = svc["planner"]
    plan = await planner.plan(tenant, task_id="t-2", agent=agent,
                              objective="Telepathically notify the board")
    assert plan.steps
    assert all(s.tool_name is None for s in plan.steps)
    assert all(s.requires_approval for s in plan.steps)


async def test_planner_respects_agent_tool_allowlist(svc, tenant):
    """No agent gets a tool it isn't authorized for — planner emits a no-tool step."""
    reg: AgentRegistry = svc["registry"]
    agent = await reg.get_record(tenant, "executive-assistant")
    planner: Planner = svc["planner"]
    plan = await planner.plan(tenant, task_id="t-3", agent=agent,
                              objective="Send promotional SMS blast to all contacts")
    assert all(s.tool_name != "comms.send_bulk_sms" for s in plan.steps)
    assert any(s.tool_name is None for s in plan.steps)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
async def test_router_routes_on_capability_not_model_quality(svc, tenant):
    router: AgentRouter = svc["agent_router"]
    agent = await router.route(tenant, RouteRequest(capability="contact.lookup"))
    assert agent.agent_id == "crm-agent"
    ranked = await router.score(tenant, RouteRequest(capability="contact.lookup"))
    assert ranked
    assert ranked[0].breakdown  # multi-factor breakdown present
    assert set(ranked[0].breakdown) >= {"capability", "cost", "latency"}


async def test_router_hard_authorization_gate(svc, tenant):
    reg: AgentRegistry = svc["registry"]
    await reg.set_status(tenant, "research-agent", AgentStatus.PAUSED)
    router: AgentRouter = svc["agent_router"]
    with pytest.raises(NoRouteAvailable):
        await router.route(
            tenant, RouteRequest(capability="document.synthesis",
                                 exclude_agent_ids=("analytics-agent",)))


async def test_router_permission_filter(svc, tenant):
    router: AgentRouter = svc["agent_router"]
    with pytest.raises(NoRouteAvailable):
        await router.route(tenant, RouteRequest(
            capability="contact.lookup",
            required_permissions=("crm:admin-superuser",)))


# ---------------------------------------------------------------------------
# Tool executor: policy-before-execution, DENY path, approvals
# ---------------------------------------------------------------------------
async def _call(svc, tenant, tool_name, arguments, grant=None):
    return await svc["executor"].execute(
        tenant, contracts.ToolCall(
            tool_name=tool_name, arguments=arguments,
            grant_id=grant or new_grant_id(),
            idempotency_key=f"test:{tool_name}"))


async def test_executor_allow_path_executes_handler(svc, tenant):
    res = await _call(svc, tenant, "crm.search_contacts", {"query": "Ada"})
    assert res.ok, res.error
    assert "contacts" in res.output


async def test_executor_deny_path_never_runs_handler(svc, tenant):
    """DENY: policy decision first, handler never invoked, denial audited."""
    svc["policy"].deny_actions.add("tool.comms.send_bulk_sms_test")
    calls: list[dict] = []

    async def spy(t, a):
        calls.append(a)
        return {"sent": 1}

    from app.agents.tools import RegisteredTool
    spec = contracts.ToolSpec(name="comms.send_bulk_sms_test",
                              description="spy", input_schema={"type": "object"},
                              risk_tier="critical", idempotent=True)
    await svc["tool_registry"].register(
        tenant, RegisteredTool(spec=spec, handler=spy, provider="test"))
    res = await _call(svc, tenant, "comms.send_bulk_sms_test", {})
    assert not res.ok
    assert res.error.startswith("policy_denied")
    assert calls == [], "DENIED handler must never run"
    entries = svc["audit"].entries(tenant.tenant_id)
    assert any(e.action == "tool.policy_decision"
               and e.payload.get("effect") == "deny" for e in entries)


async def test_executor_approval_path_returns_approval_id(svc, tenant):
    svc["policy"].require_approval_actions.add("tool.comms.send_email")
    res = await _call(svc, tenant, "comms.send_email",
                      {"to": "a@b.c", "subject": "s", "body": "b"})
    assert not res.ok and res.error == "approval_required"
    assert res.output["approval_id"]
    pending, _ = await svc["approvals"].list_pending(tenant)
    assert any(a.approval_id == res.output["approval_id"] for a in pending)


async def test_executor_missing_grant_rejected(svc, tenant):
    res = await svc["executor"].execute(
        tenant, contracts.ToolCall(tool_name="crm.search_contacts",
                                   arguments={"query": "x"}, grant_id=""))
    assert not res.ok and res.error.startswith("missing_grant")


async def test_executor_schema_validation(svc, tenant):
    errors = validate_args({"type": "object",
                            "required": ["query"],
                            "properties": {"query": {"type": "string"}}},
                           {})
    assert errors
    res = await _call(svc, tenant, "crm.search_contacts", {})
    assert not res.ok and res.error.startswith("schema_validation")


async def test_executor_idempotent_replay(svc, tenant):
    call = contracts.ToolCall(tool_name="tasks.create_task",
                              arguments={"title": "t"}, grant_id=new_grant_id(),
                              idempotency_key="replay-1")
    first = await svc["executor"].execute(tenant, call)
    second = await svc["executor"].execute(tenant, call)
    assert first.ok and second.ok
    assert first.output == second.output


async def test_inmemory_adapters_are_tenant_scoped(svc, tenant, tenant_b):
    """Dev/test adapters must not leak data across tenants.

    Tested at the adapter level: the tool registry itself is tenant-scoped,
    so tenant_b has no tools registered — the leak surface is the shared
    adapter instances, which key state by tenant_id.
    """
    crm = svc["tool_registry"].adapters["crm"]
    await crm.create_contact(tenant, {"first_name": "Zoe", "last_name": "A",
                                      "email": "zoe@example.com"})
    hits_b = await crm.search_contacts(tenant_b, {"query": "Zoe"})
    assert hits_b["count"] == 0
    hits_a = await crm.search_contacts(tenant, {"query": "Zoe"})
    assert hits_a["count"] == 1
    comms = svc["tool_registry"].adapters["comms"]
    await comms.send_email(tenant, {"to": "a@b.c", "subject": "s", "body": "b"})
    assert comms.sent_for(tenant_b) == []
    assert len(comms.sent_for(tenant.tenant_id)) == 1


# ---------------------------------------------------------------------------
# Orchestrator: happy path
# ---------------------------------------------------------------------------
async def test_orchestrator_happy_path_crm_lookup(svc, tenant):
    orch: Orchestrator = svc["orchestrator"]
    task = await orch.submit(tenant, title="Find Ada",
                             objective="Find contact Ada Okafor",
                             capability="contact.lookup")
    outcome = await orch.run_task(tenant, task.task_id)
    assert outcome.status == TaskStatus.COMPLETED, outcome.error
    assert outcome.steps_executed >= 1
    assert outcome.cost_usd >= 0
    # policy decisions recorded on the task
    assert any(p["effect"] == "allow" for p in outcome.policy_decisions)
    # task history is a real transition chain
    transitions = await svc["tasks"].transitions(tenant.tenant_id, task.task_id)
    states = [t.to_status for t in transitions]
    assert TaskStatus.COMPLETED in states
    # audit captured policy decisions
    entries = svc["audit"].entries(tenant.tenant_id)
    assert any(e.action == "plan.policy_decision" for e in entries)
    # AGRL recorded the outcome
    proj = await svc["agrl"].get_projection(tenant, "outcome_summary", "*")
    assert proj["count"] >= 1


# ---------------------------------------------------------------------------
# Orchestrator: DENY path — bulk SMS policy-denied end to end
# ---------------------------------------------------------------------------
async def _register_bulk_sender(svc, tenant):
    return await svc["registry"].register(
        tenant, AgentDefinitionRecord(
            agent_id="bulk-sender", name="Bulk Sender", version=0,
            description="test agent authorized for bulk SMS",
            capabilities=("bulk.messaging",), cannot=("anything else",),
            allowed_tools=("comms.send_bulk_sms",),
            permissions=("comms:send_bulk",), autonomy_level=1))


async def test_orchestrator_deny_path_fails_closed(svc, tenant):
    svc["policy"].deny_actions.add("tool.comms.send_bulk_sms")
    await _register_bulk_sender(svc, tenant)
    orch: Orchestrator = svc["orchestrator"]
    task = await orch.submit(tenant, title="SMS blast",
                             objective="Send promotional SMS blast to all contacts",
                             capability="bulk.messaging")
    outcome = await orch.run_task(tenant, task.task_id)
    assert outcome.status == TaskStatus.FAILED
    assert "policy_denied" in (outcome.error or "")
    # denial recorded on the task's policy trail
    assert any(p.get("effect") == "deny"
               and p.get("action") == "tool.comms.send_bulk_sms"
               for p in outcome.policy_decisions)
    # the handler never ran: comms adapter shows zero sends for this tenant
    comms = svc["tool_registry"].adapters["comms"]
    assert comms.sent_for(tenant.tenant_id) == []
    # denial is audited
    entries = svc["audit"].entries(tenant.tenant_id)
    assert any(e.action == "plan.policy_decision"
               and e.payload.get("effect") == "deny" for e in entries)
    # AGRL recorded a failed outcome (learning substrate sees the denial)
    proj = await svc["agrl"].get_projection(tenant, "outcome_summary", "*")
    assert proj["by_status"].get("failed", 0) >= 1


# ---------------------------------------------------------------------------
# Orchestrator: approval gate — timeout/denial fails closed
# ---------------------------------------------------------------------------
async def test_orchestrator_approval_granted_resumes(svc, tenant):
    svc["policy"].require_approval_actions.add("tool.comms.send_email")
    orch: Orchestrator = svc["orchestrator"]
    task = await orch.submit(tenant, title="Launch email",
                             objective="Send an email to the team about the launch",
                             capability="email.triage")
    outcome = await orch.run_task(tenant, task.task_id)
    assert outcome.status == TaskStatus.WAITING_APPROVAL
    pending, _ = await svc["approvals"].list_pending(tenant)
    assert pending
    for ap in pending:
        await svc["approvals"].decide(tenant, ap.approval_id, approved=True)
    # The (fake) policy engine now honors the granted approval — the real
    # governance package owns this lookup; the fake models it by switching
    # to ALLOW after the human decision.
    svc["policy"].require_approval_actions.discard("tool.comms.send_email")
    resumed = await orch.resume(tenant, task.task_id)
    assert resumed.status == TaskStatus.COMPLETED, resumed.error


async def test_orchestrator_approval_denied_fails_closed(svc, tenant):
    svc["policy"].require_approval_actions.add("tool.comms.send_email")
    orch: Orchestrator = svc["orchestrator"]
    task = await orch.submit(tenant, title="Launch email",
                             objective="Send an email to the team about the launch",
                             capability="email.triage")
    outcome = await orch.run_task(tenant, task.task_id)
    assert outcome.status == TaskStatus.WAITING_APPROVAL
    pending, _ = await svc["approvals"].list_pending(tenant)
    for ap in pending:
        await svc["approvals"].decide(tenant, ap.approval_id, approved=False)
    resumed = await orch.resume(tenant, task.task_id)
    assert resumed.status == TaskStatus.FAILED
    assert "denied" in (resumed.error or "").lower()


async def test_orchestrator_approval_expired_fails_closed(svc, tenant):
    svc["policy"].require_approval_actions.add("tool.comms.send_email")
    orch: Orchestrator = svc["orchestrator"]
    task = await orch.submit(tenant, title="Launch email",
                             objective="Send an email to the team about the launch",
                             capability="email.triage")
    await orch.run_task(tenant, task.task_id)
    pending, _ = await svc["approvals"].list_pending(tenant)
    assert pending
    # expire the approval in place (simulates the timeout path)
    ap = pending[0]
    expired = contracts.Approval(
        approval_id=ap.approval_id, tenant_id=ap.tenant_id, action=ap.action,
        status=contracts.ApprovalStatus.EXPIRED, requested_by=ap.requested_by,
        decided_by=None, expires_at=ap.expires_at)
    svc["approvals"]._approvals[ap.approval_id] = expired
    resumed = await orch.resume(tenant, task.task_id)
    assert resumed.status == TaskStatus.FAILED
    assert "expired" in (resumed.error or "").lower()


# ---------------------------------------------------------------------------
# Orchestrator: budget
# ---------------------------------------------------------------------------
async def test_orchestrator_budget_exhaustion_blocks(svc, tenant):
    graph = await build_graph(tenant, budget_limit=Decimal("0.000001"))
    orch: Orchestrator = graph["orchestrator"]
    task = await orch.submit(tenant, title="Research",
                             objective="Research churn reduction strategies",
                             capability="document.synthesis")
    outcome = await orch.run_task(tenant, task.task_id)
    assert outcome.status == TaskStatus.BLOCKED
    assert "budget" in (outcome.error or "").lower()
    assert outcome.steps_executed == 0


async def test_orchestrator_kill_switch_blocks_new_work(svc, tenant):
    await svc["budgets"].kill_switch(tenant, reason="test freeze")
    orch: Orchestrator = svc["orchestrator"]
    task = await orch.submit(tenant, title="Find Ada",
                             objective="Find contact Ada Okafor",
                             capability="contact.lookup")
    outcome = await orch.run_task(tenant, task.task_id)
    assert outcome.status == TaskStatus.BLOCKED


# ---------------------------------------------------------------------------
# Orchestrator: cancel
# ---------------------------------------------------------------------------
async def test_orchestrator_cancel_pending(svc, tenant):
    orch: Orchestrator = svc["orchestrator"]
    task = await orch.submit(tenant, title="Later",
                             objective="Find contact Ada Okafor",
                             capability="contact.lookup")
    cancelled = await orch.cancel(tenant, task.task_id, reason="user changed mind")
    assert cancelled.status == TaskStatus.CANCELLED


async def test_orchestrator_cancel_terminal_raises(svc, tenant):
    orch: Orchestrator = svc["orchestrator"]
    task = await orch.submit(tenant, title="Find Ada",
                             objective="Find contact Ada Okafor",
                             capability="contact.lookup")
    await orch.run_task(tenant, task.task_id)
    from app.agents.orchestrator import IllegalTransition
    with pytest.raises(IllegalTransition):
        await orch.cancel(tenant, task.task_id)


# ---------------------------------------------------------------------------
# Models: stub records cost
# ---------------------------------------------------------------------------
async def test_models_stub_records_cost(svc, tenant):
    provider = make_provider(costs=svc["costs"], force_stub=True)
    assert isinstance(provider, StubModelProvider)
    resp = await provider.complete(contracts.ModelRequest(
        tenant=tenant, model=None,
        messages=[{"role": "user", "content": "hello"}],
        metadata={"task_id": "t-1"}))
    assert resp.cost_usd > 0
    assert resp.model_used.startswith("stub:"), "stub must label its responses"
    assert "not a real model call" in (resp.content or "")
    assert svc["costs"].records, "cost must be recorded"


async def test_model_router_tiers():
    router = ModelRouter()
    assert router.pick("default")
    assert router.fallback_chain("reasoning")


# ---------------------------------------------------------------------------
# Eval harness: regression suite + version comparison
# ---------------------------------------------------------------------------


async def _run_case_observation(payload: dict, tenant) -> dict:
    """Drive one eval case through a fresh governed graph."""
    from app.agents.registry import AgentDefinitionRecord
    graph = await build_graph(tenant)
    case_id, objective, capability = (payload["case_id"], payload["objective"],
                                      payload["capability"])
    policy = graph["policy"]
    if case_id == "deny-bulk-sms":
        policy.deny_actions.add("tool.comms.send_bulk_sms")
        await graph["registry"].register(
            tenant, AgentDefinitionRecord(
                agent_id="bulk-sender", name="Bulk Sender", version=0,
                description="test", capabilities=("bulk.messaging",),
                cannot=("x",), allowed_tools=("comms.send_bulk_sms",),
                permissions=("comms:send_bulk",), autonomy_level=1))
        capability = "bulk.messaging"
    elif case_id == "approve-email":
        policy.require_approval_actions.add("tool.comms.send_email")
    elif case_id == "budget-blocked":
        graph = await build_graph(tenant, budget_limit=Decimal("0.000001"))

    orch: Orchestrator = graph["orchestrator"]
    task = await orch.submit(tenant, title=case_id, objective=objective,
                             capability=capability)
    outcome = await orch.run_task(tenant, task.task_id)
    # drive approvals like a human would
    for _ in range(3):
        if outcome.status != TaskStatus.WAITING_APPROVAL:
            break
        pending, _ = await graph["approvals"].list_pending(tenant)
        for ap in pending:
            await graph["approvals"].decide(tenant, ap.approval_id, approved=True)
        # fake policy honors granted approvals (governance owns the real lookup)
        policy.require_approval_actions.clear()
        outcome = await orch.resume(tenant, task.task_id)

    tool_sequence = [r["tool"] for r in task.step_results if r.get("ok")]
    denied = [p.get("action") for p in task.policy_decisions
              if p.get("effect") == "deny"]
    gated = [p.get("action") for p in task.policy_decisions
             if p.get("effect") == "require_approval"]
    return {"success": outcome.status == TaskStatus.COMPLETED,
            "tool_sequence": tool_sequence,
            "denied_actions": denied,
            "approved_via_gate": gated,
            "cost_usd": str(task.cost_usd),
            "retries": task.retries,
            "notes": outcome.error or ""}


async def test_eval_regression_suite_passes(tenant):
    suite = regression_suite()
    assert len(suite.cases) == 8

    async def run_fn(payload):
        return await _run_case_observation(payload, tenant)

    results = await suite.run(run_fn)
    summary = suite.summary(results)
    assert summary["cases"] == 8
    assert summary["pass_rate"] == 1.0, \
        [f"{r.case_id}: success={r.task_success} tools={r.tool_accuracy} "
         f"compliance={r.policy_compliance} budget={r.cost_within_budget} "
         f"notes={r.notes}" for r in results if not r.passed]


async def test_eval_compare_versions(tenant):
    suite = regression_suite()

    async def run_fn(payload):
        return await _run_case_observation(payload, tenant)

    async def degraded(payload):
        obs = await _run_case_observation(payload, tenant)
        obs["cost_usd"] = str(Decimal(obs["cost_usd"]) + Decimal("5.00"))
        return obs

    report = await compare_versions(suite, {"baseline": run_fn,
                                            "degraded": degraded})
    assert report["baseline"] == "baseline"
    assert set(report["per_version"]) == {"baseline", "degraded"}
    assert report["deltas_vs_baseline"]["degraded"]["cost_delta_usd"] > 0


# ---------------------------------------------------------------------------
# MCP: untrusted until authorized; policy gates before any external call
# ---------------------------------------------------------------------------
async def test_mcp_register_starts_untrusted(svc, tenant):
    mcp = MCPManager(policy=svc["policy"], approvals=svc["approvals"],
                     audit=svc["audit"])
    rec = await mcp.register(tenant, name="crm-bridge", transport="streamable-http",
                             endpoint="https://example.invalid/mcp")
    assert rec.status == MCPServerStatus.UNTRUSTED
    with pytest.raises(MCPNotAuthorized):
        await mcp.execute(tenant, server_name="crm-bridge",
                          tool_name="search", arguments={})


async def test_mcp_policy_denies_before_any_external_call(svc, tenant):
    svc["policy"].deny_actions.add("tool.mcp.crm-bridge.search")
    mcp = MCPManager(policy=svc["policy"], approvals=svc["approvals"],
                     audit=svc["audit"])
    await mcp.register(tenant, name="crm-bridge", transport="streamable-http",
                       endpoint="https://example.invalid/mcp")
    await mcp.authorize(tenant, "crm-bridge", vetted_by="tester")
    result = await mcp.execute(tenant, server_name="crm-bridge",
                               tool_name="search", arguments={"q": "x"})
    assert not result.ok and result.error.startswith("policy_denied")
    entries = svc["audit"].entries(tenant.tenant_id)
    assert any(e.action == "tool.policy_decision"
               and e.payload.get("effect") == "deny" for e in entries)


async def test_mcp_allow_path_labeled_stub(svc, tenant):
    mcp = MCPManager(policy=svc["policy"], approvals=svc["approvals"],
                     audit=svc["audit"])
    await mcp.register(tenant, name="crm-bridge", transport="streamable-http",
                       endpoint="https://example.invalid/mcp")
    await mcp.authorize(tenant, "crm-bridge", vetted_by="tester")
    result = await mcp.execute(tenant, server_name="crm-bridge",
                               tool_name="search", arguments={"q": "x"})
    assert result.ok
    assert result.untrusted is True  # outputs are untrusted data
    assert result.output.get("_stub") is True


async def test_mcp_tenant_isolation(svc, tenant, tenant_b):
    mcp = MCPManager(policy=svc["policy"], approvals=svc["approvals"],
                     audit=svc["audit"])
    await mcp.register(tenant, name="crm-bridge", transport="streamable-http",
                       endpoint="https://example.invalid/mcp")
    assert await mcp.get(tenant_b, "crm-bridge") is None
