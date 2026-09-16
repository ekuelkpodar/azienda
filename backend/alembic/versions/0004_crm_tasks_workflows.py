"""CRM, tasks/projects, and workflows tables.

Revision ID: 0004_crm_tasks_workflows
Revises: e4f7a2b91c5d (current linear head at time of writing)

Tables (all tenant-scoped via tenant_id, application-enforced; Postgres RLS
lands in the dedicated RLS migration per DATABASE.md §12):

- CRM: organizations, contacts, leads, pipelines, pipeline_stages,
  opportunities, activities, custom_field_definitions, crm_relationships
- Tasks/projects: projects, milestones, tasks, task_dependencies,
  task_transitions, task_comments, task_bulk_op_records
- Workflows: workflow_definitions, workflow_versions, workflow_executions,
  execution_events

ID columns use the frozen _GUID type below, mirroring app.core.models.GUID
(CHAR(36) on SQLite, native UUID on Postgres). Python-side defaults
(new_uuid()/utcnow()) live in the models, not in DDL.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as _PG_UUID
from sqlalchemy.types import CHAR, TypeDecorator

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_crm_tasks_workflows"
down_revision: str | Sequence[str] | None = "e4f7a2b91c5d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


class _GUID(TypeDecorator):
    """Frozen copy of app.core.models.GUID as of this revision."""

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):  # noqa: ANN001, ANN202
        if dialect.name == "postgresql":
            return dialect.type_descriptor(_PG_UUID(as_uuid=False))
        return CHAR(36)


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("domain", sa.String(255)),
        sa.Column("industry", sa.String(128)),
        sa.Column("size_band", sa.String(32)),
        sa.Column("lifecycle_stage", sa.String(64), nullable=False),
        sa.Column("owner_id", sa.String(64)),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("custom", sa.JSON(), nullable=False),
        sa.Column("is_archived", sa.Boolean(), nullable=False),
        sa.Column("id", _GUID(), primary_key=True),
        sa.Column("tenant_id", _GUID(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_organizations_tenant_id", "organizations", ["tenant_id"]
    )

    op.create_table(
        "contacts",
        sa.Column("org_id", _GUID(), sa.ForeignKey("organizations.id")),
        sa.Column("first_name", sa.String(128), nullable=False),
        sa.Column("last_name", sa.String(128)),
        sa.Column("email", sa.String(255)),
        sa.Column("phone", sa.String(64)),
        sa.Column("title", sa.String(255)),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("custom", sa.JSON(), nullable=False),
        sa.Column("is_archived", sa.Boolean(), nullable=False),
        sa.Column("id", _GUID(), primary_key=True),
        sa.Column("tenant_id", _GUID(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_contacts_email", "contacts", ["email"]
    )
    op.create_index(
        "ix_contacts_org_id", "contacts", ["org_id"]
    )
    op.create_index(
        "ix_contacts_tenant_id", "contacts", ["tenant_id"]
    )

    op.create_table(
        "leads",
        sa.Column("contact_id", _GUID(), sa.ForeignKey("contacts.id")),
        sa.Column("org_id", _GUID(), sa.ForeignKey("organizations.id")),
        sa.Column("source", sa.String(64)),
        sa.Column("status", sa.String(64), nullable=False),
        sa.Column("score", sa.Integer()),
        sa.Column("score_breakdown", sa.JSON()),
        sa.Column("owner_id", sa.String(64)),
        sa.Column("custom", sa.JSON(), nullable=False),
        sa.Column("converted_at", sa.DateTime(timezone=True)),
        sa.Column("id", _GUID(), primary_key=True),
        sa.Column("tenant_id", _GUID(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_leads_owner_id", "leads", ["owner_id"]
    )
    op.create_index(
        "ix_leads_status", "leads", ["status"]
    )
    op.create_index(
        "ix_leads_tenant_id", "leads", ["tenant_id"]
    )

    op.create_table(
        "pipelines",
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("object_type", sa.String(64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("id", _GUID(), primary_key=True),
        sa.Column("tenant_id", _GUID(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "name", name="uq_pipelines_tenant_name"),
    )
    op.create_index(
        "ix_pipelines_tenant_id", "pipelines", ["tenant_id"]
    )

    op.create_table(
        "pipeline_stages",
        sa.Column(
            "pipeline_id",
            _GUID(),
            sa.ForeignKey("pipelines.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("probability", sa.Numeric(5, 2)),
        sa.Column("is_closed_won", sa.Boolean(), nullable=False),
        sa.Column("is_closed_lost", sa.Boolean(), nullable=False),
        sa.Column("id", _GUID(), primary_key=True),
        sa.Column("tenant_id", _GUID(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_pipeline_stages_pipeline_id", "pipeline_stages", ["pipeline_id"]
    )
    op.create_index(
        "ix_pipeline_stages_tenant_id", "pipeline_stages", ["tenant_id"]
    )

    op.create_table(
        "opportunities",
        sa.Column("pipeline_id", _GUID(), sa.ForeignKey("pipelines.id"), nullable=False),
        sa.Column("stage_id", _GUID(), sa.ForeignKey("pipeline_stages.id"), nullable=False),
        sa.Column("org_id", _GUID(), sa.ForeignKey("organizations.id")),
        sa.Column("contact_id", _GUID(), sa.ForeignKey("contacts.id")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("amount", sa.Numeric(19, 4)),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("close_date", sa.Date()),
        sa.Column("owner_id", sa.String(64)),
        sa.Column("custom", sa.JSON(), nullable=False),
        sa.Column("is_archived", sa.Boolean(), nullable=False),
        sa.Column("id", _GUID(), primary_key=True),
        sa.Column("tenant_id", _GUID(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_opportunities_org_id", "opportunities", ["org_id"]
    )
    op.create_index(
        "ix_opportunities_owner_id", "opportunities", ["owner_id"]
    )
    op.create_index(
        "ix_opportunities_pipeline_id", "opportunities", ["pipeline_id"]
    )
    op.create_index(
        "ix_opportunities_stage_id", "opportunities", ["stage_id"]
    )
    op.create_index(
        "ix_opportunities_tenant_id", "opportunities", ["tenant_id"]
    )

    op.create_table(
        "activities",
        sa.Column("subject_type", sa.String(64), nullable=False),
        sa.Column("subject_id", _GUID(), nullable=False),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("body", sa.Text()),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("author_id", sa.String(64)),
        sa.Column("id", _GUID(), primary_key=True),
        sa.Column("tenant_id", _GUID(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_activities_subject_id", "activities", ["subject_id"]
    )
    op.create_index(
        "ix_activities_subject_type", "activities", ["subject_type"]
    )
    op.create_index(
        "ix_activities_tenant_id", "activities", ["tenant_id"]
    )

    op.create_table(
        "custom_field_definitions",
        sa.Column("object_type", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("field_type", sa.String(32), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("options", sa.JSON()),
        sa.Column("id", _GUID(), primary_key=True),
        sa.Column("tenant_id", _GUID(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "object_type", "name", name="uq_cfd_tenant_obj_name"),
    )
    op.create_index(
        "ix_custom_field_definitions_tenant_id", "custom_field_definitions", ["tenant_id"]
    )

    op.create_table(
        "crm_relationships",
        sa.Column("from_type", sa.String(64), nullable=False),
        sa.Column("from_id", _GUID(), nullable=False),
        sa.Column("to_type", sa.String(64), nullable=False),
        sa.Column("to_id", _GUID(), nullable=False),
        sa.Column("relation", sa.String(64), nullable=False),
        sa.Column("id", _GUID(), primary_key=True),
        sa.Column("tenant_id", _GUID(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "from_type",
            "from_id",
            "to_type",
            "to_id",
            "relation",
            name="uq_crm_rel",
        ),
    )
    op.create_index(
        "ix_crm_relationships_from_id", "crm_relationships", ["from_id"]
    )
    op.create_index(
        "ix_crm_relationships_tenant_id", "crm_relationships", ["tenant_id"]
    )
    op.create_index(
        "ix_crm_relationships_to_id", "crm_relationships", ["to_id"]
    )

    op.create_table(
        "projects",
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("owner_id", sa.String(64)),
        sa.Column("id", _GUID(), primary_key=True),
        sa.Column("tenant_id", _GUID(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_projects_tenant_id", "projects", ["tenant_id"]
    )

    op.create_table(
        "milestones",
        sa.Column(
            "project_id",
            _GUID(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("due_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("id", _GUID(), primary_key=True),
        sa.Column("tenant_id", _GUID(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_milestones_project_id", "milestones", ["project_id"]
    )
    op.create_index(
        "ix_milestones_tenant_id", "milestones", ["tenant_id"]
    )

    op.create_table(
        "tasks",
        sa.Column("project_id", _GUID(), sa.ForeignKey("projects.id")),
        sa.Column("parent_id", _GUID(), sa.ForeignKey("tasks.id", ondelete="CASCADE")),
        sa.Column("milestone_id", _GUID(), sa.ForeignKey("milestones.id")),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False),
        sa.Column("assignee_user_id", sa.String(64)),
        sa.Column("assignee_agent_id", sa.String(128)),
        sa.Column("plan", sa.JSON(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(19, 4)),
        sa.Column("due_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("idempotency_key", sa.String(128)),
        sa.Column("is_archived", sa.Boolean(), nullable=False),
        sa.Column("id", _GUID(), primary_key=True),
        sa.Column("tenant_id", _GUID(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_tasks_tenant_idem"),
    )
    op.create_index(
        "ix_tasks_assignee_user_id", "tasks", ["assignee_user_id"]
    )
    op.create_index(
        "ix_tasks_parent_id", "tasks", ["parent_id"]
    )
    op.create_index(
        "ix_tasks_project_id", "tasks", ["project_id"]
    )
    op.create_index(
        "ix_tasks_status", "tasks", ["status"]
    )
    op.create_index(
        "ix_tasks_tenant_id", "tasks", ["tenant_id"]
    )

    op.create_table(
        "task_dependencies",
        sa.Column("id", _GUID(), primary_key=True),
        sa.Column("tenant_id", _GUID(), nullable=False),
        sa.Column(
            "task_id",
            _GUID(),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "depends_on_id",
            _GUID(),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "task_id", "depends_on_id", name="uq_task_dep"),
    )
    op.create_index(
        "ix_task_dependencies_depends_on_id", "task_dependencies", ["depends_on_id"]
    )
    op.create_index(
        "ix_task_dependencies_task_id", "task_dependencies", ["task_id"]
    )
    op.create_index(
        "ix_task_dependencies_tenant_id", "task_dependencies", ["tenant_id"]
    )

    op.create_table(
        "task_transitions",
        sa.Column("id", _GUID(), primary_key=True),
        sa.Column("tenant_id", _GUID(), nullable=False),
        sa.Column(
            "task_id",
            _GUID(),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("from_status", sa.String(32), nullable=False),
        sa.Column("to_status", sa.String(32), nullable=False),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_task_transitions_task_id", "task_transitions", ["task_id"]
    )
    op.create_index(
        "ix_task_transitions_tenant_id", "task_transitions", ["tenant_id"]
    )

    op.create_table(
        "task_comments",
        sa.Column(
            "task_id",
            _GUID(),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("author_id", sa.String(64)),
        sa.Column("author_type", sa.String(16), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("id", _GUID(), primary_key=True),
        sa.Column("tenant_id", _GUID(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_task_comments_task_id", "task_comments", ["task_id"]
    )
    op.create_index(
        "ix_task_comments_tenant_id", "task_comments", ["tenant_id"]
    )

    op.create_table(
        "task_bulk_op_records",
        sa.Column("id", _GUID(), primary_key=True),
        sa.Column("tenant_id", _GUID(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("op", sa.String(32), nullable=False),
        sa.Column("task_id", _GUID(), sa.ForeignKey("tasks.id", ondelete="SET NULL")),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_bulkop_tenant_key"),
    )
    op.create_index(
        "ix_task_bulk_op_records_tenant_id", "task_bulk_op_records", ["tenant_id"]
    )

    op.create_table(
        "workflow_definitions",
        sa.Column("id", _GUID(), primary_key=True),
        sa.Column("tenant_id", _GUID(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("autonomy_level", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("draft_dag", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "name", name="uq_wf_def_tenant_name"),
    )
    op.create_index(
        "ix_workflow_definitions_tenant_id", "workflow_definitions", ["tenant_id"]
    )

    op.create_table(
        "workflow_versions",
        sa.Column("id", _GUID(), primary_key=True),
        sa.Column("tenant_id", _GUID(), nullable=False),
        sa.Column(
            "definition_id",
            _GUID(),
            sa.ForeignKey("workflow_definitions.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("dag", sa.JSON(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_by", sa.String(255)),
        sa.UniqueConstraint("definition_id", "version", name="uq_wf_ver_def_version"),
    )
    op.create_index(
        "ix_workflow_versions_definition_id", "workflow_versions", ["definition_id"]
    )
    op.create_index(
        "ix_workflow_versions_tenant_id", "workflow_versions", ["tenant_id"]
    )

    op.create_table(
        "workflow_executions",
        sa.Column("id", _GUID(), primary_key=True),
        sa.Column("tenant_id", _GUID(), nullable=False),
        sa.Column(
            "definition_id",
            _GUID(),
            sa.ForeignKey("workflow_definitions.id"),
            nullable=False,
        ),
        sa.Column("version_id", _GUID(), sa.ForeignKey("workflow_versions.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("input", sa.JSON(), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(19, 4)),
        sa.Column("error", sa.Text()),
        sa.Column("idempotency_key", sa.String(128)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_wf_exec_tenant_idem"),
    )
    op.create_index(
        "ix_workflow_executions_definition_id", "workflow_executions", ["definition_id"]
    )
    op.create_index(
        "ix_workflow_executions_status", "workflow_executions", ["status"]
    )
    op.create_index(
        "ix_workflow_executions_tenant_id", "workflow_executions", ["tenant_id"]
    )

    op.create_table(
        "execution_events",
        sa.Column("id", _GUID(), primary_key=True),
        sa.Column("tenant_id", _GUID(), nullable=False),
        sa.Column(
            "execution_id",
            _GUID(),
            sa.ForeignKey("workflow_executions.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("execution_id", "seq", name="uq_exec_event_seq"),
    )
    op.create_index(
        "ix_execution_events_execution_id", "execution_events", ["execution_id"]
    )
    op.create_index(
        "ix_execution_events_tenant_id", "execution_events", ["tenant_id"]
    )


def downgrade() -> None:
    op.drop_table("execution_events")
    op.drop_table("workflow_executions")
    op.drop_table("workflow_versions")
    op.drop_table("workflow_definitions")
    op.drop_table("task_bulk_op_records")
    op.drop_table("task_comments")
    op.drop_table("task_transitions")
    op.drop_table("task_dependencies")
    op.drop_table("tasks")
    op.drop_table("milestones")
    op.drop_table("projects")
    op.drop_table("crm_relationships")
    op.drop_table("custom_field_definitions")
    op.drop_table("activities")
    op.drop_table("opportunities")
    op.drop_table("pipeline_stages")
    op.drop_table("pipelines")
    op.drop_table("leads")
    op.drop_table("contacts")
    op.drop_table("organizations")
