# ruff: noqa: F811  # fixture params shadow fixture imports (pytest idiom)
"""Tests for the CRM package (app/crm) + its HTTP router.

Covers: tenant isolation, policy-before-write, domain events, HTTP idempotency
(+ 409 on key reuse with a different body), contact dedupe, transparent lead
scoring, lead conversion, opportunity stage movement, custom-field validation,
cursor pagination, and the standard error envelope.
"""
from __future__ import annotations

import pytest

from app.crm.schemas import LeadCreate
from app.crm.scoring import LeadFacts, score_lead
from app.crm.service import CRMDuplicate, CRMNotFound, CRMValidationError, PolicyDeniedError
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


# ------------------------------------------------------------------ helpers
async def _org(biz_services, tenant, name="Acme"):
    return await biz_services.crm.create_organization(tenant, {"name": name})


async def _contact(biz_services, tenant, org_id, email="ava@example.com", **kw):
    data = {"first_name": "Ava", "last_name": "Lee", "email": email,
            "org_id": str(org_id), **kw}
    return await biz_services.crm.create_contact(tenant, data)


async def _lead(biz_services, tenant, contact_id=None, org_id=None, **kw):
    data = {"source": "inbound", "status": "new", **kw}
    if contact_id:
        data["contact_id"] = str(contact_id)
    if org_id:
        data["org_id"] = str(org_id)
    return await biz_services.crm.create_lead(tenant, data)


async def _pipeline(biz_services, tenant, name="Sales"):
    return await biz_services.crm.create_pipeline(
        tenant, name, "opportunity",
        [{"name": "Qualify"}, {"name": "Propose"}, {"name": "Closed Won",
                                                   "is_closed_won": True}])


# ------------------------------------------------------------------ tenant isolation
async def test_cross_tenant_invisibility(biz_services, tenant, tenant_b):
    org = await _org(biz_services, tenant)
    with pytest.raises(CRMNotFound):
        await biz_services.crm.get_organization(tenant_b, str(org.id))
    items, total = await biz_services.crm.list_organizations(tenant_b)
    assert total == 0 and items == []
    lead = await _lead(biz_services, tenant)
    with pytest.raises(CRMNotFound):
        await biz_services.crm.get_lead(tenant_b, str(lead.id))


async def test_cross_tenant_invisibility_http(biz_client, tenant, tenant_b):
    r = await biz_client.post("/api/v1/crm/organizations",
                              json={"name": "Acme"}, headers=mut_headers(tenant))
    assert r.status_code == 201, r.text
    org_id = r.json()["id"]
    r2 = await biz_client.get(f"/api/v1/crm/organizations/{org_id}",
                              headers=tenant_headers(tenant_b))
    assert r2.status_code == 404
    assert r2.json()["error"]["code"] == "not_found"


# ------------------------------------------------------------------ policy
async def test_policy_denial_blocks_write_before_db(biz_services, tenant, biz_policy):
    biz_policy.deny_actions.add("crm.lead.create")
    with pytest.raises(PolicyDeniedError):
        await biz_services.crm.create_lead(tenant, {"source": "inbound"})
    items, total = await biz_services.crm.list_leads(tenant)
    assert total == 0
    # the denial was evaluated exactly once, for the right action
    assert [e.action for e in biz_policy.evaluations] == ["crm.lead.create"]


async def test_policy_denial_http_envelope(biz_client, tenant, biz_services, biz_policy):
    biz_policy.deny_actions.add("crm.organization.create")
    r = await biz_client.post("/api/v1/crm/organizations", json={"name": "X"},
                              headers=mut_headers(tenant))
    assert r.status_code == 403
    body = r.json()
    assert body["error"]["code"] == "policy_denied"
    assert "trace_id" in body["error"]


# ------------------------------------------------------------------ events
async def test_domain_events_emitted(biz_services, tenant, biz_bus):
    org = await _org(biz_services, tenant)
    contact = await _contact(biz_services, tenant, org.id)
    lead = await _lead(biz_services, tenant, contact.id, org.id)
    kinds = {e.topic for e in biz_bus.events}
    assert {"crm.organization.created", "crm.contact.created",
            "crm.lead.created"} <= kinds
    by_kind = {e.topic: e for e in biz_bus.events}
    assert by_kind["crm.lead.created"].tenant_id == tenant.tenant_id
    assert by_kind["crm.lead.created"].aggregate_id == str(lead.id)
    assert by_kind["crm.lead.created"].payload["source"] == "inbound"


