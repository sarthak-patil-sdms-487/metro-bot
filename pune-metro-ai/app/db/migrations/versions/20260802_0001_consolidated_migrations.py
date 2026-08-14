"""Consolidated migrations.

Revision ID: 20260802_0001
Revises: 
Create Date: 2026-08-02 00:00:00

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '20260802_0001'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "admin_users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=100), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_admin_users_username"), "admin_users", ["username"], unique=True)

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("whatsapp_number", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_whatsapp_number"), "users", ["whatsapp_number"], unique=True)

    op.create_table(
        "conversations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("pending_category", sa.String(length=32), nullable=True),
        sa.Column("preferred_language", sa.String(length=16), nullable=True),
        sa.Column("confusion_handoff_shown", sa.Boolean(), nullable=False),
        sa.Column("unclear_streak_count", sa.Integer(), nullable=False),
        sa.Column("complaint_collection_state", sa.String(length=32), nullable=True),
        sa.Column("complaint_collection_full_name", sa.String(length=255), nullable=True),
        sa.Column("complaint_collection_contact_number", sa.String(length=32), nullable=True),
        sa.Column("complaint_collection_station", sa.String(length=255), nullable=True),
        sa.Column("complaint_collection_description", sa.Text(), nullable=True),
        sa.Column("is_closed", sa.Boolean(), nullable=False),
        sa.Column("feedback_rating", sa.Integer(), nullable=True),
        sa.Column("feedback_comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_conversations_user_id"), "conversations", ["user_id"], unique=False)

    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("whatsapp_message_id", sa.String(length=255), nullable=True),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_messages_conversation_id"), "messages", ["conversation_id"], unique=False)
    op.create_unique_constraint("uq_messages_whatsapp_message_id", "messages", ["whatsapp_message_id"])

    op.create_table(
        "category_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("categories", sa.ARRAY(sa.String()), nullable=False),
        sa.Column("subcategory", sa.String(length=500), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_category_logs_conversation_id"), "category_logs", ["conversation_id"], unique=False)
    op.create_index(op.f("ix_category_logs_user_id"), "category_logs", ["user_id"], unique=False)

    op.create_table(
        "ticket_details",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("category_log_id", sa.Integer(), nullable=False),
        sa.Column("metro_station", sa.String(length=255), nullable=True),
        sa.Column("ticket_number", sa.String(length=255), nullable=True),
        sa.Column("payment_method", sa.String(length=100), nullable=True),
        sa.Column("passenger_name", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["category_log_id"], ["category_logs.id"], ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("category_log_id"),
    )

    op.create_table(
        "complaint_tracking",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("category_log_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("token", sa.String(length=10), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("category IN ('complaint', 'suggestion')", name="ck_complaint_tracking_category"),
        sa.CheckConstraint("status IN ('pending', 'approved', 'resolved', 'rejected')", name="ck_complaint_tracking_status"),
        sa.ForeignKeyConstraint(["category_log_id"], ["category_logs.id"], ),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("category_log_id", "category", name="uq_complaint_tracking_category_log_category"),
    )
    op.create_index(op.f("ix_complaint_tracking_category_log_id"), "complaint_tracking", ["category_log_id"], unique=False)
    op.create_index(op.f("ix_complaint_tracking_conversation_id"), "complaint_tracking", ["conversation_id"], unique=False)
    op.create_index(op.f("ix_complaint_tracking_token"), "complaint_tracking", ["token"], unique=True)
    op.create_index(op.f("ix_complaint_tracking_user_id"), "complaint_tracking", ["user_id"], unique=False)

    op.create_table(
        "qa_cache",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("normalized_question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("hit_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_qa_cache_normalized_question"), "qa_cache", ["normalized_question"], unique=True)

    op.create_table(
        "response_source_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("response_source_log")
    op.drop_index(op.f("ix_qa_cache_normalized_question"), table_name="qa_cache")
    op.drop_table("qa_cache")
    op.drop_index(op.f("ix_complaint_tracking_user_id"), table_name="complaint_tracking")
    op.drop_index(op.f("ix_complaint_tracking_token"), table_name="complaint_tracking")
    op.drop_index(op.f("ix_complaint_tracking_conversation_id"), table_name="complaint_tracking")
    op.drop_index(op.f("ix_complaint_tracking_category_log_id"), table_name="complaint_tracking")
    op.drop_table("complaint_tracking")
    op.drop_table("ticket_details")
    op.drop_index(op.f("ix_category_logs_user_id"), table_name="category_logs")
    op.drop_index(op.f("ix_category_logs_conversation_id"), table_name="category_logs")
    op.drop_table("category_logs")
    op.drop_index(op.f("ix_messages_conversation_id"), table_name="messages")
    op.drop_table("messages")
    op.drop_index(op.f("ix_conversations_user_id"), table_name="conversations")
    op.drop_table("conversations")
    op.drop_index(op.f("ix_users_whatsapp_number"), table_name="users")
    op.drop_table("users")
    op.drop_index(op.f("ix_admin_users_username"), table_name="admin_users")
    op.drop_table("admin_users")