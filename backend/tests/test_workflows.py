# ruff: noqa: F811  # fixture params shadow fixture imports (pytest idiom)
"""Tests for the workflows package (app/workflows) + its HTTP router.

Covers: DAG validation (cycles, triggers, required config, reachability),
condition-expression errors, version publishing immutability, execution
idempotency, policy-before-start, tenant isolation, node execution + events,
conditional branches, bounded retries + on_error, approval parking /
approve / deny / cancel, signaling errors, and every branch of the seeded
lead-qualification-outreach workflow (including research-stub honesty).
"""
from __future__ import annotations

import pytest

from app.workflows.dsl import validate_dag
from app.workflows.runner import WorkflowError as RunnerWorkflowError
from app.workflows.service import (
    PolicyDeniedError,
    WorkflowNotFound,
    WorkflowValidationError,
)
from tests.biz_test_helpers import (  # noqa: F401
    biz_approvals,
    biz_bus,
    biz_client,
    biz_policy,
    biz_services,
    tenant_headers,
)


def _simple_dag() -> dict:
    return {
        "nodes": [
            {"id": "start", "type": "trigger"},
            {"id": "research", "type": "action",
             "config": {"action": "research_stub", "params": {"lead_id": "L1"}}},
        ],
        "edges": [{"from": "start", "to": "research"}],
    }


def _branch_dag() -> dict:
    return {
        "nodes": [
            {"id": "start", "type": "trigger"},
            {"id": "gate", "type": "condition",
             "config": {"expression": {"var": "input.hot"}}},
            {"id": "hot_path", "type": "action",
             "config": {"action": "research_stub", "params": {}}},
            {"id": "cold_path", "type": "action",
             "config": {"action": "research_stub", "params": {}}},
        ],
        "edges": [
            {"from": "start", "to": "gate"},
            {"from": "gate", "to": "hot_path", "label": "true"},
            {"from": "gate", "to": "cold_path", "label": "false"},
        ],
    }


def _retry_dag() -> dict:
    return {
        "nodes": [
            {"id": "start", "type": "trigger"},
            {"id": "flaky", "type": "tool",
             "config": {"tool_name": "no_such_tool"},
             "retry": {"max_attempts": 2, "backoff_seconds": 0},
             "on_error": "fallback"},
            {"id": "fallback", "type": "action",
             "config": {"action": "research_stub", "params": {}}},
        ],
        "edges": [{"from": "start", "to": "flaky"}],
    }


def _approval_dag() -> dict:
    return {
        "nodes": [
            {"id": "start", "type": "trigger"},
            {"id": "approve_it", "type": "approval",
             "config": {"policy_ref": "outreach.send.v1",
                        "action": "comms.outreach.send"}},
            {"id": "granted", "type": "action",
             "config": {"action": "research_stub", "params": {}}},
            {"id": "denied", "type": "action",
             "config": {"action": "research_stub", "params": {}}},
        ],
        "edges": [
            {"from": "start", "to": "approve_it"},
            {"from": "approve_it", "to": "granted"},
            {"from": "approve_it", "to": "denied", "label": "denied"},
        ],
    }


async def _state(biz_services, tenant, execution_id: str) -> dict:
    execution = await biz_services.workflows.get_execution(tenant, execution_id)
    return execution.state or {}


async def _publish(biz_services, tenant, name: str, dag: dict):
    definition = await biz_services.workflows.create_definition(
        tenant, name, None, 2, dag)
    await biz_services.workflows.publish_version(tenant, str(definition.id))
    return definition


# ------------------------------------------------------------------ DAG validation
def test_dag_rejects_cycle():
    dag = _simple_dag()
    dag["edges"].append({"from": "research", "to": "start"})
    with pytest.raises(WorkflowValidationError) as exc:
        _raise_validation(dag)
    assert any("cycle" in e.lower() for e in exc.value.details["errors"])


def _raise_validation(dag: dict) -> None:
    from app.workflows.dsl import DagValidationError
    try:
        validate_dag(dag)
    except DagValidationError as e:
        raise WorkflowValidationError("invalid dag", {"errors": e.errors}) from e


def test_dag_requires_exactly_one_trigger():
    dag = _simple_dag()
    dag["nodes"] = [n for n in dag["nodes"] if n["type"] != "trigger"]
    with pytest.raises(WorkflowValidationError) as exc:
        _raise_validation(dag)
    assert any("trigger" in e.lower() for e in exc.value.details["errors"])


