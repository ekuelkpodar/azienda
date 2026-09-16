"""Demo 1 — New lead pipeline with governed outreach.

Flow: lead created -> research note -> transparent score -> convert to
org+contact+opportunity -> outreach draft (jinja template) -> policy says
REQUIRE_APPROVAL -> approval requested -> human approves -> scoped grant ->
message sent (log_only provider: recorded, nothing transmitted) ->
follow-up task -> audit trail printed + chain verified.

No LLM anywhere: scoring is the repo's rule-based scorer, the draft is a
jinja template render, the "human" is the demo operator step.
Run:  cd backend && .venv/bin/python ../scripts/demos/demo1_new_lead.py
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _bootstrap import Ctx, Demo  # noqa: E402

from app.comms import schemas as S  # noqa: E402
from app.comms.service import ApprovalRequiredError, CommsService  # noqa: E402
from app.core.contracts import ActionRequest, PolicyEffect  # noqa: E402


async def main() -> bool:
    demo = Demo("1 — New lead -> scored -> converted -> approval-gated outreach")
    ctx = await Ctx().start()
    t = ctx.tenant
    try:
        # Approval-gated sends; everything else auto-allowed.
        ctx.policy.approval_prefixes = ("comms.message.send",)
        grants: set[str] = set()  # scoped grants issued by human approvals

        orig_evaluate = ctx.policy.evaluate

        async def evaluate_with_grants(request):
            if request.action in grants:
                from app.core.contracts import PolicyDecision
                return PolicyDecision(
                    effect=PolicyEffect.ALLOW, policy_id="demo-grant",
                    reasons=("scoped grant from approved approval",))
            return await orig_evaluate(request)

        ctx.policy.evaluate = evaluate_with_grants  # type: ignore[method-assign]
        comms = CommsService(policy=ctx.policy, events=ctx.bus)

        # 1. lead arrives -------------------------------------------------
        lead = await ctx.services.crm.create_lead(
            t, {"source": "website", "status": "new"},
            idempotency_key=f"demo1-lead-{uuid.uuid4().hex}")
        await ctx.audit_step("demo-agent", "demo.lead.created",
                             {"lead_id": str(lead.id), "source": "website"})
        demo.step("lead created", lead.id is not None and lead.status == "new",
                  f"lead_id={lead.id}")

        # 2. research note ------------------------------------------------
        contact = await ctx.services.crm.create_contact(
            t, {"first_name": "Maya", "last_name": "Okafor",
                "email": "maya@northwind.example", "title": "Ops Director",
                "phone": "+1-404-555-0132"})
        await ctx.services.crm.update_lead(
            t, str(lead.id), {"contact_id": str(contact.id)})
        await ctx.services.crm.log_activity(
            t, "lead", str(lead.id), "research_note",
            "Northwind Traders: 120-person 3PL, hiring ops roles, "
            "runs on spreadsheets + QuickBooks. Website contact form.")
        await ctx.audit_step("demo-agent", "demo.lead.researched",
                             {"lead_id": str(lead.id)})
        demo.step("research note logged", True, "activity on lead")

        # 3. transparent score -------------------------------------------
        score, breakdown = await ctx.services.crm.score_lead(t, str(lead.id))
        await ctx.audit_step("demo-agent", "demo.lead.scored",
                             {"lead_id": str(lead.id), "score": score})
        demo.step("lead scored (rule-based, transparent)",
                  isinstance(score, int) and 0 <= score <= 100,
                  f"score={score} breakdown_keys={sorted(breakdown)[:4]}")

        # 4. convert -------------------------------------------------------
        await ctx.services.crm.create_pipeline(
            t, "Sales Pipeline", "opportunity",
            [{"name": "New"}, {"name": "Qualified"}, {"name": "Proposal"},
             {"name": "Won", "is_closed_won": True},
             {"name": "Lost", "is_closed_lost": True}],
            idempotency_key=f"demo1-pipe-{uuid.uuid4().hex}")
        org, conv_contact, opp = await ctx.services.crm.convert_lead(
            t, str(lead.id), idempotency_key=f"demo1-conv-{uuid.uuid4().hex}")
        await ctx.audit_step("demo-agent", "demo.lead.converted",
                             {"org": org.name, "opportunity": opp.name})
        demo.step("lead converted -> org+contact+opportunity",
                  org.id is not None and opp.id is not None,
                  f"org={org.name!r} opp={opp.name!r}")

        # 5. outreach draft (jinja template, deterministic) ----------------
        channel = await comms.create_channel(
            t, S.ChannelCreate(kind=S.ChannelKind.EMAIL, name="demo-outreach",
                               provider="log_only", config={}))
        template = await comms.create_template(
            t, S.MessageTemplateCreate(
                name="sdr-intro", kind=S.ChannelKind.EMAIL,
                body=("Hi {{ first_name }} — saw {{ org_name }} is scaling ops. "
                      "Azienda runs outreach + follow-up under policy; "
                      "worth 15 min? (lead score {{ score }}/100)")))
        draft = await comms.render_template(
            t, template.id,
            {"first_name": "Maya", "org_name": org.name, "score": score})
        demo.step("outreach draft rendered (jinja, no LLM)",
                  "Maya" in draft and str(score) in draft, draft[:70] + "...")

        # 6. policy check -> REQUIRE_APPROVAL -------------------------------
        req = ActionRequest(
            tenant=t, action="comms.message.send",
            resource=f"channel:{channel.id}",
            args={"channel_kind": "email", "to_address": contact.email},
            risk_context={"externally_visible": True, "reversible": False})
        decision = await ctx.policy.evaluate(req)
        demo.step("policy evaluated: REQUIRE_APPROVAL (externally visible)",
                  decision.effect == PolicyEffect.REQUIRE_APPROVAL,
                  f"effect={decision.effect.value}")

        # 7. approval requested -> the human queue ---------------------------
        approval = await ctx.approvals.request(
            decision, req, risk_score=0.35,
            risk_factors=("externally_visible", "first_touch"))
        await ctx.db.commit()
        pending, pending_total = await ctx.approvals.list_pending(t)
        await ctx.audit_step("demo-agent", "demo.approval.requested",
                             {"approval_id": approval.approval_id})
        demo.step("approval requested (pending in human queue)",
                  approval.status.value == "pending" and pending_total == 1
                  and any(a.approval_id == approval.approval_id for a in pending),
                  f"approval_id={approval.approval_id}")

        # 8. send blocked while approval pending (fail closed) ----------------
        try:
            await comms.send(
                t, S.MessageSend(channel_kind=S.ChannelKind.EMAIL,
                                 to_address=contact.email, body=draft,
                                 subject="Quick intro"))
            blocked = False
        except ApprovalRequiredError:
            blocked = True
        demo.step("send blocked before approval (fail closed)", blocked)

        # 9. human approves -> scoped grant ----------------------------------
        decided = await ctx.approvals.decide(
            t, approval.approval_id, approved=True,
            note="demo operator: copy reviewed, on-brand")
        await ctx.db.commit()
        await ctx.audit_step("demo-operator", "demo.approval.decided",
                             {"approval_id": approval.approval_id,
                              "approved": True})
        grants.add("comms.message.send")  # scoped, single-action grant
        demo.step("human approved -> scoped grant issued",
                  decided.status.value == "approved")

        # 10. logged send ------------------------------------------------------
        msg = await comms.send(
            t, S.MessageSend(channel_kind=S.ChannelKind.EMAIL,
                             to_address=contact.email, body=draft,
                             subject="Quick intro",
                             idempotency_key=f"demo1-send-{uuid.uuid4().hex}"))
        await ctx.db.commit()
        await ctx.audit_step("demo-agent", "demo.message.sent",
                             {"message_id": msg.id,
                              "provider": msg.provider})
        demo.step("message sent (log_only provider — recorded, not transmitted)",
                  msg.status == S.MessageStatus.SENT
                  and msg.provider_message_id.startswith("log-"),
                  f"provider_msg={msg.provider_message_id}")

        # 11. follow-up task ----------------------------------------------------
        task = await ctx.services.tasks.create_task(
            t, {"title": f"Follow up with {org.name}",
                "description": f"Intro email sent to {contact.email}; "
                               f"call in 3 business days if no reply.",
                "priority": "high"},
            idempotency_key=f"demo1-task-{uuid.uuid4().hex}")
        await ctx.audit_step("demo-agent", "demo.task.created",
                             {"task_id": str(task.id)})
        demo.step("follow-up task created",
                  task.status == "pending", f"task_id={task.id}")

        # 12. audit trail -------------------------------------------------------
        ok_chain = await ctx.audit.verify_chain(t)
        entries, total = await ctx.audit.list_entries(t, limit=20)
        demo.step("audit chain verifies", ok_chain,
                  f"{total} demo entries, hash-chained")
        print("\n  audit trail (seq | actor | action):")
        for e in sorted(entries, key=lambda x: x.seq):
            print(f"    {e.seq:>3} | {e.actor:<14} | {e.action}")
        return demo.verdict()
    finally:
        await ctx.stop()


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(main()) else 1)
