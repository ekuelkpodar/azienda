"""ACP tables: agents/tools/models/mcp, memory+knowledge, AGRL ledger.

Revision ID: 2fd0cc725f7b
Revises: None (standalone chain head — the coordinator sequences this into the
linear history after the base migrations; RLS policies land in the dedicated
RLS migration per DATABASE.md §12).

Tables follow DATABASE.md §6 (agents/tools/models), §8 (memory/knowledge),
§9 (AGRL). Every table carries tenant_id (application-enforced scoping;
Postgres RLS is applied by the RLS migration, not here).
"""

from __future__ import annotations

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

# revision identifiers, used by Alembic.
revision = "2fd0cc725f7b"
down_revision = "0004_crm_tasks_workflows"  # linear history: build on the current head
branch_labels = None
depends_on = None


def _is_postgres() -> bool:
    """True when the migration bind is Postgres.

    ARRAY / Vector / pg functions (gen_random_uuid, now) are Postgres-only.
    SQLite (tests/dev) gets faithful fallbacks; Python-side model defaults
    (new_uuid/utcnow) live in app code, not in DDL, per DATABASE.md.
    """
    try:
        return op.get_bind().dialect.name == "postgresql"
    except Exception:
        return False


def _pg_default(expr: str):
    """Postgres server default; None elsewhere.

    SQLite rejects unknown functions in DEFAULT clauses at DDL time, so the
    defaults stay Postgres-only. Models provide Python-side defaults.
    """
    return sa.text(expr) if _is_postgres() else None


def _text_array():
    """Postgres TEXT[]; JSON elsewhere (SQLite tests)."""
    return sa.ARRAY(sa.Text()).with_variant(sa.JSON(), "sqlite")