def test_dag_requires_approval_config():
    dag = _approval_dag()
    dag["nodes"][1]["config"] = {}  # strip policy_ref + action
    with pytest.raises(WorkflowValidationError) as exc:
        _raise_validation(dag)
    assert any("policy_ref" in e for e in exc.value.details["errors"])


def test_dag_rejects_unreachable_nodes():
    dag = _simple_dag()
    dag["nodes"].append({"id": "orphan", "type": "action",
                         "config": {"action": "research_stub", "params": {}}})
    with pytest.raises(WorkflowValidationError) as exc:
        _raise_validation(dag)
    assert any("unreachable" in e.lower() for e in exc.value.details["errors"])


def test_condition_expression_is_validated_at_runtime():
    # structurally valid but semantically broken expressions surface as errors
    from app.workflows.conditions import ExpressionError, evaluate
    with pytest.raises(ExpressionError):
        evaluate({"nonsense": 1}, {})
    with pytest.raises(ExpressionError):
        evaluate({"op": "frobnicate", "left": 1, "right": 2}, {})
    assert evaluate({"op": "gte", "left": {"var": "a"}, "right": 3}, {"a": 5}) is True


# ------------------------------------------------------------------ definitions + versions
async def test_publish_version_is_immutable(biz_services, tenant):
    definition = await _publish(biz_services, tenant, "imm", _simple_dag())
    v1 = await biz_services.workflows.get_version(tenant, str(definition.id), 1)
    assert v1.dag["nodes"][0]["id"] == "start"
    await biz_services.workflows.update_definition(tenant, str(definition.id),
                                                   {"dag": _branch_dag()})
    await biz_services.workflows.publish_version(tenant, str(definition.id))
    v1_again = await biz_services.workflows.get_version(tenant, str(definition.id), 1)
    v2 = await biz_services.workflows.get_version(tenant, str(definition.id), 2)
    assert v1_again.dag == v1.dag  # published versions never change
    assert v2.dag["nodes"][1]["id"] == "gate"


async def test_start_requires_published_version(biz_services, tenant):
    definition = await biz_services.workflows.create_definition(
        tenant, "unpub", None, 2, _simple_dag())
    with pytest.raises(WorkflowValidationError):
        await biz_services.workflows.start_execution(
            tenant, definition_id=str(definition.id))


async def test_start_execution_idempotency(biz_services, tenant):
    definition = await _publish(biz_services, tenant, "idem", _simple_dag())
    e1 = await biz_services.workflows.start_execution(
        tenant, definition_id=str(definition.id), idempotency_key="wf-k-1")
    e2 = await biz_services.workflows.start_execution(
        tenant, definition_id=str(definition.id), idempotency_key="wf-k-1")
    assert str(e1.id) == str(e2.id)


async def test_policy_denial_blocks_start(biz_services, tenant, biz_policy):
    definition = await _publish(biz_services, tenant, "denied", _simple_dag())
    biz_policy.deny_actions.add("workflows.execution.start")
    with pytest.raises(PolicyDeniedError):
        await biz_services.workflows.start_execution(
            tenant, definition_id=str(definition.id))


async def test_cross_tenant_invisibility(biz_services, tenant, tenant_b):
    definition = await _publish(biz_services, tenant, "priv", _simple_dag())
    with pytest.raises(WorkflowNotFound):
        await biz_services.workflows.get_definition(tenant_b, str(definition.id))


async def test_domain_events_on_definition_lifecycle(biz_services, tenant, biz_bus):
    await _publish(biz_services, tenant, "ev", _simple_dag())
    topics = {e.topic for e in biz_bus.events}
    assert {"workflows.definition.created", "workflows.version.published"} <= topics


# ------------------------------------------------------------------ execution: happy path
async def test_simple_run_succeeds_and_records_events(biz_services, tenant, biz_bus):
    definition = await _publish(biz_services, tenant, "run", _simple_dag())
    execution = await biz_services.workflows.start_execution(
        tenant, definition_id=str(definition.id), input={"lead_id": "L1"})
    assert execution.status == "succeeded"
    events, _total = await biz_services.workflows.list_execution_events(
        tenant, str(execution.id))
    kinds = [e.event_type for e in events]
    assert "execution.started" in kinds
    assert "node.completed" in kinds
    assert kinds[-1] == "execution.finished"
    inspected = await biz_services.workflows.inspect(tenant, str(execution.id))
    assert inspected["status"] == "succeeded"
    state = await _state(biz_services, tenant, str(execution.id))
    assert state["context"]["research"]["status"] == "stub"
    # research stub honesty: no external provider is claimed
    note = state["context"]["research"]["note"]
    assert "not configured" in note


