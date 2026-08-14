"""SQLAlchemy ORM models for the application database."""

from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, UniqueConstraint, JSON, Float, Integer, LargeBinary
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models."""


class AdminUser(Base):
    """Administrative user for the dashboard login."""

    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class User(Base):
    """A WhatsApp user of the assistant."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    whatsapp_number: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Conversation(Base):
    """A conversation belonging to a user."""

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    channel: Mapped[str] = mapped_column(String(16), default="chat", server_default="chat", index=True)
    status: Mapped[str] = mapped_column(String(32), default="active")
    pending_category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    preferred_language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    confusion_handoff_shown: Mapped[bool] = mapped_column(default=False)
    unclear_streak_count: Mapped[int] = mapped_column(default=0)
    complaint_collection_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    complaint_collection_full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    complaint_collection_contact_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    complaint_collection_station: Mapped[str | None] = mapped_column(String(255), nullable=True)
    complaint_collection_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_closed: Mapped[bool] = mapped_column(default=False)
    feedback_rating: Mapped[int | None] = mapped_column(nullable=True)
    feedback_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Message(Base):
    """A message sent within a conversation."""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id"), index=True
    )
    whatsapp_message_id: Mapped[str | None] = mapped_column(
        String(255), unique=True, nullable=True
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CategoryLog(Base):
    """LLM-derived categories recorded for an inbound user message."""

    __tablename__ = "category_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id"), index=True
    )
    categories: Mapped[list[str]] = mapped_column(
        ARRAY(String()).with_variant(JSON(), "sqlite"), nullable=False
    )
    subcategory: Mapped[str | None] = mapped_column(String(500), nullable=True)
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TicketDetails(Base):
    """Optional structured ticket information extracted from a user message."""

    __tablename__ = "ticket_details"

    id: Mapped[int] = mapped_column(primary_key=True)
    category_log_id: Mapped[int] = mapped_column(
        ForeignKey("category_logs.id"), unique=True
    )
    metro_station: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ticket_number: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payment_method: Mapped[str | None] = mapped_column(String(100), nullable=True)
    passenger_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ComplaintTracking(Base):
    """A user-facing reference token for a logged complaint or suggestion."""

    __tablename__ = "complaint_tracking"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'resolved', 'rejected')",
            name="ck_complaint_tracking_status",
        ),
        CheckConstraint(
            "category IN ('complaint', 'suggestion')",
            name="ck_complaint_tracking_category",
        ),
        UniqueConstraint(
            "category_log_id",
            "category",
            name="uq_complaint_tracking_category_log_category",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    category_log_id: Mapped[int] = mapped_column(
        ForeignKey("category_logs.id"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id"), index=True
    )
    token: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    category: Mapped[str] = mapped_column(
        String(32), default="complaint", server_default="complaint"
    )
    status: Mapped[str] = mapped_column(String(32), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class QACache(Base):
    """A cache for frequently asked questions."""

    __tablename__ = "qa_cache"

    id: Mapped[int] = mapped_column(primary_key=True)
    normalized_question: Mapped[str] = mapped_column(Text, index=True)
    answer: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(16))
    category: Mapped[str] = mapped_column(String(32))
    hit_count: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("normalized_question", "language", name="uq_qa_cache_question_language"),
    )


class TTSAudioCache(Base):
    """Provider-ready synthesized speech, keyed by text and voice settings."""

    __tablename__ = "tts_audio_cache"

    id: Mapped[int] = mapped_column(primary_key=True)
    cache_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    text: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(100))
    voice: Mapped[str] = mapped_column(String(100))
    audio: Mapped[bytes] = mapped_column(LargeBinary)
    sample_rate: Mapped[int] = mapped_column(Integer)
    num_channels: Mapped[int] = mapped_column(Integer, default=1)
    hit_count: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ResponseSourceLog(Base):
    """Log of response sources (cache vs. LLM)."""

    __tablename__ = "response_source_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32))
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("conversations.id"), index=True, nullable=True)
    call_session_id: Mapped[int | None] = mapped_column(ForeignKey("call_sessions.id"), index=True, nullable=True)
    channel: Mapped[str] = mapped_column(String(16), default="chat", server_default="chat")
    operation: Mapped[str] = mapped_column(String(16), default="llm", server_default="llm")
    question: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    cache_entry_id: Mapped[int | None] = mapped_column(ForeignKey("qa_cache.id"), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    input_units: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    output_units: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    actual_cost_inr: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    uncached_cost_inr: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CallSession(Base):
    """Lifecycle and provider metadata for a WhatsApp voice call."""

    __tablename__ = "call_sessions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ringing', 'connecting', 'active', 'completed', 'failed')",
            name="ck_call_sessions_status",
        ),
        CheckConstraint(
            "direction IN ('inbound', 'outbound')",
            name="ck_call_sessions_direction",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id"), unique=True, index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    provider_call_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="ringing", server_default="ringing")
    direction: Mapped[str] = mapped_column(String(16), default="inbound", server_default="inbound")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    detected_languages: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")
    provider_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
