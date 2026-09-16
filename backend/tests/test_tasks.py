# ruff: noqa: F811  # fixture params shadow fixture imports (pytest idiom)
"""Tests for the tasks/projects package (app/tasks) + its HTTP router.

Covers: tenant isolation, policy-before-write, domain events, HTTP idempotency
(+ 409 on key reuse with a different body), the binding state machine
(valid/illegal transitions), dependency cycle detection, completion blocking on
incomplete dependencies, transition history, durable bulk-op replay,
delegation entry point, and cursor pagination.
"""
from __future__ import annotations

import pytest

from app.tasks.service import (
    DependencyCycleError,
    IllegalTransitionError,
    PolicyDeniedError,
    TaskNotFound,
    TaskValidationError,
    can_transition,
)
from tests.biz_test_helpers import (  # noqa: F401
    biz_approvals,
    biz_bus,
    biz_client,
    biz_policy,
    biz_services,
    tenant_headers,
)

_KEY_SEQ = [0]


def mut_headers(tenant):
    """Tenant headers plus a fresh Idempotency-Key for mutating requests."""
    _KEY_SEQ[0] += 1
    return {**tenant_headers(tenant), "Idempotency-Key": f"test-key-{_KEY_SEQ[0]}"}


async def _task(biz_services, tenant, title="Do the thing", **kw):
    return await biz_services.tasks.create_task(tenant, {"title": title, **kw})


# ------------------------------------------------------------------ pure state machine
def test_can_transition_pure():
    assert can_transition("pending", "planning")
    assert can_transition("executing", "completed")
    assert not can_transition("pending", "completed")
    assert not can_transition("completed", "executing")
    assert not can_transition("completed", "completed")


# ------------------------------------------------------------------ tenant isolation
async def test_cross_tenant_invisibility(biz_services, tenant, tenant_b):
    task = await _task(biz_services, tenant)
    with pytest.raises(TaskNotFound):
        await biz_services.tasks.get_task(tenant_b, str(task.id))
    items, total = await biz_services.tasks.list_tasks(tenant_b)
    assert total == 0 and items == []


async def test_cross_tenant_invisibility_http(biz_client, tenant, tenant_b):
    r = await biz_client.post("/api/v1/tasks", json={"title": "secret"},
                              headers=mut_headers(tenant))
    assert r.status_code == 201, r.text
    task_id = r.json()["id"]
    r2 = await biz_client.get(f"/api/v1/tasks/{task_id}",
                              headers=tenant_headers(tenant_b))
    assert r2.status_code == 404
    assert r2.json()["error"]["code"] == "not_found"


# ------------------------------------------------------------------ policy
async def test_policy_denial_blocks_write(biz_services, tenant, biz_policy):
    biz_policy.deny_actions.add("tasks.task.create")
    with pytest.raises(PolicyDeniedError):
        await _task(biz_services, tenant)
    _, total = await biz_services.tasks.list_tasks(tenant)
    assert total == 0


async def test_policy_denial_http(biz_client, tenant, biz_policy):
    biz_policy.deny_actions.add("tasks.task.create")
    r = await biz_client.post("/api/v1/tasks", json={"title": "x"},
                              headers=mut_headers(tenant))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "policy_denied"


# ------------------------------------------------------------------ events
async def test_domain_events(biz_services, tenant, biz_bus):
    task = await _task(biz_services, tenant)
    await biz_services.tasks.transition_task(tenant, str(task.id), "planning")
    topics = {e.topic for e in biz_bus.events}
    assert {"tasks.task.created", "tasks.task.transitioned"} <= topics
    tr = next(e for e in biz_bus.events if e.topic == "tasks.task.transitioned")
    assert tr.aggregate_id == str(task.id)
    assert tr.payload["from"] == "pending" and tr.payload["to"] == "planning"


# ------------------------------------------------------------------ transitions
async def test_valid_transition_path(biz_services, tenant):
    task = await _task(biz_services, tenant)
    for to in ("planning", "executing", "completed"):
        task = await biz_services.tasks.transition_task(tenant, str(task.id), to)
    assert task.status == "completed"
    assert task.completed_at is not None