async def test_condition_branches(biz_services, tenant):
    definition = await _publish(biz_services, tenant, "branch", _branch_dag())
    hot = await biz_services.workflows.start_execution(
        tenant, definition_id=str(definition.id), input={"hot": True})
    cold = await biz_services.workflows.start_execution(
        tenant, definition_id=str(definition.id), input={"hot": False})
    assert hot.status == "succeeded" and cold.status == "succeeded"
    hot_done, _h = await biz_services.workflows.list_execution_events(
        tenant, str(hot.id))
    cold_done, _c = await biz_services.workflows.list_execution_events(
        tenant, str(cold.id))
    assert any(e.event_type == "node.completed" and e.payload["node"] == "hot_path"
               for e in hot_done)
    assert any(e.event_type == "node.completed" and e.payload["node"] == "cold_path"
               for e in cold_done)


async def test_retry_then_on_error(biz_services, tenant):
    definition = await _publish(biz_services, tenant, "retry", _retry_dag())
    execution = await biz_services.workflows.start_execution(
        tenant, definition_id=str(definition.id))
    assert execution.status == "succeeded"  # on_error path recovered
    events, _total = await biz_services.workflows.list_execution_events(
        tenant, str(execution.id))
    retries = [e for e in events if e.event_type == "node.retry"]
    assert len(retries) == 1  # failure #1 schedules retry; failure #2 -> on_error
    assert retries[0].payload["attempt"] == 1
    assert retries[0].payload["max_attempts"] == 2
    assert any(e.event_type == "node.failed" and e.payload["node"] == "flaky"
               for e in events)
    assert any(e.event_type == "node.completed" and e.payload["node"] == "fallback"
               for e in events)


async def test_bad_expression_fails_execution(biz_services, tenant):
    dag = {
        "nodes": [
            {"id": "start", "type": "trigger"},
            {"id": "gate", "type": "condition",
             "config": {"expression": {"nonsense": True}}},
            {"id": "end", "type": "action",
             "config": {"action": "research_stub", "params": {}}},
        ],
        "edges": [{"from": "start", "to": "gate"},
                  {"from": "gate", "to": "end", "label": "true"}],
    }
    definition = await _publish(biz_services, tenant, "badexpr", dag)
    execution = await biz_services.workflows.start_execution(
        tenant, definition_id=str(definition.id))
    assert execution.status == "failed"
    assert execution.error is not None


# ------------------------------------------------------------------ approvals
async def test_approval_skipped_when_policy_allows(biz_services, tenant):
    # policy ALLOW -> approval node is skipped without parking
    definition = await _publish(biz_services, tenant, "appr-skip", _approval_dag())
    execution = await biz_services.workflows.start_execution(
        tenant, definition_id=str(definition.id))
    assert execution.status == "succeeded"
    events, _ = await biz_services.workflows.list_execution_events(
        tenant, str(execution.id))
    assert not any(e.event_type == "approval.requested" for e in events)


async def test_approval_parks_and_approve_resumes(biz_services, tenant, biz_bus,
                                                 biz_policy):
    biz_policy.require_approval_actions.add("comms.outreach.send")
    definition = await _publish(biz_services, tenant, "appr", _approval_dag())
    execution = await biz_services.workflows.start_execution(
        tenant, definition_id=str(definition.id))
    assert execution.status == "waiting_approval"
    events, _total = await biz_services.workflows.list_execution_events(
        tenant, str(execution.id))
    requested = next(e for e in events if e.event_type == "approval.requested")
    approval_id = requested.payload["approval_id"]

    resumed = await biz_services.workflows.signal_execution(
        tenant, str(execution.id),
        "approval", {"approval_id": approval_id, "approved": True,
                     "note": "looks fine"})
    assert resumed.status == "succeeded"
    events2, _t2 = await biz_services.workflows.list_execution_events(
        tenant, str(execution.id))
    decided = next(e for e in events2 if e.event_type == "approval.decided")
    assert decided.payload["approved"] is True
    assert any(e.event_type == "node.completed" and e.payload["node"] == "granted"
               for e in events2)


