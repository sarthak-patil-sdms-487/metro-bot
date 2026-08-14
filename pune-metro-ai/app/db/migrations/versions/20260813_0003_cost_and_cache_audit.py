"""Add persistent cost, source, and TTS cache audit data."""

from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "20260813_0003"
down_revision: Union[str, Sequence[str], None] = "20260810_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_qa_cache_normalized_question", table_name="qa_cache")
    op.create_unique_constraint("uq_qa_cache_question_language", "qa_cache", ["normalized_question", "language"])
    op.create_index("ix_qa_cache_normalized_question", "qa_cache", ["normalized_question"], unique=False)
    op.create_table(
        "tts_audio_cache",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cache_key", sa.String(64), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("language", sa.String(16), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("voice", sa.String(100), nullable=False),
        sa.Column("audio", sa.LargeBinary(), nullable=False),
        sa.Column("sample_rate", sa.Integer(), nullable=False),
        sa.Column("num_channels", sa.Integer(), server_default="1", nullable=False),
        sa.Column("hit_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("cache_key"),
    )
    op.create_index("ix_tts_audio_cache_cache_key", "tts_audio_cache", ["cache_key"], unique=True)
    columns = [
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("conversations.id"), nullable=True),
        sa.Column("call_session_id", sa.Integer(), sa.ForeignKey("call_sessions.id"), nullable=True),
        sa.Column("channel", sa.String(16), server_default="chat", nullable=False),
        sa.Column("operation", sa.String(16), server_default="llm", nullable=False),
        sa.Column("question", sa.Text(), nullable=True),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("cache_entry_id", sa.Integer(), sa.ForeignKey("qa_cache.id"), nullable=True),
        sa.Column("provider", sa.String(64), nullable=True),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("input_units", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_units", sa.Integer(), server_default="0", nullable=False),
        sa.Column("actual_cost_inr", sa.Float(), server_default="0", nullable=False),
        sa.Column("uncached_cost_inr", sa.Float(), server_default="0", nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
    ]
    for column in columns:
        op.add_column("response_source_log", column)
    op.create_index("ix_response_source_log_conversation_id", "response_source_log", ["conversation_id"])
    op.create_index("ix_response_source_log_call_session_id", "response_source_log", ["call_session_id"])


def downgrade() -> None:
    op.drop_index("ix_response_source_log_call_session_id", table_name="response_source_log")
    op.drop_index("ix_response_source_log_conversation_id", table_name="response_source_log")
    for name in ("metadata_json", "uncached_cost_inr", "actual_cost_inr", "output_units", "input_units", "model", "provider", "cache_entry_id", "answer", "question", "operation", "channel", "call_session_id", "conversation_id"):
        op.drop_column("response_source_log", name)
    op.drop_table("tts_audio_cache")
    op.drop_index("ix_qa_cache_normalized_question", table_name="qa_cache")
    op.drop_constraint("uq_qa_cache_question_language", "qa_cache", type_="unique")
    op.create_index("ix_qa_cache_normalized_question", "qa_cache", ["normalized_question"], unique=True)
