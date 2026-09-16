"""Router: agents. Contract: /API.md §agents.

Thin HTTP layer — no business logic. Calls the domain service graph supplied
via ``configure_services`` (wired by the app factory; tests inject in-memory
services). Returns 500 with code ``services_not_configured`` if unset.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

router = APIRouter(prefix="/agents", tags=["agents"])

_services: dict[str, Any] | None = None


def configure_services(**services: Any) -> None:
    """Inject the domain service graph. Called once by the app factory."""
    global _services
    _services = dict(services)


def _svc(name: str) -> Any:
    if _services is None or name not in _services:
        raise HTTPException(status_code=500, detail={
            "error": {"code": "services_not_configured",
                      "message": f"service '{name}' is not configured",
                      "details": {}, "trace_id": ""}})
    return _services[name]


def _tenant() -> Any:
    # Tenant resolution (JWT claims) is wired by the auth builder; tests
    # override this dependency.
    raise HTTPException(status_code=501, detail={
        "error": {"code": "auth_not_wired",
                  "message": "tenant resolution not wired",
                  "details": {}, "trace_id": ""}})


class RegisterAgentBody(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    capabilities: list[str] = []
    cannot: list[str] = []
    allowed_tools: list[str] = []
    permissions: list[str] = []
    autonomy_level: int = Field(default=1, ge=0, le=5)
    owner: str = "platform"


class PublishVersionBody(BaseModel):
    description: str | None = None
    capabilities: list[str] | None = None
    allowed_tools: list[str] | None = None
    autonomy_level: int | None = Field(default=None, ge=0, le=5)
    status: str | None = None


@router.get("")
async def list_agents(capability: str | None = Query(default=None),
                      status: str | None = Query(default=None),
                      tenant: Any = Depends(_tenant)) -> dict[str, Any]:
    registry = _svc("registry")
    recs = await registry.list(
        tenant,
        capability=capability,
        status=status)
    return {"items": [
        {"agent_id": r.agent_id, "name": r.name, "version": r.version,
         "description": r.description, "capabilities": list(r.capabilities),
         "cannot": list(r.cannot), "allowed_tools": list(r.allowed_tools),
         "status": r.status.value, "autonomy_level": r.autonomy_level,
         "owner": r.owner}
        for r in recs], "next_page_token": None}


@router.post("", status_code=201)
async def register_agent(body: RegisterAgentBody,
                         tenant: Any = Depends(_tenant)) -> dict[str, Any]:
    registry = _svc("registry")
    rec = await registry.create(
        tenant, agent_id=body.agent_id, name=body.name,
        description=body.description, capabilities=tuple(body.capabilities),
        cannot=tuple(body.cannot), allowed_tools=tuple(body.allowed_tools),
        permissions=tuple(body.permissions),
        autonomy_level=body.autonomy_level, owner=body.owner)
    return {"agent_id": rec.agent_id, "version": rec.version, "status": rec.status.value}


@router.get("/runs/{run_id}")
async def get_run(run_id: str,
                  tenant: Any = Depends(_tenant)) -> dict[str, Any]:
    tasks = _svc("tasks")
    task = await tasks.get(tenant.tenant_id, run_id)
    if task is None:
        raise HTTPException(status_code=404, detail={
            "error": {"code": "not_found", "message": f"unknown run {run_id}",
                      "details": {}, "trace_id": ""}})
    transitions = await tasks.transitions(tenant.tenant_id, run_id)
    return {"run_id": task.task_id, "title": task.title,
            "status": task.status.value, "agent_id": task.agent_id,
            "plan_id": task.plan_id, "cost_usd": str(task.cost_usd),
            "steps": task.step_results, "policy_decisions": task.policy_decisions,
            "transitions": [{"from": t.from_status.value, "to": t.to_status.value,
                             "actor": t.actor, "note": t.note,
                             "at": t.at.isoformat()} for t in transitions]}


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, tenant: Any = Depends(_tenant)) -> dict[str, Any]:
    orchestrator = _svc("orchestrator")
    try:
        task = await orchestrator.cancel(tenant, run_id, reason="cancelled via API")
    except KeyError as err:
        raise HTTPException(status_code=404, detail={
            "error": {"code": "not_found", "message": f"unknown run {run_id}",
                      "details": {}, "trace_id": ""}}) from err
    except Exception as exc:
        raise HTTPException(status_code=409, detail={
            "error": {"code": "illegal_transition", "message": str(exc),
                      "details": {}, "trace_id": ""}}) from exc
    return {"run_id": task.task_id, "status": task.status.value}
@router.get("/{agent_id}")
async def get_agent(agent_id: str,
                    tenant: Any = Depends(_tenant)) -> dict[str, Any]:
    registry = _svc("registry")
    rec = await registry.get_record(tenant, agent_id)
    if rec is None:
        raise HTTPException(status_code=404, detail={
            "error": {"code": "not_found", "message": f"unknown agent {agent_id}",
                      "details": {}, "trace_id": ""}})
    versions = await registry.versions(tenant, agent_id)
    return {"agent_id": rec.agent_id, "name": rec.name, "version": rec.version,
            "description": rec.description,
            "capabilities": list(rec.capabilities), "cannot": list(rec.cannot),
            "allowed_tools": list(rec.allowed_tools),
            "model_prefs": rec.model_prefs, "owner": rec.owner,
            "status": rec.status.value, "permissions": list(rec.permissions),
            "autonomy_level": rec.autonomy_level, "budget_ref": rec.budget_ref,
            "approval_policy": rec.approval_policy,
            "version_count": len(versions)}


@router.post("/{agent_id}/versions", status_code=201)
async def publish_version(agent_id: str, body: PublishVersionBody,
                          tenant: Any = Depends(_tenant)) -> dict[str, Any]:
    registry = _svc("registry")
    rec = await registry.publish_version(
        tenant, agent_id,
        description=body.description,
        capabilities=tuple(body.capabilities) if body.capabilities is not None else None,
        allowed_tools=(tuple(body.allowed_tools) if body.allowed_tools is not None
                       else None),
        autonomy_level=body.autonomy_level,
        status=body.status)
    if rec is None:
        raise HTTPException(status_code=404, detail={
            "error": {"code": "not_found", "message": f"unknown agent {agent_id}",
                      "details": {}, "trace_id": ""}}) from None
    return {"agent_id": rec.agent_id, "version": rec.version,
            "status": rec.status.value}


@router.post("/{agent_id}/pause")
async def pause_agent(agent_id: str,
                      tenant: Any = Depends(_tenant)) -> dict[str, Any]:
    registry = _svc("registry")
    rec = await registry.set_status(tenant, agent_id, "paused")
    if rec is None:
        raise HTTPException(status_code=404, detail={
            "error": {"code": "not_found", "message": f"unknown agent {agent_id}",
                      "details": {}, "trace_id": ""}}) from None
    return {"agent_id": agent_id, "status": rec.status.value,
            "version": rec.version}


@router.post("/{agent_id}/resume")
async def resume_agent(agent_id: str,
                       tenant: Any = Depends(_tenant)) -> dict[str, Any]:
    registry = _svc("registry")
    rec = await registry.set_status(tenant, agent_id, "active")
    if rec is None:
        raise HTTPException(status_code=404, detail={
            "error": {"code": "not_found", "message": f"unknown agent {agent_id}",
                      "details": {}, "trace_id": ""}}) from None
    return {"agent_id": agent_id, "status": rec.status.value,
            "version": rec.version}