async def test_approval_denied_follows_denied_edge(biz_services, tenant, biz_policy):
    biz_policy.require_approval_actions.add("comms.outreach.send")
    definition = await _publish(biz_services, tenant, "deny", _approval_dag())
    execution = await biz_services.workflows.start_execution(
        tenant, definition_id=str(definition.id))
    assert execution.status == "waiting_approval"
    events, _total = await biz_services.workflows.list_execution_events(
        tenant, str(execution.id))
    approval_id = next(e for e in events
                       if e.event_type == "approval.requested").payload["approval_id"]
    resumed = await biz_services.workflows.signal_execution(
        tenant, str(execution.id),
        "approval", {"approval_id": approval_id, "approved": False,
                     "note": "not yet"})
    assert resumed.status == "succeeded"
    events2, _t2 = await biz_services.workflows.list_execution_events(
        tenant, str(execution.id))
    assert any(e.event_type == "node.completed" and e.payload["node"] == "denied"
               for e in events2)
    assert not any(e.event_type == "node.completed" and e.payload["node"] == "granted"
                   for e in events2)


async def test_signal_unknown_approval_errors(biz_services, tenant, biz_policy):
    biz_policy.require_approval_actions.add("comms.outreach.send")
    definition = await _publish(biz_services, tenant, "sig", _approval_dag())
    execution = await biz_services.workflows.start_execution(
        tenant, definition_id=str(definition.id))
    with pytest.raises(RunnerWorkflowError):
        await biz_services.workflows.signal_execution(
            tenant, str(execution.id),
            "approval", {"approval_id": "nope", "approved": True})


async def test_cancel_parked_execution(biz_services, tenant, biz_policy):
    biz_policy.require_approval_actions.add("comms.outreach.send")
    definition = await _publish(biz_services, tenant, "cancel", _approval_dag())
    execution = await biz_services.workflows.start_execution(
        tenant, definition_id=str(definition.id))
    cancelled = await biz_services.workflows.cancel_execution(
        tenant, str(execution.id), reason="no longer needed")
    assert cancelled.status == "cancelled"
    # driving again is a no-op on terminal executions
    again = await biz_services.runner.drive(tenant, str(execution.id))
    assert again.status == "cancelled"


# ------------------------------------------------------------------ lead workflow (seeded)
async def _seeded_lead(biz_services, tenant, *, high_score: bool):
    org = await biz_services.crm.create_organization(
        tenant, {"name": "Acme Corp", "industry": "software",
                 "size_band": "enterprise" if high_score else "smb"})
    if high_score:
        contact = await biz_services.crm.create_contact(
            tenant, {"org_id": str(org.id), "first_name": "Dana",
                     "last_name": "Reyes", "email": "dana@acmecorp.com",
                     "title": "VP Engineering", "phone": "+1-555-0101"})
        lead = await biz_services.crm.create_lead(
            tenant, {"contact_id": str(contact.id), "org_id": str(org.id),
                     "source": "referral"})
    else:
        contact = await biz_services.crm.create_contact(
            tenant, {"org_id": str(org.id), "first_name": "Sam",
                     "last_name": "Doe", "email": "sam@gmail.com",
                     "title": "intern"})
        lead = await biz_services.crm.create_lead(
            tenant, {"contact_id": str(contact.id), "org_id": str(org.id),
                     "source": "outbound"})
    return lead


async def test_lead_workflow_seeded_and_approved_path(biz_services, tenant, biz_policy):
    biz_policy.require_approval_actions.add("comms.outreach.send")
    from app.workflows.definitions.lead_outreach import DEFINITION_NAME
    definition = await biz_services.workflows.seed_lead_outreach(tenant)
    assert definition.name == DEFINITION_NAME
    lead = await _seeded_lead(biz_services, tenant, high_score=True)
    execution = await biz_services.workflows.start_execution(
        tenant, definition_name=DEFINITION_NAME, input={"lead_id": str(lead.id)})
    assert execution.status == "waiting_approval"
    state = await _state(biz_services, tenant, str(execution.id))
    outputs = state["context"]
    # research stub is explicit about having no external provider
    assert outputs["research"]["status"] == "stub"
    assert "not configured" in outputs["research"]["note"]
    assert outputs["rescore"]["score"] >= 40

    events, _total = await biz_services.workflows.list_execution_events(
        tenant, str(execution.id))
    approval_id = next(e for e in events
                       if e.event_type == "approval.requested").payload["approval_id"]
    resumed = await biz_services.workflows.signal_execution(
        tenant, str(execution.id),
        "approval", {"approval_id": approval_id, "approved": True})
    assert resumed.status == "succeeded"

    # approved send was LOGGED (not delivered — no external claim)
    activities, _ = await biz_services.crm.list_activities(
        tenant, subject_type="lead", subject_id=str(lead.id))
    kinds = [a.type for a in activities]
    assert "outreach_draft" in kinds and "outreach_sent" in kinds
    # follow-up task was created
    tasks, total = await biz_services.tasks.list_tasks(tenant)
    assert total == 1
    assert "Follow up with" in tasks[0].title
    assert tasks[0].priority == "high"