async def test_illegal_transition_rejected(biz_services, tenant):
    task = await _task(biz_services, tenant)
    with pytest.raises(IllegalTransitionError):
        await biz_services.tasks.transition_task(tenant, str(task.id), "completed")


async def test_illegal_transition_http(biz_client, tenant):
    r = await biz_client.post("/api/v1/tasks", json={"title": "t"},
                              headers=mut_headers(tenant))
    task_id = r.json()["id"]
    r2 = await biz_client.post(f"/api/v1/tasks/{task_id}/transition",
                               json={"to": "completed"},
                               headers=mut_headers(tenant))
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "illegal_transition"


async def test_transition_history_recorded(biz_services, tenant):
    task = await _task(biz_services, tenant)
    await biz_services.tasks.transition_task(tenant, str(task.id), "planning",
                                             note="kickoff")
    history = await biz_services.tasks.list_transitions(tenant, str(task.id))
    # creation record ("", "pending") plus the explicit transition
    assert [(h.from_status, h.to_status) for h in history] == [
        ("", "pending"), ("pending", "planning")]
    assert history[-1].note == "kickoff"


# ------------------------------------------------------------------ dependencies
async def test_dependency_cycle_rejected(biz_services, tenant):
    a = await _task(biz_services, tenant, title="A")
    b = await _task(biz_services, tenant, title="B")
    await biz_services.tasks.add_dependency(tenant, str(a.id), str(b.id))
    with pytest.raises(DependencyCycleError):
        await biz_services.tasks.add_dependency(tenant, str(b.id), str(a.id))
    with pytest.raises(TaskValidationError):
        await biz_services.tasks.add_dependency(tenant, str(a.id), str(a.id))


async def test_completion_blocked_by_incomplete_dependencies(biz_services, tenant):
    a = await _task(biz_services, tenant, title="A")
    b = await _task(biz_services, tenant, title="B")
    await biz_services.tasks.add_dependency(tenant, str(b.id), str(a.id))
    await biz_services.tasks.transition_task(tenant, str(b.id), "planning")
    await biz_services.tasks.transition_task(tenant, str(b.id), "executing")
    with pytest.raises(TaskValidationError) as exc:
        await biz_services.tasks.transition_task(tenant, str(b.id), "completed")
    assert "dependencies are not completed" in str(exc.value)
    # complete A, then B can complete
    await biz_services.tasks.transition_task(tenant, str(a.id), "planning")
    await biz_services.tasks.transition_task(tenant, str(a.id), "executing")
    await biz_services.tasks.transition_task(tenant, str(a.id), "completed")
    done = await biz_services.tasks.transition_task(tenant, str(b.id), "completed")
    assert done.status == "completed"


# ------------------------------------------------------------------ bulk + durable replay
async def test_bulk_operations_and_replay(biz_services, tenant, biz_bus):
    ops = [
        {"op": "create", "idempotency_key": "bulk-1",
         "payload": {"title": "Bulk one"}},
        {"op": "create", "idempotency_key": "bulk-2",
         "payload": {"title": "Bulk two"}},
        {"op": "transition", "idempotency_key": "bulk-3", "task_id": "missing",
         "payload": {"to": "planning"}},
    ]
    results = await biz_services.tasks.bulk(tenant, ops)
    assert [r["ok"] for r in results] == [True, True, False]
    assert results[2]["error"]["code"] == "validation_error"
    # replay the same batch: creates replay stored results, no new rows
    results2 = await biz_services.tasks.bulk(tenant, ops[:2])
    assert all(r.get("replayed") for r in results2)
    assert results2[0]["task_id"] == results[0]["task_id"]
    _, total = await biz_services.tasks.list_tasks(tenant)
    assert total == 2
    # duplicate key inside one batch is rejected
    dup = await biz_services.tasks.bulk(tenant, [
        {"op": "create", "idempotency_key": "bulk-1",
         "payload": {"title": "Bulk one"}},
        {"op": "create", "idempotency_key": "bulk-1",
         "payload": {"title": "Bulk one"}},
    ])
    assert dup[0]["ok"] is True and dup[0].get("replayed") is True
    assert dup[1]["ok"] is False