# ------------------------------------------------------------------ dedupe
async def test_contact_dedupe_by_email_case_insensitive(biz_services, tenant):
    org = await _org(biz_services, tenant)
    await _contact(biz_services, tenant, org.id, email="Ava@Example.com")
    with pytest.raises(CRMDuplicate):
        await _contact(biz_services, tenant, org.id, email="ava@example.com")


async def test_contact_dedupe_by_phone(biz_services, tenant):
    org = await _org(biz_services, tenant)
    await _contact(biz_services, tenant, org.id, email="a@x.com", phone="+1-555-0100")
    with pytest.raises(CRMDuplicate):
        await _contact(biz_services, tenant, org.id, email="b@x.com",
                       phone="+1-555-0100")


# ------------------------------------------------------------------ scoring
async def test_lead_scoring_is_transparent(biz_services, tenant):
    org = await biz_services.crm.create_organization(
        tenant, {"name": "Acme", "industry": "software", "size_band": "enterprise"})
    contact = await _contact(biz_services, tenant, org.id, email="vp@acme.com",
                             title="VP Engineering", phone="+1-555-0101")
    lead = await _lead(biz_services, tenant, contact.id, org.id, source="inbound")
    score, breakdown = await biz_services.crm.score_lead(tenant, str(lead.id))
    assert isinstance(score, int) and 0 <= score <= 100
    assert breakdown["score"] == score
    factors = breakdown["breakdown"]
    assert factors, "breakdown must list contributing factors"
    for f in factors:
        assert {"rule", "points", "detail"} <= set(f)
    # a bare lead scores lower than a rich one
    bare = await _lead(biz_services, tenant, source="purchased_list")
    bare_score, _ = await biz_services.crm.score_lead(tenant, str(bare.id))
    assert bare_score < score


def test_scoring_pure_function_deterministic():
    facts = LeadFacts(email="a@b.com", title="CTO", source="inbound",
                      org_size_band="enterprise", org_industry="software")
    r1, r2 = score_lead(facts), score_lead(facts)
    assert r1.score == r2.score and r1.as_dict() == r2.as_dict()


# ------------------------------------------------------------------ conversion
async def test_lead_conversion_creates_org_contact_opportunity(biz_services, tenant):
    pipeline = await _pipeline(biz_services, tenant)
    org = await _org(biz_services, tenant, name="ConvertMe")
    contact = await _contact(biz_services, tenant, org.id)
    lead = await _lead(biz_services, tenant, contact.id, org.id)
    org2, contact2, opp = await biz_services.crm.convert_lead(tenant, str(lead.id))
    assert str(org2.id) == str(org.id) and str(contact2.id) == str(contact.id)
    assert str(opp.pipeline_id) == str(pipeline.id)
    stages = (await biz_services.crm.get_pipeline_with_stages(tenant, str(pipeline.id)))[1]
    assert str(opp.stage_id) == str(stages[0].id)
    lead2 = await biz_services.crm.get_lead(tenant, str(lead.id))
    assert lead2.status == "converted" and lead2.converted_at is not None
    with pytest.raises(CRMValidationError):
        await biz_services.crm.convert_lead(tenant, str(lead.id))


# ------------------------------------------------------------------ opportunity stages
async def test_opportunity_stage_movement_and_terminal_guard(biz_services, tenant):
    pipeline = await _pipeline(biz_services, tenant)
    pipeline_obj, stages = await biz_services.crm.get_pipeline_with_stages(
        tenant, str(pipeline.id))
    org = await _org(biz_services, tenant)
    opp = await biz_services.crm.create_opportunity(
        tenant, {"name": "Big deal", "pipeline_id": str(pipeline.id),
                 "stage_id": str(stages[0].id), "org_id": str(org.id)})
    moved = await biz_services.crm.move_opportunity(tenant, str(opp.id),
                                                    str(stages[1].id))
    assert str(moved.stage_id) == str(stages[1].id)
    # move into terminal won stage, then try to move out -> blocked
    await biz_services.crm.move_opportunity(tenant, str(opp.id), str(stages[2].id))
    with pytest.raises(CRMValidationError):
        await biz_services.crm.move_opportunity(tenant, str(opp.id), str(stages[0].id))


