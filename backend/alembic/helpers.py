"""Shared helpers for Azienda alembic migrations.

RLS (DATABASE.md §1, ADR-008): every tenant table gets
``ENABLE ROW LEVEL SECURITY`` + a ``tenant_isolation`` policy bound to the
``app_role`` role, reading ``current_setting('app.tenant_id', true)``.
``missing_ok=true`` makes an unset variable fail CLOSED (comparison to NULL
matches nothing). Append-only tables additionally revoke UPDATE/DELETE.
"""
from __future__ import annotations

from alembic import op


def ensure_app_role() -> None:
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_role') THEN "
        "CREATE ROLE app_role; END IF; END $$;")


def apply_rls(table: str, append_only: bool = False,
              tenant_col: str = "tenant_id") -> None:
    """Enable RLS on a tenant table (Postgres only; no-op elsewhere)."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    ensure_app_role()
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"DROP POLICY IF EXISTS tenant_isolation ON {table}; "
        f"CREATE POLICY tenant_isolation ON {table} FOR ALL TO app_role "
        f"USING ({tenant_col} = current_setting('app.tenant_id', true)::uuid)")
    if append_only:
        op.execute(f"REVOKE ALL ON {table} FROM app_role")
        op.execute(f"GRANT SELECT, INSERT ON {table} TO app_role")
        op.execute(f"REVOKE ALL ON SEQUENCE {table}_id_seq FROM app_role")
        op.execute(f"GRANT USAGE, SELECT ON SEQUENCE {table}_id_seq TO app_role")
    else:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO app_role")


def drop_rls(table: str) -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