async def test_bulk_http(biz_client, tenant):
    r = await biz_client.post(
        "/api/v1/tasks/bulk",
        json={"operations": [
            {"op": "create", "idempotency_key": "http-bulk-1",
             "payload": {"title": "HTTP bulk"}}]},
        headers=mut_headers(tenant))
    assert r.status_code == 200, r.text
    assert r.json()["results"][0]["ok"] is True


# ------------------------------------------------- assignment / delegation / comments
async def test_assign_and_comment(biz_services, tenant, biz_bus):
    task = await _task(biz_services, tenant)
    assigned = await biz_services.tasks.assign_task(tenant, str(task.id),
                                                    "user-9", None)
    assert assigned.assignee_user_id == "user-9"
    comment = await biz_services.tasks.add_comment(tenant, str(task.id),
                                                   "looks good", author_type="user")
    assert comment.body == "looks good"
    comments, total = await biz_services.tasks.list_comments(tenant, str(task.id))
    assert total == 1
    assert comments[0].body == "looks good"
    assert {e.topic for e in biz_bus.events} >= {"tasks.task.assigned"}


async def test_delegate_task_records_plan(biz_services, tenant):
    task = await _task(biz_services, tenant)
    delegated = await biz_services.tasks.delegate_task(
        tenant, str(task.id), agent_id="agent-1",
        capability="research", goal="research the lead")
    assert delegated.status == "planning"
    assert delegated.plan["delegation"]["capability"] == "research"


async def test_task_idempotency_key_returns_existing(biz_services, tenant):
    t1 = await biz_services.tasks.create_task(tenant, {"title": "Same"},
                                              idempotency_key="task-k-1")
    t2 = await biz_services.tasks.create_task(tenant, {"title": "Same"},
                                              idempotency_key="task-k-1")
    assert str(t1.id) == str(t2.id)
    _, total = await biz_services.tasks.list_tasks(tenant)
    assert total == 1


# ------------------------------------------------- http: idempotency + subtasks
async def test_post_idempotency_replays(biz_client, tenant):
    headers = {**tenant_headers(tenant), "Idempotency-Key": "tk-1"}
    r1 = await biz_client.post("/api/v1/tasks", json={"title": "Once"}, headers=headers)
    assert r1.status_code == 201
    r2 = await biz_client.post("/api/v1/tasks", json={"title": "Once"}, headers=headers)
    assert r2.json()["id"] == r1.json()["id"]


async def test_missing_idempotency_key_is_422(biz_client, tenant):
    r = await biz_client.post("/api/v1/tasks", json={"title": "No key"},
                              headers=tenant_headers(tenant))
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "missing_idempotency_key"


async def test_post_idempotency_conflict(biz_client, tenant):
    headers = {**tenant_headers(tenant), "Idempotency-Key": "tk-2"}
    assert (await biz_client.post("/api/v1/tasks", json={"title": "A"},
                                  headers=headers)).status_code == 201
    r2 = await biz_client.post("/api/v1/tasks", json={"title": "B"}, headers=headers)
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "idempotency_conflict"


async def test_subtask_listing(biz_client, tenant):
    parent = (await biz_client.post("/api/v1/tasks", json={"title": "parent"},
                                      headers=mut_headers(tenant))).json()
    assert "id" in parent, parent
    child = (await biz_client.post(
        "/api/v1/tasks", json={"title": "child", "parent_id": parent["id"]},
        headers=mut_headers(tenant))).json()
    assert "id" in child, child
    assert child["parent_id"] == parent["id"]
    r = await biz_client.get("/api/v1/tasks", params={"parent_id": parent["id"]},
                             headers=tenant_headers(tenant))
    assert [t["id"] for t in r.json()["items"]] == [child["id"]]
