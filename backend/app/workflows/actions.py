"""Built-in ACTION node catalog.

Actions are small, auditable, policy-gated writes the workflow engine can
perform through its ports. Each action is pure orchestration glue over
LeadPort/TaskPort — no business logic duplicated here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.contracts import TenantContext

from .conditions import render
from .ports import LeadPort, TaskPort


class ActionError(Exception):
    pass


OUTREACH_DRAFT_TEMPLATE = (
    "Subject: Quick question about {lead_company}\n\n"
    "Hi {lead_name},\n\n"
    "Noticed {lead_company} is in {lead_industry}. Teams like yours use Azienda "
    "to put routine follow-ups on autopilot while keeping a human in the loop "
    "for every consequential step.\n\n"
    "Worth a 15-minute look this week?\n\n"
    "— {sender_name}\n"
    "[DRAFT — pending human approval]"
)


async def run_action(
    name: str,
    params: dict[str, Any],
    context: dict[str, Any],
    tenant: TenantContext,
    lead_port: LeadPort | None,
    task_port: TaskPort | None,
) -> dict[str, Any]:
    """Execute a catalog action. Raises ActionError on misuse."""
    p = render(params, context)
    handler = _CATALOG.get(name)
    if handler is None:
        raise ActionError(f"unknown action '{name}'")
    return await handler(p, context, tenant, lead_port, task_port)


def _need(port: Any, name: str, action: str) -> Any:
    if port is None:
        raise ActionError(f"action '{action}' requires {name}, which is not configured")
    return port


async def _research_stub(params, context, tenant, lead_port, task_port):
    # Explicit stub: external research providers are not wired yet. The node
    # records what WOULD be researched so the workflow stays honest.
    lead_id = params.get("lead_id")
    return {
        "status": "stub",
        "note": "external research provider not configured; continuing with CRM record only",
        "lead_id": lead_id,
        "would_research": ["company website", "recent news", "tech stack", "key contacts"],
    }


async def _lead_set_status(params, context, tenant, lead_port, task_port):
    port = _need(lead_port, "LeadPort", "crm.lead.set_status")
    lead_id = params.get("lead_id") or (context.get("lead") or {}).get("id")
    if not lead_id:
        raise ActionError("crm.lead.set_status requires params.lead_id")
    await port.set_status(tenant, str(lead_id), str(params["status"]))
    return {"lead_id": str(lead_id), "status": params["status"]}


async def _lead_rescore(params, context, tenant, lead_port, task_port):
    port = _need(lead_port, "LeadPort", "crm.lead.rescore")
    lead_id = params.get("lead_id") or (context.get("lead") or {}).get("id")
    if not lead_id:
        raise ActionError("crm.lead.rescore requires params.lead_id")
    score, breakdown = await port.rescore_lead(tenant, str(lead_id))
    return {"lead_id": str(lead_id), "score": score, "breakdown": breakdown}


async def _activity_log(params, context, tenant, lead_port, task_port):
    port = _need(lead_port, "LeadPort", "crm.activity.log")
    activity_id = await port.log_activity(
        tenant,
        subject_type=params["subject_type"],
        subject_id=str(params["subject_id"]),
        type=params.get("type", "note"),
        body=params.get("body"),
    )
    return {"activity_id": activity_id}


async def _task_create(params, context, tenant, lead_port, task_port):
    port = _need(task_port, "TaskPort", "task.create")
    due_at = None
    if params.get("due_in_days") is not None:
        due_at = (datetime.now(UTC) + timedelta(days=int(params["due_in_days"]))).isoformat()
    task = await port.create_task(
        tenant,
        title=str(params["title"]),
        description=params.get("description"),
        priority=params.get("priority", "medium"),
        due_at=due_at,
        idempotency_key=params.get("idempotency_key"),
    )
    return {"task_id": task["id"], "title": task["title"]}


async def _outreach_draft(params, context, tenant, lead_port, task_port):
    port = _need(lead_port, "LeadPort", "outreach.draft")
    lead = context.get("lead") or {}
    draft = render(params.get("template", OUTREACH_DRAFT_TEMPLATE), {
        **context,
        "lead_name": lead.get("contact_name") or "there",
        "lead_company": lead.get("org_name") or "your company",
        "lead_industry": lead.get("org_industry") or "your industry",
        "sender_name": params.get("sender_name", "the Azienda team"),
    })
    lead_id = params.get("lead_id") or lead.get("id")
    activity_id = None
    if lead_id:
        activity_id = await port.log_activity(
            tenant, subject_type="lead", subject_id=str(lead_id),
            type="outreach_draft", body=draft)
    lead_ref = str(lead_id) if lead_id else None
    return {"draft": draft, "activity_id": activity_id, "lead_id": lead_ref}


_CATALOG = {
    "research_stub": _research_stub,
    "crm.lead.set_status": _lead_set_status,
    "crm.lead.rescore": _lead_rescore,
    "crm.activity.log": _activity_log,
    "task.create": _task_create,
    "outreach.draft": _outreach_draft,
}

ACTION_CATALOG = tuple(sorted(_CATALOG))