# ------------------------------------------------------------------ custom fields
async def test_custom_field_validation(biz_services, tenant):
    await biz_services.crm.create_custom_field(
        tenant, {"object_type": "lead", "name": "budget",
                 "field_type": "number", "required": True})
    with pytest.raises(CRMValidationError):
        await biz_services.crm.create_lead(tenant, {"source": "inbound"})  # missing budget
    lead = await biz_services.crm.create_lead(
        tenant, {"source": "inbound", "custom": {"budget": 5000}})
    assert lead.custom["budget"] == 5000


# ------------------------------------------------------------------ pagination + envelope
async def test_cursor_pagination(biz_client, tenant):
    headers = tenant_headers(tenant)
    for i in range(5):
        r = await biz_client.post("/api/v1/crm/organizations",
                                  json={"name": f"Org {i}"}, headers=mut_headers(tenant))
        assert r.status_code == 201
    page1 = await biz_client.get("/api/v1/crm/organizations",
                                 params={"page_size": 2}, headers=headers)
    assert page1.status_code == 200
    body = page1.json()
    assert len(body["items"]) == 2 and body["next_page_token"]
    page2 = await biz_client.get("/api/v1/crm/organizations",
                                 params={"page_size": 2,
                                         "page_token": body["next_page_token"]},
                                 headers=headers)
    assert len(page2.json()["items"]) == 2
    assert page2.json()["items"][0]["id"] != body["items"][0]["id"]


async def test_error_envelope_shape(biz_client, tenant):
    r = await biz_client.get("/api/v1/crm/organizations/does-not-exist",
                             headers=tenant_headers(tenant))
    assert r.status_code == 404
    err = r.json()["error"]
    assert set(err) == {"code", "message", "details", "trace_id"}


async def test_missing_tenant_is_401(biz_client):
    r = await biz_client.get("/api/v1/crm/organizations")
    assert r.status_code == 401


async def test_missing_idempotency_key_is_422(biz_client, tenant):
    r = await biz_client.post("/api/v1/crm/organizations", json={"name": "No key"},
                              headers=tenant_headers(tenant))
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "missing_idempotency_key"


# ------------------------------------------------------------------ idempotency
async def test_post_idempotency_replays(biz_client, tenant):
    headers = {**tenant_headers(tenant), "Idempotency-Key": "k-1"}
    r1 = await biz_client.post("/api/v1/crm/organizations", json={"name": "Once"},
                               headers=headers)
    assert r1.status_code == 201
    r2 = await biz_client.post("/api/v1/crm/organizations", json={"name": "Once"},
                               headers=headers)
    assert r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"]
    # only one row exists
    lst = await biz_client.get("/api/v1/crm/organizations", headers=tenant_headers(tenant))
    assert sum(1 for o in lst.json()["items"] if o["name"] == "Once") == 1


async def test_post_idempotency_conflict_on_different_body(biz_client, tenant):
    headers = {**tenant_headers(tenant), "Idempotency-Key": "k-2"}
    r1 = await biz_client.post("/api/v1/crm/organizations", json={"name": "A"},
                               headers=headers)
    assert r1.status_code == 201
    r2 = await biz_client.post("/api/v1/crm/organizations", json={"name": "B"},
                               headers=headers)
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "idempotency_conflict"


async def test_lead_create_schema_validation(biz_client, tenant):
    # over-long source is rejected by the schema layer
    r = await biz_client.post("/api/v1/crm/leads", json={"source": "x" * 65},
                              headers=mut_headers(tenant))
    assert r.status_code == 422


def test_lead_create_schema_allows_missing_contact():
    m = LeadCreate(source="inbound", status="new")
    assert m.contact_id is None
