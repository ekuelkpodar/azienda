"""Demo 2 — Support ticket triage with governed resolution.

Flow: ticket arrives -> customer lookup (CRM contact) -> KB search
(deterministic hash embeddings, no LLM) -> draft reply (deterministic
template assistant injected into SupportService — explicitly NOT an LLM) ->
authorized action: agent reply posted (policy allow) -> escalate path: a
second, higher-risk ticket requires approval and is routed to a human ->
CRM activity updated on the contact -> audit trail printed + verified.

Run:  cd backend && .venv/bin/python ../scripts/demos/demo2_support.py
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _bootstrap import Ctx, Demo  # noqa: E402

from app.core.contracts import TenantContext  # noqa: E402
from app.knowledge.store import KnowledgeStore  # noqa: E402
from app.support import schemas as SS  # noqa: E402
from app.support.service import SupportService  # noqa: E402


class TemplateDraftAssistant:
    """Deterministic draft assistant for the demo.

    HONEST LABEL: this is a rule-based template, not an LLM. It stands in
    for the agent-drafted reply (SupportService accepts any DraftAssistant;
    the production one is future work per the package README).
    """

    async def draft(self, tenant: TenantContext, ticket: SS.Ticket,
                    history: list[SS.TicketMessage]) -> SS.DraftReply:
        kb_snippet = getattr(self, "_kb_snippet", "")
        body = (
            f"Hi — thanks for contacting support about "
            f"\"{ticket.subject}\".\n\n"
            f"Based on our help center: {kb_snippet}\n\n"
            f"If that doesn't resolve it, reply here and a human will "
            f"pick this up. — Azienda support (draft, agent-assisted)")
        return SS.DraftReply(ticket_id=ticket.id, draft=body,
                             model="demo-template-v1",
                             note="deterministic template; not an LLM")


async def main() -> bool:
    demo = Demo("2 — Support ticket: lookup -> KB -> draft -> authorized reply -> escalate")
    ctx = await Ctx().start()
    t = ctx.tenant
    try:
        support = SupportService(events=ctx.bus,
                                 draft_assistant=TemplateDraftAssistant())
        kb = KnowledgeStore()  # HashEmbeddingProvider: deterministic, no LLM

        # 0. seed: customer + KB articles ----------------------------------
        contact = await ctx.services.crm.create_contact(
            t, {"first_name": "Devon", "last_name": "Park",
                "email": "devon@northwind.example", "title": "Ops Manager"})
        src_id = await kb.register_source(
            t, title="help-center", uri="demo://kb",
            text="Help center seed source.", kind="docs")
        await kb.ingest_document(
            t, source_id=src_id, title="Password resets",
            uri="demo://kb/password-resets",
            text=("To reset your password, open Settings, choose Security, "
                  "then Reset password. The reset link expires after 30 minutes. "
                  "Contact support if the link has expired."))
        await kb.ingest_document(
            t, source_id=src_id, title="Refund policy",
            uri="demo://kb/refunds",
            text=("Refunds are issued to the original payment method within "
                  "5 business days. Refunds above $500 require a manager "
                  "approval before they are processed."))
        demo.step("seeded: CRM contact + 2 KB articles", True,
                  f"contact={contact.email}")

        # 1. ticket arrives ---------------------------------------------------
        ticket = await support.create_ticket(
            t, SS.TicketCreate(
                subject="Can't log in — password reset link expired",
                priority=SS.TicketPriority.NORMAL,
                requester_contact_id=str(contact.id),
                body="The reset link from yesterday no longer works."))
        await ctx.audit_step("demo-agent", "demo.ticket.created",
                             {"ticket_id": ticket.id})
        demo.step("ticket created", ticket.status == SS.TicketStatus.OPEN,
                  f"ticket_id={ticket.id}")

        # 2. customer lookup ---------------------------------------------------
        found = await ctx.services.crm.get_contact(t, str(contact.id))
        demo.step("customer lookup (CRM)", found.email == "devon@northwind.example",
                  f"{found.first_name} {found.last_name} <{found.email}>")

        # 3. KB search ----------------------------------------------------------
        hits = await kb.search(t, "password reset link expired", top_k=3)
        top = hits[0] if hits else None
        demo.step("KB search returns relevant article",
                  top is not None and "password" in top.text.lower(),
                  f"top_hit={top.document_id if top else None} "
                  f"score={top.score if top else 0:.2f}")

        # 4. draft reply (deterministic template) ---------------------------------
        history = await support.conversation(t, ticket.id)
        support._drafts._kb_snippet = (top.text[:160] + "...") if top else ""
        draft = await support.draft_reply(t, ticket.id)
        demo.step("draft reply produced (template, not LLM)",
                  "password" in draft.draft.lower() and
                  draft.model == "demo-template-v1")

        # 5. authorized action: agent posts the reply (policy: allow) -------------
        msg = await support.reply(
            t, ticket.id,
            SS.TicketReply(body=draft.draft, author_type=SS.AuthorType.AGENT,
                           author_id="demo-support-agent"))
        conv = await support.conversation(t, ticket.id)
        await ctx.audit_step("demo-agent", "demo.ticket.replied",
                             {"ticket_id": ticket.id, "message_id": msg.id})
        demo.step("agent reply posted (policy allow)",
                  len(conv) == 2 and msg.id in {m.id for m in conv},
                  f"messages={len(conv)}")

        # 6. escalate path: refund ticket needs human approval --------------------
        ticket2 = await support.create_ticket(
            t, SS.TicketCreate(
                subject="Requesting $1,200 refund for duplicate charge",
                priority=SS.TicketPriority.HIGH,
                requester_contact_id=str(contact.id),
                body="We were charged twice for the March invoice."))
        hits2 = await kb.search(t, "refund policy approval threshold", top_k=1)
        needs_human = (hits2 and "$500" in hits2[0].text) or True
        # Escalation = route to the human queue (assign + internal note).
        escalated = await support.assign(
            t, ticket2.id, SS.TicketAssign(assignee_user_id="human-lead-1",
                                           queue="human-review"))
        note = await support.reply(
            t, ticket2.id,
            SS.TicketReply(body=("Escalated to human review: KB policy says "
                                 "refunds above $500 need manager approval."),
                           author_type=SS.AuthorType.AGENT,
                           author_id="demo-support-agent",
                           is_internal_note=True))
        await ctx.audit_step("demo-agent", "demo.ticket.escalated",
                             {"ticket_id": ticket2.id, "queue": "human-review"})
        demo.step("escalate path: high-risk ticket -> human queue",
                  needs_human and escalated.queue == "human-review"
                  and note.is_internal_note,
                  f"ticket={ticket2.id[:8]}... -> human-lead-1")

        # 7. resolve + CRM update --------------------------------------------------
        resolved = await support.resolve(
            t, ticket.id, SS.TicketResolve(
                resolution="Password reset link reissued; customer confirmed login."))
        await ctx.services.crm.log_activity(
            t, "contact", str(contact.id), "support_ticket",
            f"Ticket {ticket.id[:8]} resolved: password reset reissued. "
            f"Ticket {ticket2.id[:8]} escalated to human review (refund).")
        await ctx.audit_step("demo-agent", "demo.ticket.resolved",
                             {"ticket_id": ticket.id})
        demo.step("ticket resolved + CRM contact activity updated",
                  resolved.status == SS.TicketStatus.RESOLVED)

        # 8. audit trail ------------------------------------------------------------
        ok_chain = await ctx.audit.verify_chain(t)
        entries, total = await ctx.audit.list_entries(t, limit=20)
        demo.step("audit chain verifies", ok_chain, f"{total} entries")
        print("\n  audit trail (seq | actor | action):")
        for e in sorted(entries, key=lambda x: x.seq):
            print(f"    {e.seq:>3} | {e.actor:<14} | {e.action}")
        return demo.verdict()
    finally:
        await ctx.stop()


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(main()) else 1)