async def test_lead_workflow_low_score_nurture(biz_services, tenant):
    from app.workflows.definitions.lead_outreach import DEFINITION_NAME
    await biz_services.workflows.seed_lead_outreach(tenant)
    lead = await _seeded_lead(biz_services, tenant, high_score=False)
    execution = await biz_services.workflows.start_execution(
        tenant, definition_name=DEFINITION_NAME, input={"lead_id": str(lead.id)})
    assert execution.status == "succeeded"
    state = await _state(biz_services, tenant, str(execution.id))
    assert state["context"]["rescore"]["score"] < 40
    tasks, total = await biz_services.tasks.list_tasks(tenant)
    assert total == 1
    assert "Nurture low-score lead" in tasks[0].title
    assert tasks[0].priority == "low"


async def test_lead_workflow_denied_manual_review(biz_services, tenant, biz_policy):
    biz_policy.require_approval_actions.add("comms.outreach.send")
    from app.workflows.definitions.lead_outreach import DEFINITION_NAME
    await biz_services.workflows.seed_lead_outreach(tenant)
    lead = await _seeded_lead(biz_services, tenant, high_score=True)
    execution = await biz_services.workflows.start_execution(
        tenant, definition_name=DEFINITION_NAME, input={"lead_id": str(lead.id)})
    assert execution.status == "waiting_approval"
    events, _total = await biz_services.workflows.list_execution_events(
        tenant, str(execution.id))
    approval_id = next(e for e in events
                       if e.event_type == "approval.requested").payload["approval_id"]
    resumed = await biz_services.workflows.signal_execution(
        tenant, str(execution.id),
        "approval", {"approval_id": approval_id, "approved": False,
                     "note": "too soon"})
    assert resumed.status == "succeeded"
    tasks, total = await biz_services.tasks.list_tasks(tenant)
    assert total == 1
    assert "Review outreach" in tasks[0].title
    assert tasks[0].priority == "high"


# ------------------------------------------------------------------ HTTP surface
async def test_start_execution_requires_idempotency_key(biz_client, tenant):
    keys = iter(["http-wf2-def", "http-wf2-pub"])

    def _h():
        return {**tenant_headers(tenant), "Idempotency-Key": next(keys)}

    r = await biz_client.post("/api/v1/workflows/definitions",
                              json={"name": "http-wf", "autonomy_level": 2,
                                    "dag": _simple_dag()},
                              headers=_h())
    assert r.status_code == 201, r.text
    definition_id = r.json()["id"]
    pub = await biz_client.post(
        f"/api/v1/workflows/definitions/{definition_id}/publish", headers=_h())
    assert pub.status_code in (200, 201), pub.text
    r2 = await biz_client.post(
        "/api/v1/workflows/executions",
        json={"definition_id": definition_id},
        headers=tenant_headers(tenant))  # no Idempotency-Key
    assert r2.status_code == 422
    assert r2.json()["error"]["code"] == "missing_idempotency_key"


async def test_http_signal_and_inspect(biz_client, tenant, biz_policy):
    biz_policy.require_approval_actions.add("comms.outreach.send")
    keys = iter(["http-wf-def", "http-wf-pub", "http-wf-exe", "http-wf-sig"])

    def _h():
        return {**tenant_headers(tenant), "Idempotency-Key": next(keys)}

    r = await biz_client.post("/api/v1/workflows/definitions",
                              json={"name": "http-appr", "autonomy_level": 2,
                                    "dag": _approval_dag()},
                              headers=_h())
    assert r.status_code == 201, r.text
    definition_id = r.json()["id"]
    pub = await biz_client.post(
        f"/api/v1/workflows/definitions/{definition_id}/publish", headers=_h())
    assert pub.status_code in (200, 201), pub.text
    started = await biz_client.post(
        "/api/v1/workflows/executions",
        json={"definition_id": definition_id},
        headers=_h())
    assert started.status_code == 201, started.text
    execution_id = started.json()["id"]
    assert started.json()["status"] == "waiting_approval"
    insp = await biz_client.get(
        f"/api/v1/workflows/executions/{execution_id}/inspect",
        headers=tenant_headers(tenant))
    assert insp.status_code == 200, insp.text
    approval_id = insp.json()["pending_approval"]["approval_id"]
    sig = await biz_client.post(
        f"/api/v1/workflows/executions/{execution_id}/signal",
        json={"signal": "approval",
              "payload": {"approval_id": approval_id, "approved": True}},
        headers=_h())
    assert sig.status_code == 200, sig.text
    assert sig.json()["status"] == "succeeded"