def _embedding():
    """pgvector VECTOR(1536) on Postgres; JSON elsewhere (SQLite tests)."""
    return Vector(1536).with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    # ---- agents / tools / models / mcp (DATABASE.md §6) ----
    op.create_table(
        "agents",
        sa.Column("id", sa.Uuid(), server_default=_pg_default("gen_random_uuid()"),
                  primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=_pg_default("now()"), nullable=False),
    )
    op.create_table(
        "agent_versions",
        sa.Column("id", sa.Uuid(), server_default=_pg_default("gen_random_uuid()"),
                  primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("capabilities", _text_array(), nullable=False,
                  server_default="{}"),
        sa.Column("allowed_tools", _text_array(), nullable=False,
                  server_default="{}"),
        sa.Column("autonomy_level", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("model_prefs", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("published_at", sa.DateTime(timezone=True),
                  server_default=_pg_default("now()"), nullable=False),
        sa.UniqueConstraint("agent_id", "version", name="uq_agent_versions_agent_version"),
    )
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Uuid(), server_default=_pg_default("gen_random_uuid()"),
                  primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("version_id", sa.Uuid(), sa.ForeignKey("agent_versions.id")),
        sa.Column("task_id", sa.Uuid()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("policy_decisions", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("cost_usd", sa.Numeric(19, 4), nullable=False,
                  server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True),
                  server_default=_pg_default("now()"), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "tool_registry",
        sa.Column("id", sa.Uuid(), server_default=_pg_default("gen_random_uuid()"),
                  primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("input_schema", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("risk_tier", sa.Text(), nullable=False, server_default="low"),
        sa.Column("idempotent", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("source", sa.Text(), nullable=False, server_default="local"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=_pg_default("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "name", name="uq_tool_registry_tenant_name"),
    )
    op.create_table(
        "tool_calls",
        sa.Column("id", sa.Uuid(), server_default=_pg_default("gen_random_uuid()"),
                  primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id")),
        sa.Column("task_id", sa.Uuid()),
        sa.Column("tool_name", sa.Text(), nullable=False),
        sa.Column("arguments", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("grant_id", sa.Text(), nullable=False),
        sa.Column("policy_decision", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("result", sa.JSON()),
        sa.Column("cost_usd", sa.Numeric(19, 4), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=_pg_default("now()"), nullable=False),
    )
    op.create_table(
        "model_registry",
        sa.Column("id", sa.Uuid(), server_default=_pg_default("gen_random_uuid()"),
                  primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("cost_per_1k_in", sa.Numeric(19, 6), nullable=False,
                  server_default="0"),
        sa.Column("cost_per_1k_out", sa.Numeric(19, 6), nullable=False,
                  server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_model_registry_tenant_name"),
    )
    op.create_table(
        "mcp_servers",
        sa.Column("id", sa.Uuid(), server_default=_pg_default("gen_random_uuid()"),
                  primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("transport", sa.Text(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("auth_ref", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False, server_default="untrusted"),
        sa.Column("vetted_at", sa.DateTime(timezone=True)),
        sa.Column("vetted_by", sa.Text()),
        sa.UniqueConstraint("tenant_id", "name", name="uq_mcp_servers_tenant_name"),
    )

    # ---- memory / knowledge (DATABASE.md §8) ----
    op.create_table(
        "memory_namespaces",
        sa.Column("id", sa.Uuid(), server_default=_pg_default("gen_random_uuid()"),
                  primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False, server_default="short_term"),
        sa.Column("ttl_default_seconds", sa.Integer()),
        sa.Column("retention_days", sa.Integer(), nullable=False,
                  server_default="90"),
        sa.UniqueConstraint("tenant_id", "name",
                            name="uq_memory_namespaces_tenant_name"),
    )
    op.create_table(
        "memory_items",
        sa.Column("id", sa.Uuid(), server_default=_pg_default("gen_random_uuid()"),
                  primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("namespace_id", sa.Uuid(),
                  sa.ForeignKey("memory_namespaces.id"), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("provenance", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False,
                  server_default="0.8"),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=_pg_default("now()"), nullable=False),
        sa.UniqueConstraint("namespace_id", "key",
                            name="uq_memory_items_namespace_key"),
    )
    op.create_table(
        "knowledge_sources",
        sa.Column("id", sa.Uuid(), server_default=_pg_default("gen_random_uuid()"),
                  primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False, server_default="document"),
        sa.Column("uri", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("last_ingested_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
    )
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), server_default=_pg_default("gen_random_uuid()"),
                  primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), sa.ForeignKey("knowledge_sources.id")),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("uri", sa.Text(), nullable=False, server_default=""),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("checksum", sa.Text(), nullable=False, server_default=""),
    )
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Uuid(), server_default=_pg_default("gen_random_uuid()"),
                  primary_key=True),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id"),
                  nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", _embedding()),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.create_index("ix_document_chunks_embedding_hnsw", "document_chunks",
                    ["embedding"], postgresql_using="hnsw",
                    postgresql_with={"m": 16, "ef_construction": 64},
                    postgresql_ops={"embedding": "vector_cosine_ops"})
    op.create_table(
        "knowledge_edges",
        sa.Column("id", sa.Uuid(), server_default=_pg_default("gen_random_uuid()"),
                  primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("from_chunk_id", sa.Uuid(), sa.ForeignKey("document_chunks.id"),
                  nullable=False),
        sa.Column("to_chunk_id", sa.Uuid(), sa.ForeignKey("document_chunks.id"),
                  nullable=False),
        sa.Column("relation", sa.Text(), nullable=False),
        sa.Column("weight", sa.Numeric(8, 4), nullable=False, server_default="1"),
    )

    # ---- AGRL (DATABASE.md §9) — ONE ledger ----
    op.create_table(
        "agrl_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("causation_id", sa.Uuid()),
        sa.Column("correlation_id", sa.Uuid()),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("prev_hash", sa.Text(), nullable=False),
        sa.Column("hash", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True),
                  server_default=_pg_default("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "seq", name="uq_agrl_events_tenant_seq"),
    )
    op.create_index("ix_agrl_events_aggregate", "agrl_events",
                    ["tenant_id", "aggregate_id", "seq"])
    op.create_table(
        "agrl_projections",
        sa.Column("id", sa.Uuid(), server_default=_pg_default("gen_random_uuid()"),
                  primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("projection", sa.Text(), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("at_seq", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=_pg_default("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "projection", "aggregate_id",
                            name="uq_agrl_projections_tenant_proj_agg"),
    )
    op.create_table(
        "goals",
        sa.Column("id", sa.Uuid(), server_default=_pg_default("gen_random_uuid()"),
                  primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("priority", sa.Numeric(5, 4), nullable=False, server_default="0.5"),
        sa.Column("owner_id", sa.Uuid()),
        sa.Column("parent_goal_id", sa.Uuid(), sa.ForeignKey("goals.id")),
        sa.Column("constraints", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("target_date", sa.Date()),
    )
    op.create_table(
        "goal_links",
        sa.Column("id", sa.Uuid(), server_default=_pg_default("gen_random_uuid()"),
                  primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("from_goal_id", sa.Uuid(), sa.ForeignKey("goals.id"),
                  nullable=False),
        sa.Column("to_goal_id", sa.Uuid(), sa.ForeignKey("goals.id"),
                  nullable=False),
        sa.Column("relation", sa.Text(), nullable=False),
        sa.Column("resolution", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=_pg_default("now()"), nullable=False),
    )
    op.create_table(
        "resource_allocations",
        sa.Column("id", sa.Uuid(), server_default=_pg_default("gen_random_uuid()"),
                  primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("resource_type", sa.Text(), nullable=False),
        sa.Column("resource_ref", sa.Text(), nullable=False),
        sa.Column("goal_id", sa.Uuid(), sa.ForeignKey("goals.id")),
        sa.Column("amount", sa.Numeric(19, 4), nullable=False),
        sa.Column("unit", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("valid_from", sa.DateTime(timezone=True)),
        sa.Column("valid_to", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    for table in ("resource_allocations", "goal_links", "goals",
                  "agrl_projections", "agrl_events",
                  "knowledge_edges", "document_chunks", "documents",
                  "knowledge_sources", "memory_items", "memory_namespaces",
                  "mcp_servers", "model_registry", "tool_calls",
                  "tool_registry", "agent_runs", "agent_versions", "agents"):
        op.drop_table(table)
