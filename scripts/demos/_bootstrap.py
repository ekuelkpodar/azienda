"""Shared bootstrap for the Azienda signature demos (scripts/demos/).

Each demo runs against a FRESH SQLite database in /tmp — no live Postgres,
no live LLM, no real comms providers. The `log_only` comms provider records
messages without transmitting anything (honest: nothing leaves the machine).

Service-layer driven (like the repo's own test suite), because the demos
compose cross-package flows (CRM + approvals + comms + audit + AGRL) that
don't have unified HTTP wiring yet (command-center/agents auth is 501).
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

# backend/ on sys.path so `app` imports.
BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.core.contracts import (  # noqa: E402
    ActionRequest,
    PolicyDecision,
    PolicyEffect,
    PolicyEngine,
    TenantContext,
)
from app.core.events import InProcessEventBus  # noqa: E402
from app.core.models import Base  # noqa: E402

# Import every model module so Base.metadata covers all tables.
import app.core.models  # noqa: F401,E402
import app.crm.models  # noqa: F401,E402
import app.tasks.models  # noqa: F401,E402
import app.workflows.models  # noqa: F401,E402
import app.governance.models  # noqa: F401,E402
import app.billing.models  # noqa: F401,E402
import app.comms.models  # noqa: F401,E402
import app.marketing.models  # noqa: F401,E402
import app.support.models  # noqa: F401,E402
import app.scheduling.models  # noqa: F401,E402
import app.finance.models  # noqa: F401,E402
import app.agents.models  # noqa: F401,E402
# NOTE: app.memory and app.knowledge are in-memory stores at MVP (no SQL
# models) — see their READMEs. Nothing to register for them here.

from app.api.routers._common import build_services  # noqa: E402
from app.governance.approvals.store import ApprovalStoreImpl  # noqa: E402
from app.governance.audit.ledger import AuditLedgerImpl  # noqa: E402
from app.governance.budgets.enforcer import BudgetEnforcerImpl  # noqa: E402


class ScriptedPolicy(PolicyEngine):
    """Deterministic demo policy: REQUIRE_APPROVAL for send-like actions,
    allow everything else. No LLM, no randomness."""

    def __init__(self, approval_prefixes: tuple[str, ...] = ()) -> None:
        self.approval_prefixes = approval_prefixes
        self.denied: list[str] = []

    async def evaluate(self, request: ActionRequest) -> PolicyDecision:
        if any(request.action.startswith(p) for p in self.approval_prefixes):
            return PolicyDecision(
                effect=PolicyEffect.REQUIRE_APPROVAL,
                policy_id="demo-scripted",
                reasons=(f"demo policy: human approval required for {request.action}",),
            )
        return PolicyDecision(
            effect=PolicyEffect.ALLOW,
            policy_id="demo-scripted",
            reasons=("demo policy: low-risk action auto-allowed",),
        )


class Demo:
    """Per-step PASS/FAIL reporter with a final verdict."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._results: list[tuple[str, bool]] = []
        print(f"\n{'=' * 70}\nDEMO: {name}\n{'=' * 70}")

    def step(self, name: str, ok: bool, detail: str = "") -> bool:
        self._results.append((name, bool(ok)))
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))
        return bool(ok)

    def verdict(self) -> bool:
        passed = sum(1 for _, ok in self._results if ok)
        total = len(self._results)
        ok = passed == total
        print(f"\n  VERDICT: {'PASS' if ok else 'FAIL'} "
              f"({passed}/{total} steps)\n{'=' * 70}\n")
        return ok


class Ctx:
    """Wired demo context: one tenant, all services, shared DB session."""

    def __init__(self) -> None:
        self.db_path = f"/tmp/azienda_demo_{uuid.uuid4().hex[:8]}.db"
        self.settings = Settings()
        self.settings.approval_ttl_seconds = 3600
        self.tenant = TenantContext(
            tenant_id=f"demo-{uuid.uuid4().hex[:8]}", user_id="demo-operator")

    async def start(self) -> "Ctx":
        # One shared connection (StaticPool): SQLite serializes cleanly and
        # every service sees the same data in this single-threaded demo.
        self.engine = create_async_engine(
            f"sqlite+aiosqlite:///{self.db_path}",
            poolclass=StaticPool, connect_args={"check_same_thread": False})
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        self.sf = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False)
        self.bus = InProcessEventBus()
        self.policy = ScriptedPolicy()
        self.services = build_services(
            session_factory=self.sf, bus=self.bus, policy=self.policy)
        self.db: AsyncSession = self.sf()
        self.approvals = ApprovalStoreImpl(self.db, self.settings)
        self.audit = AuditLedgerImpl(self.db)
        self.budgets = BudgetEnforcerImpl(self.db, self.settings)
        return self

    async def stop(self) -> None:
        await self.db.close()
        await self.engine.dispose()
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    async def audit_step(self, actor: str, action: str,
                         payload: dict) -> None:
        await self.audit.append(self.tenant, actor, action, payload)
        await self.db.commit()
