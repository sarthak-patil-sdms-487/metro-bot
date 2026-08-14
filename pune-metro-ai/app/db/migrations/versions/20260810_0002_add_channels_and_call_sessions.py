"""Add conversation channels and WhatsApp call sessions.

Revision ID: 20260810_0002
Revises: 20260802_0001
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260810_0002"
down_revision: Union[str, Sequence[str], None] = "20260802_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("channel", sa.String(length=16), server_default="chat", nullable=False),
    )
    op.create_check_constraint(
        "ck_conversations_channel",
        "conversations",
        "channel IN ('chat', 'call')",
    )
    op.create_index("ix_conversations_channel", "conversations", ["channel"], unique=False)

    op.create_table(
        "call_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider_call_id", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="ringing", nullable=False),
        sa.Column("direction", sa.String(length=16), server_default="inbound", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_reason", sa.String(length=100), nullable=True),
        sa.Column("detected_languages", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("provider_metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('ringing', 'connecting', 'active', 'completed', 'failed')",
            name="ck_call_sessions_status",
        ),
        sa.CheckConstraint(
            "direction IN ('inbound', 'outbound')",
            name="ck_call_sessions_direction",
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("conversation_id"),
        sa.UniqueConstraint("provider_call_id"),
    )
    op.create_index("ix_call_sessions_conversation_id", "call_sessions", ["conversation_id"])
    op.create_index("ix_call_sessions_provider_call_id", "call_sessions", ["provider_call_id"])
    op.create_index("ix_call_sessions_user_id", "call_sessions", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_call_sessions_user_id", table_name="call_sessions")
    op.drop_index("ix_call_sessions_provider_call_id", table_name="call_sessions")
    op.drop_index("ix_call_sessions_conversation_id", table_name="call_sessions")
    op.drop_table("call_sessions")
    op.drop_index("ix_conversations_channel", table_name="conversations")
    op.drop_constraint("ck_conversations_channel", "conversations", type_="check")
    op.drop_column("conversations", "channel")
