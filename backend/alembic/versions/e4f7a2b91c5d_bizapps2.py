"""bizapps2: business-application tables (comms/marketing/support/scheduling/finance).

Revision ID: e4f7a2b91c5d
Revises: 0003_billing

28 tenant-scoped tables owned by the five business-app packages. Every table
gets RLS via helpers.apply_rls (DATABASE.md §1, ADR-008). finance_transactions
is append-only (the service exposes no update/delete path).

Table definitions were generated from the packages' SQLAlchemy models on
2026-09-15 (pre-prod: no prod data exists yet, so a frozen re-generation is
safe). If models drift, regenerate rather than hand-editing.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.models import GUID
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

from helpers import apply_rls, drop_rls

revision: str = "e4f7a2b91c5d"
down_revision: str | Sequence[str] | None = "0003_billing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = [
    "channels",
    "comms_usage",
    "conversations",
    "message_templates",
    "messages",
    "audience_members",
    "audiences",
    "campaign_sends",
    "campaign_steps",
    "campaigns",
    "content_assets",
    "escalation_rules",
    "kb_articles",
    "macros",
    "sla_policies",
    "ticket_messages",
    "tickets",
    "availability_rules",
    "bookings",
    "calendar_events",
    "calendars",
    "collection_reminders",
    "finance_accounts",
    "finance_customers",
    "finance_expenses",
    "finance_transactions",
    "invoices",
    "payments",
]


def upgrade() -> None:
    op.create_table(
        "channels",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("tenant_id", GUID(), nullable=False, index=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("provider", sa.String(100), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    apply_rls("channels")

    op.create_table(
        "comms_usage",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("tenant_id", GUID(), nullable=False, index=True),
        sa.Column("period", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("units", sa.BigInteger(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(19, 4), nullable=False),
    )
    apply_rls("comms_usage")

    op.create_table(
        "conversations",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("tenant_id", GUID(), nullable=False, index=True),
        sa.Column("channel_id", GUID(), nullable=True),
        sa.Column("subject_type", sa.String(50), nullable=True),
        sa.Column("subject_id", GUID(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
    )
    apply_rls("conversations")

    op.create_table(
        "message_templates",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("tenant_id", GUID(), nullable=False, index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("subject", sa.String(300), nullable=True),
        sa.Column("body", sa.String(None), nullable=False),
        sa.Column("variables", sa.JSON(), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    apply_rls("message_templates")

    op.create_table(
        "messages",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("tenant_id", GUID(), nullable=False, index=True),
        sa.Column("conversation_id", GUID(), nullable=True, index=True),
        sa.Column("channel_id", GUID(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("to_address", sa.String(320), nullable=False),
        sa.Column("from_address", sa.String(320), nullable=True),
        sa.Column("subject", sa.String(300), nullable=True),
        sa.Column("body", sa.String(None), nullable=False),
        sa.Column("template_id", GUID(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(100), nullable=False),
        sa.Column("provider_message_id", sa.String(200), nullable=True),
        sa.Column("cost_usd", sa.Numeric(19, 4), nullable=False),
        sa.Column("error", sa.String(None), nullable=True),
        sa.Column("idempotency_key", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    apply_rls("messages")

    op.create_table(
        "audience_members",
        sa.Column("audience_id", GUID(), primary_key=True, nullable=False),
        sa.Column("contact_id", GUID(), primary_key=True, nullable=False),
        sa.Column("tenant_id", GUID(), nullable=False, index=True),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
    )
    apply_rls("audience_members")

    op.create_table(
        "audiences",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("tenant_id", GUID(), nullable=False, index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.String(None), nullable=True),
        sa.Column("filter", sa.JSON(), nullable=False),
        sa.Column("member_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    apply_rls("audiences")

    op.create_table(
        "campaign_sends",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("tenant_id", GUID(), nullable=False, index=True),
        sa.Column("campaign_id", GUID(), nullable=False, index=True),
        sa.Column("step_id", GUID(), nullable=False),
        sa.Column("contact_ref", sa.String(320), nullable=False),
        sa.Column("message_id", GUID(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error", sa.String(None), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    apply_rls("campaign_sends")

    op.create_table(
        "campaign_steps",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("tenant_id", GUID(), nullable=False, index=True),
        sa.Column("campaign_id", GUID(), nullable=False, index=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column("template_id", GUID(), nullable=True),
        sa.Column("asset_id", GUID(), nullable=True),
        sa.Column("delay_minutes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    apply_rls("campaign_steps")

    op.create_table(
        "campaigns",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("tenant_id", GUID(), nullable=False, index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("audience_id", GUID(), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approval_id", GUID(), nullable=True),
        sa.Column("launched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", GUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    apply_rls("campaigns")

    op.create_table(
        "content_assets",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("tenant_id", GUID(), nullable=False, index=True),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("body", sa.String(None), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("brand_check", sa.JSON(), nullable=True),
        sa.Column("created_by", GUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    apply_rls("content_assets")

    op.create_table(
        "escalation_rules",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("tenant_id", GUID(), nullable=False, index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("priorities", sa.JSON(), nullable=False),
        sa.Column("breach_type", sa.String(32), nullable=False),
        sa.Column("min_age_minutes", sa.Integer(), nullable=False),
        sa.Column("action_assign_user_id", GUID(), nullable=True),
        sa.Column("action_assign_queue", sa.String(100), nullable=True),
        sa.Column("action_set_priority", sa.String(16), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    apply_rls("escalation_rules")

    op.create_table(
        "kb_articles",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("tenant_id", GUID(), nullable=False, index=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("body", sa.String(None), nullable=False),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("is_faq", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("view_count", sa.Integer(), nullable=False),
        sa.Column("created_by", GUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    apply_rls("kb_articles")

    op.create_table(
        "macros",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("tenant_id", GUID(), nullable=False, index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("body", sa.String(None), nullable=False),
        sa.Column("shared", sa.Boolean(), nullable=False),
        sa.Column("created_by", GUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    apply_rls("macros")

    op.create_table(
        "sla_policies",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("tenant_id", GUID(), nullable=False, index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False),
        sa.Column("first_response_minutes", sa.Integer(), nullable=False),
        sa.Column("resolution_hours", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
    )
    apply_rls("sla_policies")

    op.create_table(
        "ticket_messages",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("tenant_id", GUID(), nullable=False, index=True),
        sa.Column("ticket_id", GUID(), nullable=False, index=True),
        sa.Column("author_type", sa.String(16), nullable=False),
        sa.Column("author_id", GUID(), nullable=True),
        sa.Column("channel", sa.String(32), nullable=True),
        sa.Column("body", sa.String(None), nullable=False),
        sa.Column("is_internal_note", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    apply_rls("ticket_messages")

    op.create_table(
        "tickets",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("tenant_id", GUID(), nullable=False, index=True),
        sa.Column("subject", sa.String(300), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False),
        sa.Column("requester_contact_id", GUID(), nullable=True),
        sa.Column("assignee_user_id", GUID(), nullable=True),
        sa.Column("assignee_agent_id", GUID(), nullable=True),
        sa.Column("queue", sa.String(100), nullable=True),
        sa.Column("channel", sa.String(32), nullable=True),
        sa.Column("conversation_id", GUID(), nullable=True),
        sa.Column("sla_policy_id", GUID(), nullable=True),
        sa.Column("first_response_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_response_breached", sa.Boolean(), nullable=False),
        sa.Column("resolution_breached", sa.Boolean(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution", sa.String(None), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False),
    )
    apply_rls("tickets")

    op.create_table(
        "availability_rules",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("tenant_id", GUID(), nullable=False, index=True),
        sa.Column("calendar_id", GUID(), nullable=False, index=True),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    apply_rls("availability_rules")

    op.create_table(
        "bookings",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("tenant_id", GUID(), nullable=False, index=True),
        sa.Column("calendar_id", GUID(), nullable=False, index=True),
        sa.Column("event_id", GUID(), nullable=False),
        sa.Column("contact_id", GUID(), nullable=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("reminders", sa.JSON(), nullable=False),
        sa.Column("reminders_sent", sa.JSON(), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_reason", sa.String(None), nullable=True),
        sa.Column("created_by", GUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    apply_rls("bookings")

    op.create_table(
        "calendar_events",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("tenant_id", GUID(), nullable=False, index=True),
        sa.Column("calendar_id", GUID(), nullable=False, index=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("location", sa.String(300), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    apply_rls("calendar_events")

    op.create_table(
        "calendars",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("tenant_id", GUID(), nullable=False, index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("owner_id", GUID(), nullable=True),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    apply_rls("calendars")

    op.create_table(
        "collection_reminders",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("tenant_id", GUID(), nullable=False, index=True),
        sa.Column("invoice_id", GUID(), nullable=False, index=True),
        sa.Column("body", sa.String(None), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("approval_id", GUID(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", GUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    apply_rls("collection_reminders")

    op.create_table(
        "finance_accounts",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("tenant_id", GUID(), nullable=False, index=True),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "code", name="uq_account_code"),
    )
    apply_rls("finance_accounts")

    op.create_table(
        "finance_customers",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("tenant_id", GUID(), nullable=False, index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("org_id", GUID(), nullable=True),
        sa.Column("nexora_ref", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    apply_rls("finance_customers")

    op.create_table(
        "finance_expenses",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("tenant_id", GUID(), nullable=False, index=True),
        sa.Column("category", sa.String(100), nullable=False, index=True),
        sa.Column("amount", sa.Numeric(19, 4), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("vendor", sa.String(200), nullable=True),
        sa.Column("description", sa.String(None), nullable=True),
        sa.Column("incurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("receipt_ref", sa.String(200), nullable=True),
        sa.Column("nexora_ref", sa.String(128), nullable=True),
        sa.Column("created_by", GUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    apply_rls("finance_expenses")

    op.create_table(
        "finance_transactions",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("tenant_id", GUID(), nullable=False, index=True),
        sa.Column("account_id", GUID(), nullable=False, index=True),
        sa.Column("amount", sa.Numeric(19, 4), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("memo", sa.String(None), nullable=True),
        sa.Column("ref_type", sa.String(50), nullable=True),
        sa.Column("ref_id", GUID(), nullable=True),
        sa.Column("reverses_id", GUID(), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", GUID(), nullable=True),
    )
    apply_rls("finance_transactions", append_only=True)

    op.create_table(
        "invoices",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("tenant_id", GUID(), nullable=False, index=True),
        sa.Column("number", sa.String(40), nullable=False),
        sa.Column("customer_id", GUID(), nullable=False, index=True),
        sa.Column("lines", sa.JSON(), nullable=False),
        sa.Column("amount", sa.Numeric(19, 4), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, index=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("balance_due", sa.Numeric(19, 4), nullable=False),
        sa.Column("nexora_ref", sa.String(128), nullable=True),
        sa.Column("void_reason", sa.String(None), nullable=True),
        sa.Column("created_by", GUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "number", name="uq_invoice_number"),
    )
    apply_rls("invoices")

    op.create_table(
        "payments",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("tenant_id", GUID(), nullable=False, index=True),
        sa.Column("invoice_id", GUID(), nullable=False, index=True),
        sa.Column("amount", sa.Numeric(19, 4), nullable=False),
        sa.Column("method", sa.String(50), nullable=False),
        sa.Column("reference", sa.String(200), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("nexora_ref", sa.String(128), nullable=True),
        sa.Column("created_by", GUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    apply_rls("payments")


def downgrade() -> None:
    drop_rls("payments")
    op.drop_table("payments")
    drop_rls("invoices")
    op.drop_table("invoices")
    drop_rls("finance_transactions")
    op.drop_table("finance_transactions")
    drop_rls("finance_expenses")
    op.drop_table("finance_expenses")
    drop_rls("finance_customers")
    op.drop_table("finance_customers")
    drop_rls("finance_accounts")
    op.drop_table("finance_accounts")
    drop_rls("collection_reminders")
    op.drop_table("collection_reminders")
    drop_rls("calendars")
    op.drop_table("calendars")
    drop_rls("calendar_events")
    op.drop_table("calendar_events")
    drop_rls("bookings")
    op.drop_table("bookings")
    drop_rls("availability_rules")
    op.drop_table("availability_rules")
    drop_rls("tickets")
    op.drop_table("tickets")
    drop_rls("ticket_messages")
    op.drop_table("ticket_messages")
    drop_rls("sla_policies")
    op.drop_table("sla_policies")
    drop_rls("macros")
    op.drop_table("macros")
    drop_rls("kb_articles")
    op.drop_table("kb_articles")
    drop_rls("escalation_rules")
    op.drop_table("escalation_rules")
    drop_rls("content_assets")
    op.drop_table("content_assets")
    drop_rls("campaigns")
    op.drop_table("campaigns")
    drop_rls("campaign_steps")
    op.drop_table("campaign_steps")
    drop_rls("campaign_sends")
    op.drop_table("campaign_sends")
    drop_rls("audiences")
    op.drop_table("audiences")
    drop_rls("audience_members")
    op.drop_table("audience_members")
    drop_rls("messages")
    op.drop_table("messages")
    drop_rls("message_templates")
    op.drop_table("message_templates")
    drop_rls("conversations")
    op.drop_table("conversations")
    drop_rls("comms_usage")
    op.drop_table("comms_usage")
    drop_rls("channels")
    op.drop_table("channels")
