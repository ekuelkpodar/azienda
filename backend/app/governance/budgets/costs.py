"""Cost recording: the hook the agents package calls for every model/tool cost.

``CostRecorderImpl.record`` does three things atomically:
1. Appends a ``cost_ledger`` row (governance-owned table) — the audit trail of spend.
2. Draws credits from the billing credit pool via the injected ``CreditLedger``
   protocol (billing owns ``credit_transactions``) — the money movement.
3. Emits a ``cost.recorded`` domain event for the outcome ledger / command center.

Fail-closed note: if the credit draw raises (e.g. spend cap hit), the whole
record is rolled back — cost is never silently unmetered.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.contracts import CostRecord, CostRecorder, CreditLedger, EventBus, TenantContext
from app.core.events import new_event
from app.governance.models import CostLedgerEntry


class CostRecorderImpl(CostRecorder):
    def __init__(self, db: AsyncSession, credit_ledger: CreditLedger,
                 bus: EventBus | None = None) -> None:
        self.db = db
        self.credit_ledger = credit_ledger
        self.bus = bus

    async def record(self, record: CostRecord) -> None:
        tenant = TenantContext(tenant_id=record.tenant_id)
        self.db.add(CostLedgerEntry(
            tenant_id=record.tenant_id,
            task_id=record.task_id,
            run_id=None,
            model=record.model,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            cost_usd=record.cost_usd,
            credits_drawn=record.credits_drawn,
            recorded_at=record.recorded_at or datetime.now(UTC)))
        if record.credits_drawn > 0:
            await self.credit_ledger.draw(
                tenant, record.credits_drawn,
                reason=f"model={record.model} task={record.task_id or '-'}",
                task_id=record.task_id)
        await self.db.flush()
        if self.bus is not None:
            await self.bus.publish(new_event(
                "cost.recorded", record.tenant_id, record.task_id or record.tenant_id,
                {"model": record.model,
                 "input_tokens": record.input_tokens,
                 "output_tokens": record.output_tokens,
                 "cost_usd": str(record.cost_usd),
                 "credits_drawn": str(record.credits_drawn)}))
