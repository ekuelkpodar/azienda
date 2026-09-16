"""Operator CLI: ``python -m app.cli.main <command>``.

User/tenant administration, audit verification, plan seeding. (Seed-demo is
reserved for another builder — this CLI never fabricates demo business data.)
"""
from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from decimal import Decimal
from typing import Any

import typer

from app.core.auth import AuthService
from app.core.config import settings
from app.core.contracts import TenantContext
from app.core.db import init_engine, session_scope
from app.governance.approvals.store import ApprovalStoreImpl
from app.governance.audit.ledger import AuditLedgerImpl
from app.governance.policy.engine import DEFAULT_TENANT_RULES

app = typer.Typer(name="azienda", help="Azienda operator CLI")


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


@app.command()
def tenant_create(name: str, email: str, password: str,
                  display_name: str = "") -> None:
    """Create a tenant with an owner user (bootstrap / support tool)."""
    async def _go() -> None:
        init_engine(settings)
        async with session_scope() as db:
            auth = AuthService(db, settings)
            tenant, user, _ = await auth.register_tenant(
                name, email, password, display_name)
            typer.echo(f"tenant: {tenant.id} ({tenant.slug})")
            typer.echo(f"owner:  {user.id} ({user.email})")
            typer.echo("access token issued (not printed; use login to obtain)")
    _run(_go())


@app.command()
def user_create(tenant_id: str, email: str, password: str,
                role: str = "member", display_name: str = "") -> None:
    """Create a user in an existing tenant."""
    async def _go() -> None:
        init_engine(settings)
        async with session_scope() as db:
            auth = AuthService(db, settings)
            view = await auth.create_user(tenant_id, email, password,
                                          display_name, [role])
            typer.echo(f"user: {view['id']} ({view['email']}) roles={view['roles']}")
    _run(_go())


@app.command()
def user_set_role(tenant_id: str, email: str, roles: str) -> None:
    """Replace a user's roles (comma-separated)."""
    from sqlalchemy import select

    from app.core.models import User

    async def _go() -> None:
        init_engine(settings)
        async with session_scope() as db:
            auth = AuthService(db, settings)
            user = (await db.execute(
                select(User).where(User.tenant_id == tenant_id,
                                   User.email == email.lower())
            )).scalar_one_or_none()
            if user is None:
                raise typer.BadParameter("user not found")
            view = await auth.update_user(tenant_id, user.id,
                                          roles=[r.strip() for r in roles.split(",")])
            typer.echo(f"user: {view['id']} roles={view['roles']}")
    _run(_go())


@app.command()
def user_deactivate(tenant_id: str, email: str) -> None:
    """Deactivate a user (they can no longer authenticate)."""
    from sqlalchemy import select

    from app.core.models import User

    async def _go() -> None:
        init_engine(settings)
        async with session_scope() as db:
            auth = AuthService(db, settings)
            user = (await db.execute(
                select(User).where(User.tenant_id == tenant_id,
                                   User.email == email.lower())
            )).scalar_one_or_none()
            if user is None:
                raise typer.BadParameter("user not found")
            await auth.update_user(tenant_id, user.id, is_active=False)
            typer.echo(f"deactivated {email}")
    _run(_go())


@app.command()
def apikey_create(tenant_id: str, name: str, scopes: str = "",
                  expires_days: int = 0) -> None:
    """Create a scoped API key. The full key is printed ONCE."""
    async def _go() -> None:
        init_engine(settings)
        async with session_scope() as db:
            auth = AuthService(db, settings)
            view, presented = await auth.create_api_key(
                tenant_id, None, name,
                [s.strip() for s in scopes.split(",") if s.strip()],
                expires_days or None)
            typer.echo(f"key id: {view['id']}")
            typer.echo(f"api_key: {presented}")
            typer.echo("STORE THIS NOW — it will never be shown again.")
    _run(_go())


@app.command()
def audit_verify(tenant_id: str, from_seq: int = 1) -> None:
    """Verify the hash-chained audit ledger for a tenant."""
    async def _go() -> bool:  # noqa: ANN202
        init_engine(settings)
        async with session_scope() as db:
            ledger = AuditLedgerImpl(db)
            ok = await ledger.verify_chain(TenantContext(tenant_id=tenant_id),
                                           from_seq=from_seq)
            typer.echo("CHAIN OK" if ok else "CHAIN BROKEN")
            return ok
    ok = _run(_go())
    raise typer.Exit(0 if ok else 2)


@app.command()
def approvals_sweep(tenant_id: str = "") -> None:
    """Mark expired approvals EXPIRED (timeout == DENY)."""
    async def _go() -> int:  # noqa: ANN202
        init_engine(settings)
        async with session_scope() as db:
            store = ApprovalStoreImpl(db, settings)
            tenant = TenantContext(tenant_id=tenant_id) if tenant_id else None
            n = await store.sweep_expired(tenant)
            typer.echo(f"expired {n} approval(s)")
            return n
    _run(_go())


@app.command()
def billing_seed_plans() -> None:
    """Seed the plan catalogue (pricing as data)."""
    from app.billing.service import BillingService

    async def _go() -> int:  # noqa: ANN202
        init_engine(settings)
        async with session_scope() as db:
            service = BillingService(db, settings)
            n = await service.seed_plans()
            typer.echo(f"seeded {n} plan(s)")
            return n
    _run(_go())


@app.command()
def policy_seed_defaults(tenant_id: str) -> None:
    """Install the default tenant policy rule set."""
    from app.governance.models import Policy

    async def _go() -> None:
        init_engine(settings)
        async with session_scope() as db:
            policy = Policy(tenant_id=tenant_id, name="default-tenant-policy",
                            description="Seeded default rule set",
                            rules=DEFAULT_TENANT_RULES, priority=100,
                            is_active=True, version=1)
            db.add(policy)
            await db.flush()
            typer.echo(f"policy: {policy.id} ({len(DEFAULT_TENANT_RULES)} rules)")
    _run(_go())


@app.command()
def budget_create(tenant_id: str, name: str, credit_limit: str,
                  period: str = "monthly") -> None:
    """Create a tenant-scope budget."""
    from app.governance.budgets.enforcer import BudgetEnforcerImpl

    async def _go() -> None:
        init_engine(settings)
        async with session_scope() as db:
            enforcer = BudgetEnforcerImpl(db, settings)
            budget = await enforcer.create_budget(
                TenantContext(tenant_id=tenant_id), name,
                Decimal(credit_limit), period)
            typer.echo(f"budget: {budget.id} ({budget.credit_limit} credits/{period})")
    _run(_go())


if __name__ == "__main__":
    app()
