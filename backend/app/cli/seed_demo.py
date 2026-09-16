"""Seed demo workspace data for Azienda.

Standalone operator script (new file; intentionally NOT wired into the `azienda`
Typer group — do not edit other cli files). Seeds a realistic fictional demo
tenant ("Acme Field Services", a 45-person home-services company) through the
documented REST API in /API.md so the frontend demo mode has something honest
to render.

What is seeded (via API contracts that exist in API.md):
  - CRM: 1 pipeline + stages, 4 organizations, 8 contacts, 6 leads (with scores),
    5 opportunities (deals) spread across stages, activity log entries
  - Tasks: 6 tasks across lifecycle states
  - Agents: 2 registered agents (sdr-agent, support-triage-agent)
  - Workflows: 1 definition ("new-lead-pipeline") + 1 published version whose DAG
    encodes the signature demo: lead -> score -> outreach draft -> approval ->
    follow-up task

Prepared but NOT seeded (no API contract in API.md yet — builder 3 owns these
packages; the fixtures below are ready to wire the moment the routers land):
  - support tickets, marketing campaigns, finance invoices, knowledge-base articles

Usage:
  cd backend && python -m app.cli.seed_demo \\
      --base-url http://localhost:8000 \\
      --email admin@demo.azienda --password "$AZIENDA_SEED_PASSWORD"

The admin user must already exist (create it with the backend provision command
from builder 1's cli work, e.g. `azienda provision ...`). This script only logs
in — it never invents users.

Idempotency: every mutating POST carries a deterministic Idempotency-Key
(uuid5 over the fixture name), so re-running the script is safe within the
API's 24h idempotency window. `--reset` is accepted for the documented demo
workflow and performs the same idempotent seed; a full data wipe is NOT done
here (destructive, needs operator tooling) — the script says so explicitly.

No secrets in code: the password comes from --password or AZIENDA_SEED_PASSWORD.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

import httpx
import typer

app = typer.Typer(help="Seed demo workspace data for Azienda (via the REST API).")


# ---------------------------------------------------------------- fixtures ---

ORG_NAME = "Acme Field Services"

ORGANIZATIONS: list[dict[str, Any]] = [
    {"name": "Harborview Property Group", "domain": "harborview.example", "industry": "Property management", "size_band": "51-200", "lifecycle_stage": "customer"},
    {"name": "Cedar & Pine Builders", "domain": "cedarpine.example", "industry": "Construction", "size_band": "11-50", "lifecycle_stage": "opportunity"},
    {"name": "Northside Clinics", "domain": "northsideclinics.example", "industry": "Healthcare", "size_band": "51-200", "lifecycle_stage": "opportunity"},
    {"name": "Blue Ribbon Realty", "domain": "blueribbon.example", "industry": "Real estate", "size_band": "11-50", "lifecycle_stage": "lead"},
]

CONTACTS: list[dict[str, Any]] = [
    {"first_name": "Maya", "last_name": "Okafor", "email": "maya.okafor@harborview.example", "phone": "+1-404-555-0113", "title": "Facilities Director", "org": "Harborview Property Group", "tags": ["customer", "decision-maker"]},
    {"first_name": "Daniel", "last_name": "Reyes", "email": "d.reyes@cedarpine.example", "phone": "+1-404-555-0147", "title": "Project Manager", "org": "Cedar & Pine Builders", "tags": ["opportunity"]},
    {"first_name": "Priya", "last_name": "Natarajan", "email": "priya@northsideclinics.example", "phone": "+1-404-555-0172", "title": "Operations Manager", "org": "Northside Clinics", "tags": ["opportunity"]},
    {"first_name": "Tom", "last_name": "Beckett", "email": "tom@blueribbon.example", "phone": "+1-404-555-0198", "title": "Broker/Owner", "org": "Blue Ribbon Realty", "tags": ["lead"]},
    {"first_name": "Sofia", "last_name": "Marsh", "email": "sofia.marsh@harborview.example", "phone": "+1-404-555-0121", "title": "Office Manager", "org": "Harborview Property Group", "tags": ["customer"]},
    {"first_name": "James", "last_name": "Whitfield", "email": "jwhitfield@cedarpine.example", "phone": "+1-404-555-0155", "title": "Owner", "org": "Cedar & Pine Builders", "tags": ["decision-maker"]},
    {"first_name": "Aisha", "last_name": "Bello", "email": "a.bello@northsideclinics.example", "phone": "+1-404-555-0188", "title": "Front Desk Lead", "org": "Northside Clinics", "tags": []},
    {"first_name": "Leo", "last_name": "Fontaine", "email": "leo@blueribbon.example", "phone": "+1-404-555-0102", "title": "Agent", "org": "Blue Ribbon Realty", "tags": ["lead"]},
]

LEADS: list[dict[str, Any]] = [
    {"contact_email": "tom@blueribbon.example", "source": "website", "status": "new", "score": 78},
    {"contact_email": "leo@blueribbon.example", "source": "website", "status": "new", "score": 64},
    {"contact_email": "d.reyes@cedarpine.example", "source": "referral", "status": "contacted", "score": 82},
    {"contact_email": "priya@northsideclinics.example", "source": "outbound", "status": "qualified", "score": 91},
    {"contact_email": "jwhitfield@cedarpine.example", "source": "partner", "status": "contacted", "score": 73},
    {"contact_email": "a.bello@northsideclinics.example", "source": "website", "status": "new", "score": 55},
]

PIPELINE_STAGES = [
    {"name": "Discovery", "probability": 0.15},
    {"name": "Proposal", "probability": 0.40},
    {"name": "Negotiation", "probability": 0.70},
    {"name": "Won", "probability": 1.0},
    {"name": "Lost", "probability": 0.0},
]

OPPORTUNITIES: list[dict[str, Any]] = [
    {"name": "Harborview — 12-property maintenance contract", "org": "Harborview Property Group", "contact_email": "maya.okafor@harborview.example", "stage": "Negotiation", "amount": 84000, "currency": "USD", "close_date": "2026-10-15"},
    {"name": "Cedar & Pine — new-build HVAC package (14 units)", "org": "Cedar & Pine Builders", "contact_email": "d.reyes@cedarpine.example", "stage": "Proposal", "amount": 62000, "currency": "USD", "close_date": "2026-11-01"},
    {"name": "Northside Clinics — 3-location service plan", "org": "Northside Clinics", "contact_email": "priya@northsideclinics.example", "stage": "Discovery", "amount": 45000, "currency": "USD", "close_date": "2026-12-01"},
    {"name": "Blue Ribbon — preferred vendor agreement", "org": "Blue Ribbon Realty", "contact_email": "tom@blueribbon.example", "stage": "Discovery", "amount": 28000, "currency": "USD", "close_date": "2026-10-30"},
    {"name": "Harborview — emergency line replacement", "org": "Harborview Property Group", "contact_email": "sofia.marsh@harborview.example", "stage": "Won", "amount": 12500, "currency": "USD", "close_date": "2026-09-10"},
]

TASKS: list[dict[str, Any]] = [
    {"title": "Follow up: Cedar & Pine proposal", "description": "Proposal sent 2026-09-12; decision expected this week.", "priority": "high", "status": "executing"},
    {"title": "Draft Q4 maintenance schedule for Harborview", "description": "12 properties, prioritize the 3 with aging units.", "priority": "medium", "status": "planning"},
    {"title": "Review outreach draft for Blue Ribbon Realty", "description": "Agent-drafted outreach is waiting in the approvals inbox.", "priority": "high", "status": "waiting_approval"},
    {"title": "Reconcile August invoices (NEXORA)", "description": "Cross-check posted invoices against the NEXORA seam.", "priority": "medium", "status": "pending"},
    {"title": "Onboard Northside Clinics contact", "description": "Blocked: waiting on facility access paperwork.", "priority": "low", "status": "blocked"},
    {"title": "Publish September service report", "description": "Completed and sent to all customer contacts.", "priority": "low", "status": "completed"},
]

AGENTS: list[dict[str, Any]] = [
    {"name": "sdr-agent", "description": "Scores inbound leads, drafts outreach, creates follow-up tasks. Outreach sends always require approval."},
    {"name": "support-triage-agent", "description": "Classifies tickets, grounds replies in the KB, escalates what it cannot resolve. Autonomous replies disabled (L2)."},
]

# Signature demo 1: lead -> score -> outreach draft -> approval -> follow-up task.
NEW_LEAD_PIPELINE_DAG: dict[str, Any] = {
    "name": "new-lead-pipeline",
    "steps": [
        {"id": "fetch_lead", "tool": "crm.get_lead", "input": {"lead_id": "{{ input.lead_id }}"},
         "description": "Load the lead record and contact context."},
        {"id": "score_lead", "tool": "agents.score_lead",
         "description": "Score fit + intent (0-100). Scores < 50 route to nurture instead of outreach."},
        {"id": "draft_outreach", "tool": "comms.draft_email",
         "description": "Draft a personalized outreach email grounded in the lead's context."},
        {"id": "approval_gate", "type": "human_approval",
         "description": "Human reviews the draft. Timeout = deny (fail closed).",
         "on_approve": "send_outreach", "on_deny": "end_nurture"},
        {"id": "send_outreach", "tool": "comms.send_email", "requires": ["approval_gate"],
         "description": "Send the approved outreach (policy-gated, audited)."},
        {"id": "create_followup_task", "tool": "tasks.create",
         "description": "Create a follow-up task for the owner in 3 business days."},
        {"id": "end_nurture", "tool": "crm.tag_lead",
         "description": "Denied/low-score path: tag for nurture sequence, no send."},
    ],
    "policy": {
        "autonomy_level": 2,
        "approval_required": ["send_outreach"],
        "budget_cap_usd": 5.0,
    },
}

# --- Prepared fixtures for modules whose API contract does not exist yet. ----
# These are NOT seeded (no endpoints in API.md). They document exactly what the
# seed will create once builder 3's routers land, so wiring is mechanical.
TICKETS_FIXTURE: list[dict[str, Any]] = [
    {"subject": "AC not cooling — Harborview unit 4B", "contact_email": "sofia.marsh@harborview.example", "priority": "high", "status": "open",
     "body": "Tenant reports the AC runs but doesn't cool. Filter changed last month."},
    {"subject": "Invoice #INV-2041 question", "contact_email": "maya.okafor@harborview.example", "priority": "medium", "status": "open",
     "body": "Line item 'after-hours dispatch fee' — please explain before we pay."},
    {"subject": "Schedule fall tune-ups (3 locations)", "contact_email": "priya@northsideclinics.example", "priority": "low", "status": "open",
     "body": "Want tune-ups before flu season; mornings preferred."},
]

KB_ARTICLES_FIXTURE: list[dict[str, Any]] = [
    {"title": "AC runs but doesn't cool — triage checklist",
     "body": "1) Confirm thermostat mode/cool setpoint. 2) Check air filter (replace if gray). 3) Inspect outdoor unit for debris/blocked airflow. 4) Listen for short-cycling. If ice on lines or warm air after 30 min, escalate to a technician — do NOT advise refrigerant handling.",
     "tags": ["hvac", "triage"]},
    {"title": "After-hours dispatch fee policy",
     "body": "After-hours dispatch (6pm–8am + holidays) carries a flat $95 fee, waived for Priority and 24/7 plan customers. The fee covers the on-call technician, not parts or labor. Cite the signed service agreement §4.2 when asked.",
     "tags": ["billing", "policy"]},
    {"title": "Fall tune-up: what's included",
     "body": "21-point inspection: filters, coils, refrigerant check, thermostat calibration, duct visual, drain line flush. Takes ~60–90 min per system. Mornings book fastest in Sep–Oct.",
     "tags": ["hvac", "maintenance"]},
]

CAMPAIGNS_FIXTURE: list[dict[str, Any]] = [
    {"name": "Fall tune-up reminder — customers", "channel": "email", "status": "draft",
     "audience": "customers with last service > 9 months ago",
     "notes": "Launch requires human approval (bulk-send policy)."},
]

INVOICES_FIXTURE: list[dict[str, Any]] = [
    {"number": "INV-2041", "org": "Harborview Property Group", "amount": 1875.00, "currency": "USD", "status": "sent", "due_date": "2026-10-01"},
    {"number": "INV-2042", "org": "Cedar & Pine Builders", "amount": 3200.00, "currency": "USD", "status": "draft", "due_date": "2026-10-15"},
]


# ------------------------------------------------------------------ client ---

class SeedClient:
    """Minimal typed wrapper over the documented API (see /API.md)."""

    def __init__(self, base_url: str, email: str, password: str) -> None:
        self.base = base_url.rstrip("/") + "/api/v1"
        self.session = httpx.Client(base_url=self.base, timeout=30.0)
        resp = self.session.post("/auth/login", json={"email": email, "password": password})
        if resp.status_code != 200:
            raise SystemExit(f"login failed ({resp.status_code}): {resp.text[:300]}")
        data = resp.json()
        token = data.get("access_token")
        if not token:
            raise SystemExit("login response had no access_token")
        self.session.headers["Authorization"] = f"Bearer {token}"
        me = self.session.get("/auth/me").json()
        tenant = self.session.get("/tenants/me").json()
        print(f"  logged in as {me['user']['email']} → tenant '{tenant['name']}'")

    def post(self, path: str, payload: dict[str, Any], key_name: str) -> dict[str, Any]:
        """POST with a deterministic idempotency key so re-runs are safe."""
        key = str(uuid.uuid5(uuid.NAMESPACE_URL, f"azienda-seed:{key_name}"))
        resp = self.session.post(path, json=payload, headers={"Idempotency-Key": key})
        if resp.status_code >= 400:
            raise SystemExit(f"POST {path} failed ({resp.status_code}): {resp.text[:500]}")
        return resp.json()  # type: ignore[no-any-return]

    def get_page(self, path: str, **params: Any) -> list[dict[str, Any]]:
        resp = self.session.get(path, params=params or None)
        if resp.status_code >= 400:
            raise SystemExit(f"GET {path} failed ({resp.status_code}): {resp.text[:300]}")
        data = resp.json()
        return data["items"] if isinstance(data, dict) and "items" in data else data


# ------------------------------------------------------------------- seed ---

def seed(client: SeedClient) -> dict[str, int]:
    counts: dict[str, int] = {}

    # Pipeline + stages
    existing = [p for p in client.get_page("/crm/pipelines") if p["name"] == "Sales Pipeline"]
    if existing:
        pipeline = existing[0]
        print("  pipeline 'Sales Pipeline' already exists — reusing")
    else:
        pipeline = client.post(
            "/crm/pipelines",
            {"name": "Sales Pipeline", "object_type": "opportunity",
             "stages": PIPELINE_STAGES},
            "pipeline:sales",
        )
        print(f"  created pipeline 'Sales Pipeline' ({len(pipeline.get('stages', []))} stages)")
    stage_by_name = {s["name"]: s["id"] for s in pipeline.get("stages", [])}
    counts["pipelines"] = 1

    # Organizations
    org_ids: dict[str, str] = {}
    existing_orgs = {o["name"]: o for o in client.get_page("/crm/organizations", page_size=200)}
    for org in ORGANIZATIONS:
        if org["name"] in existing_orgs:
            org_ids[org["name"]] = existing_orgs[org["name"]]["id"]
            continue
        created = client.post("/crm/organizations", org, f"org:{org['name']}")
        org_ids[org["name"]] = created["id"]
    counts["organizations"] = len(org_ids)
    print(f"  organizations: {len(org_ids)}")

    # Contacts
    contact_ids: dict[str, str] = {}
    existing_contacts = {c["email"]: c for c in client.get_page("/crm/contacts", page_size=200) if c.get("email")}
    for c in CONTACTS:
        if c["email"] in existing_contacts:
            contact_ids[c["email"]] = existing_contacts[c["email"]]["id"]
            continue
        payload = {k: v for k, v in c.items() if k != "org"}
        payload["org_id"] = org_ids[c["org"]]
        created = client.post("/crm/contacts", payload, f"contact:{c['email']}")
        contact_ids[c["email"]] = created["id"]
    counts["contacts"] = len(contact_ids)
    print(f"  contacts: {len(contact_ids)}")

    # Leads
    existing_leads = {(l.get("contact_id")) for l in client.get_page("/crm/leads", page_size=200)}
    n_leads = 0
    for lead in LEADS:
        cid = contact_ids[lead["contact_email"]]
        if cid in existing_leads:
            continue
        client.post(
            "/crm/leads",
            {"contact_id": cid, "org_id": org_ids[[c["org"] for c in CONTACTS if c["email"] == lead["contact_email"]][0]],
             "source": lead["source"], "status": lead["status"], "score": lead["score"]},
            f"lead:{lead['contact_email']}",
        )
        n_leads += 1
    counts["leads"] = n_leads
    print(f"  leads: {n_leads} new")

    # Opportunities (deals)
    existing_opps = {o["name"] for o in client.get_page("/crm/opportunities", page_size=200)}
    n_opps = 0
    for opp in OPPORTUNITIES:
        if opp["name"] in existing_opps:
            continue
        client.post(
            "/crm/opportunities",
            {"pipeline_id": pipeline["id"], "stage_id": stage_by_name[opp["stage"]],
             "org_id": org_ids[opp["org"]], "contact_id": contact_ids[opp["contact_email"]],
             "name": opp["name"], "amount": opp["amount"], "currency": opp["currency"],
             "close_date": opp["close_date"]},
            f"opp:{opp['name']}",
        )
        n_opps += 1
    counts["opportunities"] = n_opps
    print(f"  opportunities: {n_opps} new")

    # Activities
    for i, note in enumerate([
        ("Harborview Property Group", "Call with Maya Okafor — approved expanding to 12 properties; proposal for negotiation stage."),
        ("Cedar & Pine Builders", "Site walk scheduled; Daniel Reyes wants itemized HVAC package by Friday."),
    ]):
        client.post(
            "/crm/activities",
            {"subject_type": "organization", "subject_id": org_ids[note[0]],
             "type": "note", "body": note[1]},
            f"activity:{i}",
        )
    print("  activities: 2")

    # Tasks
    existing_tasks = {t["title"] for t in client.get_page("/tasks", page_size=200)}
    n_tasks = 0
    for task in TASKS:
        if task["title"] in existing_tasks:
            continue
        created = client.post("/tasks", {k: v for k, v in task.items() if k != "status"}, f"task:{task['title']}")
        # Drive the task into its demo lifecycle state via guarded transitions.
        target = task["status"]
        if target != "pending":
            client.session.post(f"/tasks/{created['id']}/transition", json={"to": target, "note": "seeded demo state"})
        n_tasks += 1
    counts["tasks"] = n_tasks
    print(f"  tasks: {n_tasks} new")

    # Agents
    existing_agents = {a["name"] for a in client.get_page("/agents")}
    n_agents = 0
    for agent in AGENTS:
        if agent["name"] in existing_agents:
            continue
        client.post("/agents", agent, f"agent:{agent['name']}")
        n_agents += 1
    counts["agents"] = n_agents
    print(f"  agents: {n_agents} new")

    # Workflow definition: new-lead-pipeline + published version
    existing_defs = [d for d in client.get_page("/workflows/definitions") if d["name"] == "new-lead-pipeline"]
    if existing_defs:
        print("  workflow definition 'new-lead-pipeline' already exists — reusing")
    else:
        definition = client.post(
            "/workflows/definitions",
            {"name": "new-lead-pipeline",
             "description": "Signature demo: lead → score → outreach draft → human approval → follow-up task.",
             "autonomy_level": 2, "is_active": True},
            "workflow:new-lead-pipeline",
        )
        version = client.post(
            f"/workflows/definitions/{definition['id']}/versions",
            {"dag": NEW_LEAD_PIPELINE_DAG},
            "workflow:new-lead-pipeline:v1",
        )
        print(f"  workflow 'new-lead-pipeline' published as version {version.get('version', 1)}")
    counts["workflow_definitions"] = 1

    return counts


def report_skipped() -> None:
    print("\nSkipped (no API contract in API.md yet — fixtures prepared in this file):")
    print(f"  - support tickets:      {len(TICKETS_FIXTURE)} fixtures (builder 3: support package)")
    print(f"  - knowledge articles:   {len(KB_ARTICLES_FIXTURE)} fixtures (builder 4: knowledge package)")
    print(f"  - marketing campaigns:  {len(CAMPAIGNS_FIXTURE)} fixtures (builder 3: marketing package)")
    print(f"  - finance invoices:     {len(INVOICES_FIXTURE)} fixtures (builder 3: finance package)")
    print("  Wire these the moment the routers land; the frontend already renders honest")
    print("  'Planned' states for these modules.")


@app.command()
def main(
    base_url: str = typer.Option("http://localhost:8000", help="Backend base URL (serves /api/v1 same-origin)."),
    email: str = typer.Option(..., help="Admin email (must already exist — see module docstring)."),
    password: str = typer.Option("", help="Admin password (or AZIENDA_SEED_PASSWORD)."),
    reset: bool = typer.Option(False, help="Re-run idempotently. NOT a data wipe — see docstring."),
) -> None:
    """Seed the demo workspace via the documented REST API."""
    pw = password or os.environ.get("AZIENDA_SEED_PASSWORD", "")
    if not pw:
        raise SystemExit("password required: --password or AZIENDA_SEED_PASSWORD")
    if reset:
        print("NOTE: --reset re-seeds idempotently; it does not wipe data (destructive wipes need operator tooling).")
    print(f"Seeding demo data against {base_url} ...")
    client = SeedClient(base_url, email, password=pw)
    counts = seed(client)
    report_skipped()
    print("\nDone. Summary:", json.dumps(counts))
    print(f"Demo org: {ORG_NAME}. Sign in to the console as {email}.")


if __name__ == "__main__":
    app()
