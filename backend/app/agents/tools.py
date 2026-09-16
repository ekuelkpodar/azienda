"""Tool registry and policy-gated executor.

Every tool call passes ``PolicyEngine.evaluate`` BEFORE the handler runs —
there is no code path around it (fail closed: policy errors are treated as
DENY by the caller-provided engine). Handlers are plain functions behind the
``ToolHandler`` protocol; the in-memory domain adapters below are dev/test
adapters — production binds them to the crm/tasks/workflows/comms packages
via ``core/contracts.py`` protocols.

Internal tools are registered per tenant by ``seed_internal_tools``.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, TypedDict

from app.core import contracts

ToolHandler = Callable[[contracts.TenantContext, dict[str, Any]], Awaitable[dict[str, Any]]]


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class RegisteredTool:
    spec: contracts.ToolSpec
    provider: str = "internal"          # "internal" | "mcp:<server>"
    permissions: tuple[str, ...] = ()
    rate_limit_per_min: int = 60
    cost_usd: Decimal = Decimal("0")
    handler: ToolHandler | None = None
    auth: str = "scoped-grant"          # per-action grant; never ambient


# ---------------------------------------------------------------------------
# JSON-schema validation (small, honest subset — required/type/enum)
# ---------------------------------------------------------------------------
_TYPEMAP: dict[str, type | tuple[type, ...]] = {
    "string": str, "integer": int, "number": (int, float), "boolean": bool,
    "array": list, "object": dict}


def validate_args(schema: dict[str, Any], args: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(args, dict):
        return ["arguments must be an object"]
    for name in schema.get("required", []):
        if name not in args:
            errors.append(f"missing required argument: {name}")
    props = schema.get("properties", {})
    for name, value in args.items():
        prop = props.get(name)
        if not prop:
            continue  # additionalProperties allowed unless schema says otherwise
        want = prop.get("type")
        if want and not isinstance(value, _TYPEMAP.get(want, object)):
            # allow int where number is wanted is covered by typemap tuple
            errors.append(f"argument '{name}' must be {want}, got {type(value).__name__}")
        if "enum" in prop and value not in prop["enum"]:
            errors.append(f"argument '{name}' must be one of {prop['enum']}")
    if schema.get("additionalProperties") is False:
        for name in args:
            if name not in props:
                errors.append(f"unknown argument: {name}")
    return errors


# ---------------------------------------------------------------------------
# In-memory domain adapters (DEV/TEST ONLY — production binds real packages)
# ---------------------------------------------------------------------------
class _InMemoryCRM:
    """Stands in for the crm package until its service is wired.

    DEV/TEST ONLY. State is keyed by tenant_id — cross-tenant invisibility
    is enforced even in the fake.
    """

    def __init__(self) -> None:
        self._by_tenant: dict[str, dict[str, list[dict[str, Any]]]] = {}

    def _state(self, t: contracts.TenantContext) -> dict[str, list[dict[str, Any]]]:
        return self._by_tenant.setdefault(t.tenant_id, {
            "contacts": [
                {"id": "c-001", "first_name": "Ada", "last_name": "Okafor",
                 "email": "ada@example.com", "org": "Acme Logistics"},
                {"id": "c-002", "first_name": "Ben", "last_name": "Diallo",
                 "email": "ben@example.com", "org": "Northwind Traders"},
            ],
            "opportunities": [],
            "activities": [],
        })

    async def search_contacts(self, t: contracts.TenantContext,
                              args: dict[str, Any]) -> dict[str, Any]:
        q = str(args.get("query", "")).lower()
        contacts = self._state(t)["contacts"]
        hits = [c for c in contacts
                if q in f"{c.get('first_name', '')} {c.get('last_name', '')} "
                        f"{c.get('email', '')} {c.get('org', '')}".lower()]
        return {"contacts": hits, "count": len(hits)}

    async def create_contact(self, t: contracts.TenantContext,
                             args: dict[str, Any]) -> dict[str, Any]:
        contact = {"id": f"c-{uuid.uuid4().hex[:8]}", **{k: v for k, v in args.items()
                                                         if k in ("first_name", "last_name",
                                                                  "email", "phone", "title")}}
        self._state(t)["contacts"].append(contact)
        return {"contact": contact}

    async def create_opportunity(self, t: contracts.TenantContext,
                                 args: dict[str, Any]) -> dict[str, Any]:
        opp = {"id": f"o-{uuid.uuid4().hex[:8]}", "stage": "prospecting", **args}
        self._state(t)["opportunities"].append(opp)
        return {"opportunity": opp}

    async def log_activity(self, t: contracts.TenantContext,
                           args: dict[str, Any]) -> dict[str, Any]:
        act = {"id": f"a-{uuid.uuid4().hex[:8]}", "occurred_at": _utcnow().isoformat(),
               **args}
        self._state(t)["activities"].append(act)
        return {"activity": act}


class _InMemoryTasks:
    """Stands in for the tasks package until its service is wired. Tenant-scoped."""

    def __init__(self) -> None:
        self._by_tenant: dict[str, dict[str, dict[str, Any]]] = {}

    async def create_task(self, t: contracts.TenantContext,
                          args: dict[str, Any]) -> dict[str, Any]:
        task = {"id": f"t-{uuid.uuid4().hex[:8]}", "status": "pending", **args}
        self._by_tenant.setdefault(t.tenant_id, {})[task["id"]] = task
        return {"task": task}

    async def transition_task(self, t: contracts.TenantContext,
                              args: dict[str, Any]) -> dict[str, Any]:
        task = self._by_tenant.get(t.tenant_id, {}).get(str(args["task_id"]))
        if task is None:
            raise KeyError(f"unknown task {args['task_id']}")
        task["status"] = args["to"]
        return {"task": task}


class _InMemoryComms:
    """Stands in for the comms package until its service is wired. Tenant-scoped."""

    def __init__(self) -> None:
        self._sent: dict[str, list[dict[str, Any]]] = {}

    def sent_for(self, tenant_id: str) -> list[dict[str, Any]]:
        return list(self._sent.get(tenant_id, []))

    async def send_email(self, t: contracts.TenantContext,
                         args: dict[str, Any]) -> dict[str, Any]:
        msg = {"id": f"m-{uuid.uuid4().hex[:8]}", "kind": "email",
               "status": "queued", **args}
        self._sent.setdefault(t.tenant_id, []).append(msg)
        return {"message": msg}

    async def send_bulk_sms(self, t: contracts.TenantContext,
                            args: dict[str, Any]) -> dict[str, Any]:
        # NOTE: reachable only if policy ALLOWS — the executor gates this.
        batch = {"id": f"b-{uuid.uuid4().hex[:8]}", "kind": "bulk_sms",
                 "recipients": len(args.get("to", [])), "status": "queued"}
        self._sent.setdefault(t.tenant_id, []).append(batch)
        return {"batch": batch}


class _InMemoryWorkflows:
    """Stands in for the workflows package until its WorkflowBackend is wired."""

    async def start_execution(self, t: contracts.TenantContext,
                              args: dict[str, Any]) -> dict[str, Any]:
        return {"execution_id": f"ex-{uuid.uuid4().hex[:8]}", "status": "running",
                "definition_id": args.get("definition_id")}


# ---------------------------------------------------------------------------
# Tool specifications for the internal suite
# ---------------------------------------------------------------------------
def _spec(name: str, description: str, schema: dict[str, Any], risk_tier: str,
          idempotent: bool = False) -> contracts.ToolSpec:
    return contracts.ToolSpec(name=name, description=description, input_schema=schema,
                              risk_tier=risk_tier, idempotent=idempotent)


INTERNAL_TOOL_DEFS: tuple[tuple[contracts.ToolSpec, str, Decimal, int], ...] = (
    (_spec("crm.search_contacts", "Search contacts by name/email/org.",
           {"type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"]}, "low"), "crm:read", Decimal("0.001"), 120),
    (_spec("crm.create_contact", "Create a contact.",
           {"type": "object",
            "properties": {"first_name": {"type": "string"},
                           "last_name": {"type": "string"},
                           "email": {"type": "string"}, "phone": {"type": "string"},
                           "title": {"type": "string"}},
            "required": ["first_name", "last_name"]}, "medium"), "crm:write",
     Decimal("0.002"), 60),
    (_spec("crm.create_opportunity", "Create an opportunity.",
           {"type": "object",
            "properties": {"name": {"type": "string"}, "amount": {"type": "number"},
                           "org_id": {"type": "string"}},
            "required": ["name"]}, "medium"), "crm:write", Decimal("0.002"), 60),
    (_spec("crm.log_activity", "Log an activity against a subject.",
           {"type": "object",
            "properties": {"subject_type": {"type": "string"},
                           "subject_id": {"type": "string"},
                           "type": {"type": "string"}, "body": {"type": "string"}},
            "required": ["subject_type", "subject_id", "type"]}, "low"),
     "crm:write", Decimal("0.001"), 120),
    (_spec("tasks.create_task", "Create a task.",
           {"type": "object",
            "properties": {"title": {"type": "string"},
                           "description": {"type": "string"},
                           "priority": {"type": "string",
                                        "enum": ["low", "normal", "high", "urgent"]}},
            "required": ["title"]}, "low"), "tasks:write", Decimal("0.001"), 120),
    (_spec("tasks.transition_task", "Transition a task's state.",
           {"type": "object",
            "properties": {"task_id": {"type": "string"},
                           "to": {"type": "string"}},
            "required": ["task_id", "to"]}, "medium"), "tasks:write",
     Decimal("0.001"), 120),
    (_spec("workflows.start_execution", "Start a workflow execution.",
           {"type": "object",
            "properties": {"definition_id": {"type": "string"},
                           "input": {"type": "object"}},
            "required": ["definition_id"]}, "medium"), "workflows:execute",
     Decimal("0.01"), 30),
    (_spec("comms.send_email", "Send a single email. Requires approval by policy.",
           {"type": "object",
            "properties": {"to": {"type": "string"}, "subject": {"type": "string"},
                           "body": {"type": "string"}},
            "required": ["to", "subject", "body"]}, "medium"), "comms:send_single",
     Decimal("0.02"), 30),
    (_spec("comms.send_bulk_sms", "Send SMS to many recipients. High-risk bulk action.",
           {"type": "object",
            "properties": {"to": {"type": "array"}, "body": {"type": "string"}},
            "required": ["to", "body"]}, "critical"), "comms:send_bulk",
     Decimal("0.05"), 10),
    (_spec("knowledge.search", "Hybrid search over the knowledge base.",
           {"type": "object",
            "properties": {"query": {"type": "string"}, "top_k": {"type": "integer"}},
            "required": ["query"]}, "low"), "knowledge:read", Decimal("0.005"), 120),
    (_spec("memory.store", "Store a namespaced memory value.",
           {"type": "object",
            "properties": {"namespace": {"type": "string"}, "key": {"type": "string"},
                           "value": {"type": "object"}},
            "required": ["namespace", "key", "value"]}, "low"), "memory:write",
     Decimal("0.0005"), 120),
    (_spec("memory.recall", "Recall a namespaced memory value.",
           {"type": "object",
            "properties": {"namespace": {"type": "string"}, "key": {"type": "string"}},
            "required": ["namespace", "key"]}, "low"), "memory:read",
     Decimal("0.0005"), 120),
)


class _Adapters(TypedDict):
    """The dev/test domain adapters behind the internal tool suite."""
    crm: _InMemoryCRM
    tasks: _InMemoryTasks
    comms: _InMemoryComms
    workflows: _InMemoryWorkflows


class ToolRegistry:
    """In-memory ToolRegistry (dev/test adapter). Production: SQLAlchemy repo."""

    def __init__(self) -> None:
        # tenant_id -> name -> RegisteredTool
        self._tools: dict[str, dict[str, RegisteredTool]] = {}
        self.adapters: _Adapters = {
            "crm": _InMemoryCRM(),
            "tasks": _InMemoryTasks(),
            "comms": _InMemoryComms(),
            "workflows": _InMemoryWorkflows(),
        }

    # -- contracts.ToolRegistry ------------------------------------------------
    async def get(self, tenant: contracts.TenantContext,
                  name: str) -> contracts.ToolSpec | None:
        tool = self._tools.get(tenant.tenant_id, {}).get(name)
        return tool.spec if tool else None

    async def list(self, tenant: contracts.TenantContext,
                   capability: str | None = None) -> list[contracts.ToolSpec]:
        tools = list(self._tools.get(tenant.tenant_id, {}).values())
        if capability:
            tools = [t for t in tools
                     if capability in t.spec.name or capability in t.spec.description.lower()]
        return [t.spec for t in tools]

    # -- management --------------------------------------------------------------
    async def register(self, tenant: contracts.TenantContext, tool: RegisteredTool) -> None:
        self._tools.setdefault(tenant.tenant_id, {})[tool.spec.name] = tool

    async def get_registered(self, tenant: contracts.TenantContext,
                             name: str) -> RegisteredTool | None:
        return self._tools.get(tenant.tenant_id, {}).get(name)

    async def seed_internal_tools(self, tenant: contracts.TenantContext,
                                  knowledge_search: ToolHandler | None = None,
                                  memory_store: ToolHandler | None = None,
                                  memory_recall: ToolHandler | None = None) -> None:
        """Register the internal tool suite for a tenant. Idempotent."""
        crm, tasks, comms, workflows = (self.adapters["crm"], self.adapters["tasks"],
                                       self.adapters["comms"], self.adapters["workflows"])
        handlers: dict[str, ToolHandler] = {
            "crm.search_contacts": crm.search_contacts,
            "crm.create_contact": crm.create_contact,
            "crm.create_opportunity": crm.create_opportunity,
            "crm.log_activity": crm.log_activity,
            "tasks.create_task": tasks.create_task,
            "tasks.transition_task": tasks.transition_task,
            "workflows.start_execution": workflows.start_execution,
            "comms.send_email": comms.send_email,
            "comms.send_bulk_sms": comms.send_bulk_sms,
        }
        if knowledge_search:
            handlers["knowledge.search"] = knowledge_search
        if memory_store:
            handlers["memory.store"] = memory_store
        if memory_recall:
            handlers["memory.recall"] = memory_recall
        for spec, permission, cost, rate in INTERNAL_TOOL_DEFS:
            handler = handlers.get(spec.name)
            await self.register(tenant, RegisteredTool(
                spec=spec, provider="internal", permissions=(permission,),
                rate_limit_per_min=rate, cost_usd=cost, handler=handler))


class ToolExecutor:
    """Executes tool calls. Policy evaluation is NON-BYPASSABLE.

    Order: lookup → schema validation → grant check → POLICY EVALUATE →
    (deny | require_approval | allow → idempotency → handler → cost → audit).
    """

    def __init__(self, *, policy: contracts.PolicyEngine,
                 approvals: contracts.ApprovalStore,
                 audit: contracts.AuditLedger,
                 registry: ToolRegistry,
                 costs: contracts.CostRecorder | None = None,
                 step_timeout_seconds: float = 60.0) -> None:
        self._policy = policy
        self._approvals = approvals
        self._audit = audit
        self._registry = registry
        self._costs = costs
        self._step_timeout = step_timeout_seconds
        self._idempotency: dict[str, contracts.ToolResult] = {}
        self._rate: dict[tuple[str, str], list[float]] = {}

    async def execute(self, tenant: contracts.TenantContext,
                      call: contracts.ToolCall) -> contracts.ToolResult:
        tool = await self._registry.get_registered(tenant, call.tool_name)
        if tool is None:
            return contracts.ToolResult(ok=False, error=f"unknown_tool: {call.tool_name}")
        if tool.handler is None:
            return contracts.ToolResult(ok=False,
                                        error=f"tool_not_bound: {call.tool_name}")

        schema_errors = validate_args(tool.spec.input_schema, call.arguments)
        if schema_errors:
            return contracts.ToolResult(ok=False,
                                        error="schema_validation: " + "; ".join(schema_errors))
        if not call.grant_id:
            await self._audit.append(tenant, actor="tool-executor",
                                     action="tool.rejected_no_grant",
                                     payload={"tool": call.tool_name})
            return contracts.ToolResult(ok=False, error="missing_grant: no scoped grant")

        # ---- POLICY CHECK: the non-bypassable gate ----
        request = contracts.ActionRequest(
            tenant=tenant, action=f"tool.{call.tool_name}",
            resource=None, args=call.arguments,
            risk_context={"risk_tier": tool.spec.risk_tier,
                          "grant_id": call.grant_id,
                          "provider": tool.provider},
            idempotency_key=call.idempotency_key)
        try:
            decision = await self._policy.evaluate(request)
        except Exception as exc:  # fail closed: evaluation failure == DENY
            await self._audit.append(tenant, actor="tool-executor",
                                     action="tool.policy_error",
                                     payload={"tool": call.tool_name, "error": str(exc)})
            return contracts.ToolResult(ok=False, error=f"policy_error_deny: {exc}")

        await self._audit.append(tenant, actor="tool-executor", action="tool.policy_decision",
                                 payload={"tool": call.tool_name,
                                          "effect": decision.effect.value,
                                          "policy_id": decision.policy_id,
                                          "reasons": list(decision.reasons)})

        if decision.effect == contracts.PolicyEffect.DENY:
            return contracts.ToolResult(
                ok=False, error="policy_denied: " + "; ".join(decision.reasons))

        if decision.effect == contracts.PolicyEffect.REQUIRE_APPROVAL:
            approval = await self._approvals.request(decision, request,
                                                       risk_score=70.0,
                                                       risk_factors=(tool.spec.risk_tier,))
            return contracts.ToolResult(
                ok=False, error="approval_required",
                output={"approval_id": approval.approval_id,
                        "status": "waiting_approval"})

        # ---- ALLOW path ----
        if call.idempotency_key and call.idempotency_key in self._idempotency:
            return self._idempotency[call.idempotency_key]
        if not self._check_rate(tenant.tenant_id, call.tool_name, tool.rate_limit_per_min):
            return contracts.ToolResult(ok=False, error="rate_limited")

        started = time.monotonic()
        try:
            assert tool.handler is not None
            output = await asyncio.wait_for(tool.handler(tenant, call.arguments),
                                            timeout=self._step_timeout)
        except TimeoutError:
            await self._audit.append(tenant, actor="tool-executor", action="tool.timeout",
                                     payload={"tool": call.tool_name})
            return contracts.ToolResult(ok=False, error="tool_timeout")
        except Exception as exc:
            await self._audit.append(tenant, actor="tool-executor", action="tool.failed",
                                     payload={"tool": call.tool_name, "error": str(exc)})
            return contracts.ToolResult(ok=False, error=f"tool_error: {exc}")

        latency_ms = int((time.monotonic() - started) * 1000)
        result = contracts.ToolResult(ok=True, output=output, cost_usd=tool.cost_usd)
        if call.idempotency_key:
            self._idempotency[call.idempotency_key] = result
        if self._costs is not None:
            await self._costs.record(contracts.CostRecord(
                tenant_id=tenant.tenant_id, task_id=None, model=f"tool:{call.tool_name}",
                input_tokens=0, output_tokens=0, cost_usd=tool.cost_usd,
                credits_drawn=tool.cost_usd, recorded_at=_utcnow()))
        await self._audit.append(tenant, actor="tool-executor", action="tool.called",
                                 payload={"tool": call.tool_name, "grant_id": call.grant_id,
                                          "latency_ms": latency_ms,
                                          "cost_usd": str(tool.cost_usd)})
        return result

    def _check_rate(self, tenant_id: str, tool_name: str, limit: int) -> bool:
        now = time.monotonic()
        key = (tenant_id, tool_name)
        window = [t for t in self._rate.get(key, []) if now - t < 60.0]
        if len(window) >= limit:
            return False
        window.append(now)
        self._rate[key] = window
        return True


def new_grant_id() -> str:
    """Mint a per-action scoped grant id. No ambient authority."""
    return f"grant-{uuid.uuid4().hex[:16]}"
