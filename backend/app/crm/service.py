"""CRMService: organizations, contacts, leads, opportunities, pipelines, activities.

Conventions (binding):
- Every method that touches data takes ``tenant: TenantContext`` FIRST.
- Every query filters tenant_id. No exceptions.
- Consequential writes (convert, close, delete/archive, bulk) are policy-gated
  BEFORE execution via the injected PolicyEngine; DENY raises PolicyDeniedError.
- Mutations that matter emit DomainEvents via the injected EventBus.
- ``custom`` JSON payloads are validated against CustomFieldDefinitions.

The service never imports another domain package. Cross-domain needs (e.g. the
workflow engine calling lead scoring) go through the ports defined in
workflows/ports.py, implemented by adapters over this service.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.contracts import (
    ActionRequest,
    DomainEvent,
    EventBus,
    PolicyDecision,
    PolicyEffect,
    PolicyEngine,
    TenantContext,
)

from .models import (
    Activity,
    Contact,
    CustomFieldDefinition,
    Lead,
    Opportunity,
    Organization,
    Pipeline,
    PipelineStage,
    Relationship,
)
from .scoring import LeadFacts, score_lead


# ------------------------------------------------------------------ errors
class CRMError(Exception):
    """Base for CRM domain errors; routers map these to the error envelope."""


class CRMNotFound(CRMError):
    def __init__(self, entity: str, entity_id: str):
        super().__init__(f"{entity} {entity_id} not found")
        self.entity = entity
        self.entity_id = entity_id


class CRMDuplicate(CRMError):
    def __init__(self, entity: str, field: str, existing_id: str):
        super().__init__(f"duplicate {entity}: {field} already exists")
        self.entity = entity
        self.field = field
        self.existing_id = existing_id


class CRMValidationError(CRMError):
    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.details = details or {}


class PolicyDeniedError(CRMError):
    def __init__(self, action: str, reasons: tuple[str, ...]):
        super().__init__(
            f"policy denied action '{action}': {'; '.join(reasons) or 'no reason given'}")
        self.action = action
        self.reasons = reasons


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _tid(tenant: TenantContext) -> str:
    return tenant.tenant_id


def _actor(tenant: TenantContext) -> str:
    return tenant.user_id or tenant.agent_id or "system"


# ------------------------------------------------------------------ service
class CRMService:
    """Domain service for the CRM package. Constructed with injected seams."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        policy_engine: PolicyEngine,
    ) -> None:
        self._sessions = session_factory
        self._bus = event_bus
        self._policy = policy_engine

    # ---------------------------------------------------------- internals
    async def _emit(
        self, topic: str, tenant: TenantContext, aggregate_id: str, payload: dict[str, Any]
    ) -> None:
        await self._bus.publish(
            DomainEvent(
                topic=topic,
                tenant_id=tenant.tenant_id,
                aggregate_id=aggregate_id,
                payload=payload,
                event_id=str(uuid.uuid4()),
                occurred_at=_utcnow(),
            )
        )

    async def _check_policy(
        self,
        tenant: TenantContext,
        action: str,
        resource: str | None = None,
        args: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> PolicyDecision:
        decision = await self._policy.evaluate(
            ActionRequest(
                tenant=tenant,
                action=action,
                resource=resource,
                args=args or {},
                idempotency_key=idempotency_key,
            )
        )
        if decision.effect is PolicyEffect.DENY:
            raise PolicyDeniedError(action, decision.reasons)
        return decision

    async def _validate_custom(
        self, session: AsyncSession, tenant: TenantContext, object_type: str,
        custom: dict[str, Any],
    ) -> None:
        defs = (
            await session.execute(
                select(CustomFieldDefinition).where(
                    CustomFieldDefinition.tenant_id == _tid(tenant),
                    CustomFieldDefinition.object_type == object_type,
                )
            )
        ).scalars().all()
        by_name = {d.name: d for d in defs}
        for d in defs:
            if d.required and d.name not in custom:
                raise CRMValidationError(
                    f"custom field '{d.name}' is required for {object_type}",
                    {"field": d.name, "object_type": object_type},
                )
        for name, value in custom.items():
            d = by_name.get(name)
            if d is None:
                continue  # undeclared keys are allowed (schema is additive)
            self._check_field_type(d, value)

    @staticmethod
    def _check_field_type(d: CustomFieldDefinition, value: Any) -> None:
        ok = {
            "string": isinstance(value, str),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
            "date": isinstance(value, str),  # ISO-8601 validated at the router schema layer
            "enum": isinstance(value, str) and (not d.options or value in d.options),
        }.get(d.field_type, False)
        if not ok:
            raise CRMValidationError(
                f"custom field '{d.name}' must be {d.field_type}"
                + (f" one of {d.options}" if d.field_type == "enum" and d.options else ""),
                {"field": d.name, "expected": d.field_type},
            )

    async def _get(
        self, session: AsyncSession, tenant: TenantContext, model: Any, entity: str,
        entity_id: str,
    ) -> Any:
        row = (
            await session.execute(
                select(model).where(model.id == entity_id, model.tenant_id == _tid(tenant))
            )
        ).scalar_one_or_none()
        if row is None:
            raise CRMNotFound(entity, str(entity_id))
        return row

    # ---------------------------------------------------------- organizations
    async def create_organization(
        self, tenant: TenantContext, data: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> Organization:
        await self._check_policy(tenant, "crm.organization.create", args={"name": data.get("name")},
                                 idempotency_key=idempotency_key)
        async with self._sessions() as session:
            await self._validate_custom(session, tenant, "organization", data.get("custom", {}))
            org = Organization(tenant_id=_tid(tenant), **data)
            session.add(org)
            await session.commit()
            await session.refresh(org)
        await self._emit("crm.organization.created", tenant, str(org.id),
                         {"name": org.name, "actor": _actor(tenant)})
        return org

    async def get_organization(
        self, tenant: TenantContext, org_id: str
    ) -> Organization:
        async with self._sessions() as session:
            return await self._get(session, tenant, Organization, "organization", org_id)

    async def list_organizations(
        self, tenant: TenantContext, *, search: str | None = None, tag: str | None = None,
        owner_id: str | None = None, include_archived: bool = False,
        limit: int = 50, offset: int = 0,
    ) -> tuple[list[Organization], int]:
        async with self._sessions() as session:
            q = select(Organization).where(Organization.tenant_id == _tid(tenant))
            if not include_archived:
                q = q.where(Organization.is_archived.is_(False))
            if search:
                q = q.where(Organization.name.ilike(f"%{search}%"))
            if owner_id:
                q = q.where(Organization.owner_id == owner_id)
            total = (await session.execute(
                select(func.count()).select_from(q.subquery()))).scalar_one()
            rows = (await session.execute(
                q.order_by(Organization.created_at.desc()).limit(limit).offset(offset)
            )).scalars().all()
            items = [r for r in rows]
            if tag:
                items = [r for r in items if tag in (r.tags or [])]
            return items, total

    async def update_organization(
        self, tenant: TenantContext, org_id: str, data: dict[str, Any]
    ) -> Organization:
        await self._check_policy(tenant, "crm.organization.update",
                                 resource=f"organization:{org_id}")
        async with self._sessions() as session:
            org = await self._get(session, tenant, Organization, "organization", org_id)
            if "custom" in data:
                await self._validate_custom(session, tenant, "organization", data["custom"])
            for k, v in data.items():
                setattr(org, k, v)
            await session.commit()
            await session.refresh(org)
        await self._emit("crm.organization.updated", tenant, str(org.id),
                         {"actor": _actor(tenant), "fields": sorted(data)})
        return org

    async def archive_organization(self, tenant: TenantContext, org_id: str) -> Organization:
        await self._check_policy(tenant, "crm.organization.archive",
                                 resource=f"organization:{org_id}")
        async with self._sessions() as session:
            org = await self._get(session, tenant, Organization, "organization", org_id)
            org.is_archived = True
            await session.commit()
            await session.refresh(org)
        await self._emit("crm.organization.archived", tenant, str(org.id),
                         {"actor": _actor(tenant)})
        return org

    # ---------------------------------------------------------- contacts
    async def _dedupe_contact(
        self, session: AsyncSession, tenant: TenantContext,
        email: str | None, phone: str | None, exclude_id: str | None = None,
    ) -> None:
        tid = _tid(tenant)
        if email:
            q = select(Contact).where(Contact.tenant_id == tid,
                                     func.lower(Contact.email) == email.lower())
            if exclude_id:
                q = q.where(Contact.id != exclude_id)
            existing = (await session.execute(q)).scalar_one_or_none()
            if existing:
                raise CRMDuplicate("contact", "email", str(existing.id))
        if phone:
            q = select(Contact).where(Contact.tenant_id == tid, Contact.phone == phone)
            if exclude_id:
                q = q.where(Contact.id != exclude_id)
            existing = (await session.execute(q)).scalar_one_or_none()
            if existing:
                raise CRMDuplicate("contact", "phone", str(existing.id))

    async def create_contact(
        self, tenant: TenantContext, data: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> Contact:
        await self._check_policy(tenant, "crm.contact.create",
                                 args={"email": data.get("email")},
                                 idempotency_key=idempotency_key)
        async with self._sessions() as session:
            await self._validate_custom(session, tenant, "contact", data.get("custom", {}))
            await self._dedupe_contact(session, tenant, data.get("email"), data.get("phone"))
            if data.get("org_id"):
                await self._get(session, tenant, Organization, "organization", data["org_id"])
            contact = Contact(tenant_id=_tid(tenant), **data)
            session.add(contact)
            await session.commit()
            await session.refresh(contact)
        await self._emit("crm.contact.created", tenant, str(contact.id),
                         {"email": contact.email, "actor": _actor(tenant)})
        return contact

    async def get_contact(self, tenant: TenantContext, contact_id: str) -> Contact:
        async with self._sessions() as session:
            return await self._get(session, tenant, Contact, "contact", contact_id)

    async def list_contacts(
        self, tenant: TenantContext, *, org_id: str | None = None,
        tag: str | None = None, search: str | None = None,
        limit: int = 50, offset: int = 0,
    ) -> tuple[list[Contact], int]:
        async with self._sessions() as session:
            q = select(Contact).where(
                Contact.tenant_id == _tid(tenant), Contact.is_archived.is_(False))
            if org_id:
                q = q.where(Contact.org_id == org_id)
            if search:
                like = f"%{search}%"
                q = q.where(
                    Contact.first_name.ilike(like) | Contact.last_name.ilike(like)
                    | Contact.email.ilike(like)
                )
            total = (await session.execute(
                select(func.count()).select_from(q.subquery()))).scalar_one()
            rows = (await session.execute(
                q.order_by(Contact.created_at.desc()).limit(limit).offset(offset)
            )).scalars().all()
            items = [r for r in rows]
            if tag:
                items = [r for r in items if tag in (r.tags or [])]
            return items, total

    async def update_contact(
        self, tenant: TenantContext, contact_id: str, data: dict[str, Any]
    ) -> Contact:
        await self._check_policy(tenant, "crm.contact.update", resource=f"contact:{contact_id}")
        async with self._sessions() as session:
            contact = await self._get(session, tenant, Contact, "contact", contact_id)
            if "custom" in data:
                await self._validate_custom(session, tenant, "contact", data["custom"])
            if "email" in data or "phone" in data:
                await self._dedupe_contact(
                    session, tenant,
                    data.get("email", contact.email), data.get("phone", contact.phone),
                    exclude_id=contact.id,
                )
            for k, v in data.items():
                setattr(contact, k, v)
            await session.commit()
            await session.refresh(contact)
        await self._emit("crm.contact.updated", tenant, str(contact.id),
                         {"actor": _actor(tenant), "fields": sorted(data)})
        return contact

    # ---------------------------------------------------------- leads
    async def create_lead(
        self, tenant: TenantContext, data: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> Lead:
        await self._check_policy(tenant, "crm.lead.create",
                                 args={"source": data.get("source")},
                                 idempotency_key=idempotency_key)
        async with self._sessions() as session:
            await self._validate_custom(session, tenant, "lead", data.get("custom", {}))
            if data.get("contact_id"):
                await self._get(session, tenant, Contact, "contact", data["contact_id"])
            if data.get("org_id"):
                await self._get(session, tenant, Organization, "organization", data["org_id"])
            lead = Lead(tenant_id=_tid(tenant), **data)
            session.add(lead)
            await session.commit()
            await session.refresh(lead)
        await self._emit("crm.lead.created", tenant, str(lead.id),
                         {"source": lead.source, "actor": _actor(tenant)})
        return lead

    async def get_lead(self, tenant: TenantContext, lead_id: str) -> Lead:
        async with self._sessions() as session:
            return await self._get(session, tenant, Lead, "lead", lead_id)

    async def list_leads(
        self, tenant: TenantContext, *, status: str | None = None,
        owner_id: str | None = None, source: str | None = None,
        min_score: int | None = None, limit: int = 50, offset: int = 0,
    ) -> tuple[list[Lead], int]:
        async with self._sessions() as session:
            q = select(Lead).where(Lead.tenant_id == _tid(tenant))
            if status:
                q = q.where(Lead.status == status)
            if owner_id:
                q = q.where(Lead.owner_id == owner_id)
            if source:
                q = q.where(Lead.source == source)
            if min_score is not None:
                q = q.where(Lead.score >= min_score)
            total = (await session.execute(
                select(func.count()).select_from(q.subquery()))).scalar_one()
            rows = (await session.execute(
                q.order_by(Lead.created_at.desc()).limit(limit).offset(offset)
            )).scalars().all()
            return list(rows), total

    async def update_lead(
        self, tenant: TenantContext, lead_id: str, data: dict[str, Any]
    ) -> Lead:
        await self._check_policy(tenant, "crm.lead.update", resource=f"lead:{lead_id}")
        async with self._sessions() as session:
            lead = await self._get(session, tenant, Lead, "lead", lead_id)
            if "custom" in data:
                await self._validate_custom(session, tenant, "lead", data["custom"])
            for k, v in data.items():
                setattr(lead, k, v)
            await session.commit()
            await session.refresh(lead)
        await self._emit("crm.lead.updated", tenant, str(lead.id),
                         {"actor": _actor(tenant), "fields": sorted(data)})
        return lead

    async def score_lead(
        self, tenant: TenantContext, lead_id: str
    ) -> tuple[int, dict[str, Any]]:
        """Recompute the transparent rule-based score and persist it."""
        await self._check_policy(tenant, "crm.lead.score", resource=f"lead:{lead_id}")
        async with self._sessions() as session:
            lead = await self._get(session, tenant, Lead, "lead", lead_id)
            contact = org = None
            if lead.contact_id:
                contact = await self._get(session, tenant, Contact, "contact", lead.contact_id)
            if lead.org_id:
                org = await self._get(session, tenant, Organization, "organization", lead.org_id)
            last_activity = (
                await session.execute(
                    select(func.max(Activity.occurred_at)).where(
                        Activity.tenant_id == _tid(tenant),
                        Activity.subject_type == "lead",
                        Activity.subject_id == lead.id,
                    )
                )
            ).scalar_one_or_none()
            result = score_lead(LeadFacts(
                email=contact.email if contact else None,
                phone=contact.phone if contact else None,
                title=contact.title if contact else None,
                source=lead.source,
                org_size_band=org.size_band if org else None,
                org_industry=org.industry if org else None,
                last_activity_at=last_activity,
            ))
            lead.score = result.score
            lead.score_breakdown = result.as_dict()
            await session.commit()
        await self._emit("crm.lead.scored", tenant, str(lead.id),
                         {"score": result.score, "actor": _actor(tenant)})
        return result.score, result.as_dict()

    async def convert_lead(
        self, tenant: TenantContext, lead_id: str,
        idempotency_key: str | None = None,
    ) -> tuple[Organization, Contact, Opportunity]:
        """Lead -> organization + contact + opportunity. Policy-gated (consequential)."""
        await self._check_policy(tenant, "crm.lead.convert", resource=f"lead:{lead_id}",
                                 idempotency_key=idempotency_key)
        async with self._sessions() as session:
            lead = await self._get(session, tenant, Lead, "lead", lead_id)
            if lead.converted_at is not None:
                raise CRMValidationError("lead is already converted",
                                         {"lead_id": str(lead_id)})
            contact = await self._get(session, tenant, Contact, "contact", lead.contact_id) \
                if lead.contact_id else None
            org = await self._get(session, tenant, Organization, "organization", lead.org_id) \
                if lead.org_id else None
            if org is None:
                org = Organization(
                    tenant_id=_tid(tenant),
                    name=(contact.last_name or contact.first_name or "Unknown")
                    if contact else "Converted lead org",
                    lifecycle_stage="customer",
                    owner_id=lead.owner_id,
                )
                session.add(org)
                await session.flush()
            if contact is None:
                contact = Contact(tenant_id=_tid(tenant), org_id=org.id, first_name="Unknown")
                session.add(contact)
                await session.flush()
            pipeline = (
                await session.execute(
                    select(Pipeline).where(
                        Pipeline.tenant_id == _tid(tenant),
                        Pipeline.object_type == "opportunity",
                        Pipeline.is_active.is_(True),
                    ).order_by(Pipeline.created_at)
                )
            ).scalars().first()
            if pipeline is None:
                raise CRMValidationError("no active opportunity pipeline exists for tenant")
            first_stage = (
                await session.execute(
                    select(PipelineStage)
                    .where(PipelineStage.pipeline_id == pipeline.id)
                    .order_by(PipelineStage.position)
                )
            ).scalars().first()
            if first_stage is None:
                raise CRMValidationError("pipeline has no stages")
            opp = Opportunity(
                tenant_id=_tid(tenant),
                pipeline_id=pipeline.id,
                stage_id=first_stage.id,
                org_id=org.id,
                contact_id=contact.id,
                name=f"{org.name} — new opportunity",
                owner_id=lead.owner_id,
            )
            session.add(opp)
            lead.status = "converted"
            lead.converted_at = _utcnow()
            await session.commit()
            for obj in (org, contact, opp):
                await session.refresh(obj)
        await self._emit("crm.lead.converted", tenant, str(lead.id), {
            "organization_id": str(org.id), "contact_id": str(contact.id),
            "opportunity_id": str(opp.id), "actor": _actor(tenant),
        })
        return org, contact, opp

    # ---------------------------------------------------------- pipelines
    async def create_pipeline(
        self, tenant: TenantContext, name: str, object_type: str,
        stages: list[dict[str, Any]], idempotency_key: str | None = None,
    ) -> Pipeline:
        await self._check_policy(tenant, "crm.pipeline.create", args={"name": name},
                                 idempotency_key=idempotency_key)
        if not stages:
            raise CRMValidationError("pipeline requires at least one stage")
        async with self._sessions() as session:
            existing = (await session.execute(
                select(Pipeline).where(Pipeline.tenant_id == _tid(tenant),
                                       Pipeline.name == name)
            )).scalar_one_or_none()
            if existing:
                raise CRMDuplicate("pipeline", "name", str(existing.id))
            pipeline = Pipeline(tenant_id=_tid(tenant), name=name, object_type=object_type)
            session.add(pipeline)
            await session.flush()
            for i, s in enumerate(stages):
                session.add(PipelineStage(
                    tenant_id=_tid(tenant), pipeline_id=pipeline.id,
                    name=s["name"], position=s.get("position", i),
                    probability=s.get("probability"),
                    is_closed_won=bool(s.get("is_closed_won", False)),
                    is_closed_lost=bool(s.get("is_closed_lost", False)),
                ))
            await session.commit()
            await session.refresh(pipeline)
        await self._emit("crm.pipeline.created", tenant, str(pipeline.id),
                         {"name": name, "actor": _actor(tenant)})
        return pipeline

    async def list_pipelines(self, tenant: TenantContext) -> list[Pipeline]:
        async with self._sessions() as session:
            rows = (await session.execute(
                select(Pipeline).where(Pipeline.tenant_id == _tid(tenant))
                .order_by(Pipeline.created_at)
            )).scalars().all()
            return list(rows)

    async def get_pipeline_with_stages(
        self, tenant: TenantContext, pipeline_id: str
    ) -> tuple[Pipeline, list[PipelineStage]]:
        async with self._sessions() as session:
            pipeline = await self._get(session, tenant, Pipeline, "pipeline", pipeline_id)
            stages = (await session.execute(
                select(PipelineStage)
                .where(PipelineStage.pipeline_id == pipeline.id)
                .order_by(PipelineStage.position)
            )).scalars().all()
            return pipeline, list(stages)

    # ---------------------------------------------------------- opportunities
    async def create_opportunity(
        self, tenant: TenantContext, data: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> Opportunity:
        await self._check_policy(tenant, "crm.opportunity.create",
                                 args={"name": data.get("name")},
                                 idempotency_key=idempotency_key)
        async with self._sessions() as session:
            await self._validate_custom(session, tenant, "opportunity", data.get("custom", {}))
            pipeline, stages = await self.get_pipeline_with_stages(
                tenant, data["pipeline_id"])
            stage_id = data.get("stage_id") or stages[0].id
            if all(s.id != stage_id for s in stages):
                raise CRMValidationError("stage does not belong to pipeline")
            data = {**data, "stage_id": stage_id}
            opp = Opportunity(tenant_id=_tid(tenant), **data)
            session.add(opp)
            await session.commit()
            await session.refresh(opp)
        await self._emit("crm.opportunity.created", tenant, str(opp.id),
                         {"name": opp.name, "actor": _actor(tenant)})
        return opp

    async def get_opportunity(self, tenant: TenantContext, opp_id: str) -> Opportunity:
        async with self._sessions() as session:
            return await self._get(session, tenant, Opportunity, "opportunity", opp_id)

    async def list_opportunities(
        self, tenant: TenantContext, *, pipeline_id: str | None = None,
        stage_id: str | None = None, owner_id: str | None = None,
        limit: int = 50, offset: int = 0,
    ) -> tuple[list[Opportunity], int]:
        async with self._sessions() as session:
            q = select(Opportunity).where(
                Opportunity.tenant_id == _tid(tenant),
                Opportunity.is_archived.is_(False))
            if pipeline_id:
                q = q.where(Opportunity.pipeline_id == pipeline_id)
            if stage_id:
                q = q.where(Opportunity.stage_id == stage_id)
            if owner_id:
                q = q.where(Opportunity.owner_id == owner_id)
            total = (await session.execute(
                select(func.count()).select_from(q.subquery()))).scalar_one()
            rows = (await session.execute(
                q.order_by(Opportunity.created_at.desc()).limit(limit).offset(offset)
            )).scalars().all()
            return list(rows), total

    async def update_opportunity(
        self, tenant: TenantContext, opp_id: str, data: dict[str, Any]
    ) -> Opportunity:
        await self._check_policy(tenant, "crm.opportunity.update",
                                 resource=f"opportunity:{opp_id}")
        async with self._sessions() as session:
            opp = await self._get(session, tenant, Opportunity, "opportunity", opp_id)
            if "custom" in data:
                await self._validate_custom(session, tenant, "opportunity", data["custom"])
            for k, v in data.items():
                setattr(opp, k, v)
            await session.commit()
            await session.refresh(opp)
        await self._emit("crm.opportunity.updated", tenant, str(opp.id),
                         {"actor": _actor(tenant), "fields": sorted(data)})
        return opp

    async def move_opportunity(
        self, tenant: TenantContext, opp_id: str, stage_id: str
    ) -> Opportunity:
        """Stage transition. Closed (won/lost) stages are terminal — fail closed."""
        await self._check_policy(tenant, "crm.opportunity.move",
                                 resource=f"opportunity:{opp_id}",
                                 args={"stage_id": str(stage_id)})
        async with self._sessions() as session:
            opp = await self._get(session, tenant, Opportunity, "opportunity", opp_id)
            pipeline, stages = await self.get_pipeline_with_stages(tenant, opp.pipeline_id)
            by_id = {s.id: s for s in stages}
            target = by_id.get(stage_id)
            if target is None:
                raise CRMValidationError("stage does not belong to the opportunity's pipeline")
            current = by_id.get(opp.stage_id)
            if current and (current.is_closed_won or current.is_closed_lost):
                raise CRMValidationError(
                    f"opportunity is in terminal stage '{current.name}' and cannot move",
                    {"stage": current.name},
                )
            opp.stage_id = stage_id
            await session.commit()
            await session.refresh(opp)
            closed = target.is_closed_won or target.is_closed_lost
        await self._emit("crm.opportunity.stage_changed", tenant, str(opp.id), {
            "from_stage": current.name if current else None,
            "to_stage": target.name, "closed": closed, "actor": _actor(tenant),
        })
        return opp

    # ---------------------------------------------------------- activities
    _SUBJECT_MODELS = {
        "organization": Organization, "contact": Contact,
        "lead": Lead, "opportunity": Opportunity,
    }

    async def log_activity(
        self, tenant: TenantContext, subject_type: str, subject_id: str,
        type: str, body: str | None, author_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> Activity:
        model = self._SUBJECT_MODELS.get(subject_type)
        if model is None:
            raise CRMValidationError(f"unknown subject_type '{subject_type}'")
        await self._check_policy(tenant, "crm.activity.log",
                                 resource=f"{subject_type}:{subject_id}")
        async with self._sessions() as session:
            await self._get(session, tenant, model, subject_type, subject_id)
            activity = Activity(
                tenant_id=_tid(tenant), subject_type=subject_type, subject_id=subject_id,
                type=type, body=body,
                occurred_at=occurred_at or _utcnow(),
                author_id=author_id or (str(tenant.user_id) if tenant.user_id else None),
            )
            session.add(activity)
            await session.commit()
            await session.refresh(activity)
        await self._emit("crm.activity.logged", tenant, str(activity.id), {
            "subject_type": subject_type, "subject_id": str(subject_id),
            "type": type, "actor": _actor(tenant),
        })
        return activity

    async def list_activities(
        self, tenant: TenantContext, *, subject_type: str | None = None,
        subject_id: str | None = None, type: str | None = None,
        limit: int = 50, offset: int = 0,
    ) -> tuple[list[Activity], int]:
        async with self._sessions() as session:
            q = select(Activity).where(Activity.tenant_id == _tid(tenant))
            if subject_type:
                q = q.where(Activity.subject_type == subject_type)
            if subject_id:
                q = q.where(Activity.subject_id == subject_id)
            if type:
                q = q.where(Activity.type == type)
            total = (await session.execute(
                select(func.count()).select_from(q.subquery()))).scalar_one()
            rows = (await session.execute(
                q.order_by(Activity.occurred_at.desc()).limit(limit).offset(offset)
            )).scalars().all()
            return list(rows), total

    # ---------------------------------------------------------- custom fields
    async def create_custom_field(
        self, tenant: TenantContext, data: dict[str, Any]
    ) -> CustomFieldDefinition:
        await self._check_policy(tenant, "crm.custom_field.create", args=data)
        async with self._sessions() as session:
            existing = (await session.execute(
                select(CustomFieldDefinition).where(
                    CustomFieldDefinition.tenant_id == _tid(tenant),
                    CustomFieldDefinition.object_type == data["object_type"],
                    CustomFieldDefinition.name == data["name"],
                )
            )).scalar_one_or_none()
            if existing:
                raise CRMDuplicate("custom_field_definition", "name", str(existing.id))
            if data["field_type"] == "enum" and not data.get("options"):
                raise CRMValidationError("enum custom fields require options")
            cfd = CustomFieldDefinition(tenant_id=_tid(tenant), **data)
            session.add(cfd)
            await session.commit()
            await session.refresh(cfd)
        await self._emit("crm.custom_field.created", tenant, str(cfd.id),
                         {"object_type": cfd.object_type, "name": cfd.name})
        return cfd

    async def list_custom_fields(
        self, tenant: TenantContext, object_type: str | None = None
    ) -> list[CustomFieldDefinition]:
        async with self._sessions() as session:
            q = select(CustomFieldDefinition).where(
                CustomFieldDefinition.tenant_id == _tid(tenant))
            if object_type:
                q = q.where(CustomFieldDefinition.object_type == object_type)
            rows = await session.execute(q.order_by(CustomFieldDefinition.name))
            return list(rows.scalars().all())

    # ---------------------------------------------------------- relationships
    async def link(
        self, tenant: TenantContext, from_type: str, from_id: str,
        to_type: str, to_id: str, relation: str,
    ) -> Relationship:
        await self._check_policy(tenant, "crm.relationship.create",
                                 args={"relation": relation})
        async with self._sessions() as session:
            for t, i, m in ((from_type, from_id, self._SUBJECT_MODELS.get(from_type)),
                            (to_type, to_id, self._SUBJECT_MODELS.get(to_type))):
                if m is None:
                    raise CRMValidationError(f"unknown subject_type '{t}'")
                await self._get(session, tenant, m, t, i)
            rel = Relationship(
                tenant_id=_tid(tenant), from_type=from_type, from_id=from_id,
                to_type=to_type, to_id=to_id, relation=relation,
            )
            session.add(rel)
            try:
                await session.commit()
            except Exception as e:
                await session.rollback()
                raise CRMDuplicate("relationship", "relation", "") from e
            await session.refresh(rel)
        await self._emit("crm.relationship.created", tenant, str(rel.id),
                         {"relation": relation, "actor": _actor(tenant)})
        return rel

    async def list_relationships(
        self, tenant: TenantContext, subject_type: str, subject_id: str
    ) -> list[Relationship]:
        async with self._sessions() as session:
            rows = (await session.execute(
                select(Relationship).where(
                    Relationship.tenant_id == _tid(tenant),
                    ((Relationship.from_type == subject_type)
                     & (Relationship.from_id == subject_id))
                    | ((Relationship.to_type == subject_type)
                       & (Relationship.to_id == subject_id)),
                )
            )).scalars().all()
            return list(rows)


# re-export for the workflows port adapters
__all__ = [
    "CRMService", "CRMError", "CRMNotFound", "CRMDuplicate",
    "CRMValidationError", "PolicyDeniedError",
]
