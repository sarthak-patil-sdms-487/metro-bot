"""Provider-neutral contracts shared by text and voice adapters."""

from dataclasses import dataclass, field
from typing import Any, Literal


Channel = Literal["chat", "call"]


@dataclass(frozen=True)
class BrainMessage:
    role: Literal["user", "assistant", "tool"]
    content: str
    tool_name: str | None = None


@dataclass(frozen=True)
class BrainRequest:
    user_id: int
    user_identity: str
    channel: Channel
    text: str
    conversation_id: int
    history: list[BrainMessage] = field(default_factory=list)
    session_id: int | None = None
    preferred_language: str | None = None


@dataclass(frozen=True)
class BrainAction:
    tool: str
    status: Literal["completed", "failed"]
    arguments: dict[str, Any]
    result: dict[str, Any]


@dataclass(frozen=True)
class BrainResponse:
    reply_text: str
    actions: list[BrainAction]
    language: str
    categories: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BrainEvent:
    type: Literal[
        "text_delta", "tool_started", "tool_completed", "tool_failed", "response_completed"
    ]
    data: dict[str, Any]
