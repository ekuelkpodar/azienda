"""Demo 3 — Executive briefing: unified attention queue.

Seeds a realistic morning state (pending approval, failed task, blocked
task, overdue task, budget kill-switch freeze, a policy deny-rate spike),
then runs the real AttentionService (app/api/routers/command_center.py)
over the real stores and prints the exec briefing with evidence links.

Every item links to the underlying record (approval_id / task_id), so the
briefing is auditable, not a narrative.

Run:  cd backend && .venv/bin/python ../scripts/demos/demo3_exec_briefing.py
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _bootstrap import Ctx, Demo  # noqa: E402

from app.api.routers.command_center import AttentionDeps, AttentionService  # noqa: E402
from app.core.contracts import ActionRequest, PolicyEffect  # noqa: E402
from app.core.models import Tenant  # noqa: E402
from app.memory.agrl.ledger import AGRLLedger  # noqa: E402


class TaskAdapter:
    """Maps TaskService tasks onto the shape AttentionService expects."""

    def __init__(self, task_service, tenant) -> None:
        self._svc = task_service
        self._tenant = tenant

    async def list(self, tenant_id: str):
        tasks, _ = await self._svc.list_tasks(self._tenant, limit=100)
        out = []
        for tsk in tasks:
            due = tsk.due_at
            if due is not None and due.tzinfo is None:
                due = due.replace(tzinfo=UTC)
            out.append(SimpleNamespace(
                task_id=str(tsk.id), title=tsk.title, status=tsk.status,
                error="", note=f"priority={tsk.priority}",
                deadline_at=due, updated_at=datetime.now(UTC)))
        return out


async def main() -> bool:
    demo = Demo("3 — Executive briefing: attention across sales/support/tasks/finance")
    ctx = await Ctx().start()
    t = ctx.tenant
    try:
        # -- seed a Tenant row so the budget enforcer has something to freeze --
        ctx.db.add(Tenant(id=t.tenant_id, name="Demo Corp",
                          slug=f"demo-{t.tenant_id[:8]}"))
        await ctx.db.commit()

        # 1. pending approval ------------------------------------------------
        req = ActionRequest(
            tenant=t, action="billing.subscription.change",
            resource="subscription:demo",
            args={"plan": "scale"}, risk_context={"financial_impact_usd": 1200})
        decision = await ctx.policy.evaluate(req)
        approval = await ctx.approvals.request(
            decision, req, risk_score=0.55,
            risk_factors=("financial_impact", "plan_change"))
        await ctx.db.commit()
        demo.step("seeded: pending approval (plan change, $1,200 impact)",
                  approval.status.value == "pending")

        # 2. failed + blocked + overdue tasks ---------------------------------
        failed = await ctx.services.tasks.create_task(
            t, {"title": "Nightly invoice sync", "priority": "high"},
            idempotency_key=f"d3-{uuid.uuid4().hex}")
        await ctx.services.tasks.transition_task(t, str(failed.id), "planning")
        await ctx.services.tasks.transition_task(
            t, str(failed.id), "failed", note="NEXORA seam timeout x3")
        blocked = await ctx.services.tasks.create_task(
            t, {"title": "Q3 board deck — needs CFO numbers", "priority": "high"},
            idempotency_key=f"d3-{uuid.uuid4().hex}")
        await ctx.services.tasks.transition_task(t, str(blocked.id), "planning")
        await ctx.services.tasks.transition_task(
            t, str(blocked.id), "waiting_approval")
        await ctx.services.tasks.transition_task(
            t, str(blocked.id), "blocked", note="waiting on CFO input")
        overdue = await ctx.services.tasks.create_task(
            t, {"title": "Renew SOC 2 auditor contract",
                "due_at": datetime.now(UTC) - timedelta(days=2)},
            idempotency_key=f"d3-{uuid.uuid4().hex}")
        demo.step("seeded: failed + blocked + overdue tasks", True,
                  f"failed={str(failed.id)[:8]} blocked={str(blocked.id)[:8]}")

        # 3. budget kill switch ------------------------------------------------
        await ctx.budgets.kill_switch(t, "demo: anomalous agent spend spike")
        await ctx.db.commit()
        demo.step("seeded: budget kill switch engaged",
                  await ctx.budgets.is_frozen(t))

        # 4. policy deny-rate spike --------------------------------------------
        policy_decisions = (
            [{"action": "comms.message.send", "effect": "deny"} for _ in range(4)]
            + [{"action": "crm.lead.create", "effect": "allow"}]
        )
        demo.step("seeded: policy deny-rate signal (4/5 denies)", True)

        # 5. an AGRL outcome so recent_outcomes is non-empty --------------------
        agrl = AGRLLedger()
        await agrl.append(t, "outcome.recorded", f"task-{failed.id}",
                          {"status": "failed", "cost_usd": 1.42},
                          actor="demo-orchestrator")

        # 6. run the real attention aggregation ---------------------------------
        deps = AttentionDeps(
            approvals=ctx.approvals,
            tasks=TaskAdapter(ctx.services.tasks, t),
            budgets=ctx.budgets,
            audit=ctx.audit,
            agrl=agrl,
            policy_decisions=policy_decisions,
        )
        svc = AttentionService(deps)
        briefing = await svc.attention(t)
        sections = briefing["sections"]

        demo.step("briefing generated",
                  briefing["total_items"] > 0,
                  f"total_items={briefing['total_items']}")
        demo.step("pending approval surfaced with evidence link",
                  any(i["links"].get("approval_id") == approval.approval_id
                      for i in sections["pending_approvals"]),
                  f"{len(sections['pending_approvals'])} pending")
        demo.step("failed + blocked + overdue tasks surfaced",
                  len(sections["failed_tasks"]) == 1
                  and len(sections["blocked_tasks"]) == 1
                  and len(sections["overdue_tasks"]) == 1)
        demo.step("budget freeze surfaced as critical",
                  any(i["severity"] == "critical"
                      for i in sections["budget_alerts"]))
        demo.step("policy deny-rate anomaly surfaced",
                  len(sections["anomalies"]) == 1,
                  sections["anomalies"][0]["title"] if sections["anomalies"] else "")
        demo.step("recent outcome surfaced from AGRL",
                  len(sections["recent_outcomes"]) == 1)

        print("\n  ---- EXEC BRIEFING -----------------------------------")
        for name, items in sections.items():
            if not items:
                continue
            print(f"  {name} ({len(items)}):")
            for i in items:
                link = ", ".join(f"{k}={str(v)[:8]}"
                                 for k, v in i["links"].items())
                print(f"    [{i['severity']}] {i['title']}"
                      + (f"  <{link}>" if link else ""))
        print("  ------------------------------------------------------")
        await ctx.audit_step("demo-agent", "demo.briefing.generated",
                             {"total_items": briefing["total_items"]})
        ok_chain = await ctx.audit.verify_chain(t)
        demo.step("audit chain verifies", ok_chain)
        return demo.verdict()
    finally:
        await ctx.stop()


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(main()) else 1)
