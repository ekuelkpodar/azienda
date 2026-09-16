"""The complete shippable lead workflow: "lead-qualification-outreach" v1.

Pipeline (TRIGGER -> CONDITION -> AGENT -> TOOL -> APPROVAL -> ACTION -> NOTIFICATION):

  new_lead (trigger)
    -> qualify (condition: status == "new" AND source is not a purchased list)
        false -> nurture_low (action: task.create, low priority) -> done
        true  -> research (action: research_stub — explicit stub, see actions.py)
    -> rescore (action: crm.lead.rescore — transparent rule-based score)
    -> score_gate (condition: score >= 40)
        false -> nurture_low -> done
        true  -> draft (action: outreach.draft — renders the outreach email)
    -> approve_send (approval: policy_ref "outreach.send.v1", action "comms.outreach.send")
        denied -> manual_review (action: task.create, high priority) -> done
        approved -> log_send (action: crm.activity.log type=outreach_sent)
    -> followup (action: task.create — follow-up task due in 3 days)
    -> notify (notification)

Honesty notes:
- "research" is an explicit stub: no external research provider is wired, and
  the node says so in its output instead of fabricating findings.
- "send" is LOGGED, not sent: real outbound delivery belongs to the comms
  package (not built yet). The workflow records the approved send as an
  activity + audit event; nothing claims an email left the building.
- The approval node is policy-driven: if the governance policy ALLOWs the send
  outright the approval is skipped; REQUIRE_APPROVAL parks the execution for a
  human; DENY fails closed (or follows the "denied" edge).
"""

from __future__ import annotations

from typing import Any

DEFINITION_NAME = "lead-qualification-outreach"
DEFINITION_DESCRIPTION = (
    "New lead -> qualify -> research (stub) -> score -> draft outreach -> "
    "human approval -> log send -> schedule follow-up task."
)
POLICY_REF = "outreach.send.v1"
SCORE_THRESHOLD = 40


def build_dag(score_threshold: int = SCORE_THRESHOLD) -> dict[str, Any]:
    return {
        "nodes": [
            {"id": "new_lead", "type": "trigger", "label": "New lead arrives",
             "config": {"kind": "manual"}},
            {"id": "qualify", "type": "condition", "label": "Basic qualification",
             "config": {"expression": {
                 "and": [
                     {"op": "eq", "left": {"var": "lead.status"}, "right": "new"},
                     {"op": "ne", "left": {"var": "lead.source"}, "right": "purchased_list"},
                 ]}}},
            {"id": "research", "type": "action", "label": "Research lead (stub)",
             "config": {"action": "research_stub",
                        "params": {"lead_id": "{lead.id}"}}},
            {"id": "rescore", "type": "action", "label": "Score lead",
             "config": {"action": "crm.lead.rescore",
                        "params": {"lead_id": "{lead.id}"}}},
            {"id": "score_gate", "type": "condition", "label": "Score threshold",
             "config": {"expression": {
                 "op": "gte", "left": {"var": "rescore.score"},
                 "right": score_threshold}}},
            {"id": "draft", "type": "action", "label": "Draft outreach",
             "config": {"action": "outreach.draft",
                        "params": {"lead_id": "{lead.id}"}}},
            {"id": "approve_send", "type": "approval", "label": "Human approves send",
             "config": {"policy_ref": POLICY_REF,
                        "action": "comms.outreach.send",
                        "args": {"lead_id": "{lead.id}",
                                 "draft": "{draft.draft}"}}},
            {"id": "log_send", "type": "action", "label": "Log approved send",
             "config": {"action": "crm.activity.log",
                        "params": {"subject_type": "lead",
                                   "subject_id": "{lead.id}",
                                   "type": "outreach_sent",
                                   "body": "{draft.draft}"}}},
            {"id": "followup", "type": "action", "label": "Schedule follow-up",
             "config": {"action": "task.create",
                        "params": {
                            "title": "Follow up with {lead.contact_name} ({lead.org_name})",
                            "description": "Outreach approved and logged. Follow up on reply.",
                            "priority": "high",
                            "due_in_days": 3,
                            "idempotency_key": "wf-followup-{execution_id}",
                        }}},
            {"id": "nurture_low", "type": "action", "label": "Nurture low-score lead",
             "config": {"action": "task.create",
                        "params": {
                            "title": "Nurture low-score lead {lead.org_name}",
                            "description": "Lead did not meet the qualification bar.",
                            "priority": "low",
                            "due_in_days": 14,
                            "idempotency_key": "wf-nurture-{execution_id}",
                        }}},
            {"id": "manual_review", "type": "action", "label": "Manual review task",
             "config": {"action": "task.create",
                        "params": {
                            "title": "Review outreach for {lead.org_name} (approval denied)",
                            "description": "Human denied the outreach send; review manually.",
                            "priority": "high",
                            "due_in_days": 2,
                            "idempotency_key": "wf-review-{execution_id}",
                        }}},
            {"id": "notify", "type": "notification", "label": "Done",
             "config": {"message": (
                 "Lead {lead.org_name} qualified with score {rescore.score}. "
                 "Outreach approved and logged; follow-up task scheduled.")}},
        ],
        "edges": [
            {"from": "new_lead", "to": "qualify"},
            {"from": "qualify", "to": "research", "label": "true"},
            {"from": "qualify", "to": "nurture_low", "label": "false"},
            {"from": "research", "to": "rescore"},
            {"from": "rescore", "to": "score_gate"},
            {"from": "score_gate", "to": "draft", "label": "true"},
            {"from": "score_gate", "to": "nurture_low", "label": "false"},
            {"from": "draft", "to": "approve_send"},
            {"from": "approve_send", "to": "log_send"},
            {"from": "approve_send", "to": "manual_review", "label": "denied"},
            {"from": "log_send", "to": "followup"},
            {"from": "followup", "to": "notify"},
            {"from": "nurture_low", "to": "notify"},
            {"from": "manual_review", "to": "notify"},
        ],
    }
