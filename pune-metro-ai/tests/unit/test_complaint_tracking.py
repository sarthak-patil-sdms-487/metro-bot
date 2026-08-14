import os
import re
from types import SimpleNamespace

os.environ.setdefault("WHATSAPP_VERIFY_TOKEN", "test")
os.environ.setdefault("WHATSAPP_ACCESS_TOKEN", "test")
os.environ.setdefault("WHATSAPP_PHONE_NUMBER_ID", "test")
os.environ.setdefault("PRIMARY_LLM_API_KEY", "test")
os.environ.setdefault("FALLBACK_LLM_API_KEY", "test")
os.environ.setdefault("DATABASE_URL", "sqlite://")

from app.services import complaint_tracking


class TrackingSession:
    """Minimal session double for collision-safe token creation."""

    def __init__(self, existing_tokens: set[str]) -> None:
        self.existing_tokens = existing_tokens
        self.added: list[object] = []

    def scalar(self, query: object) -> object | None:
        candidate = next(iter(query.compile().params.values()))
        return object() if candidate in self.existing_tokens else None

    def add(self, instance: object) -> None:
        self.added.append(instance)

    def flush(self) -> None:
        pass


def test_complaint_token_is_unique_pending_and_linked_to_the_logged_complaint(
    monkeypatch,
) -> None:
    generated = iter(["PMC-000001", "PMC-482913"])
    monkeypatch.setattr(complaint_tracking, "generate_complaint_token", lambda: next(generated))
    db = TrackingSession(existing_tokens={"PMC-000001"})

    tracking = complaint_tracking.create_complaint_tracking(
        category_log=SimpleNamespace(id=51),
        user_id=7,
        conversation_id=9,
        db=db,
    )

    assert tracking.token == "PMC-482913"
    assert tracking.category == "complaint"
    assert tracking.status == "pending"
    assert tracking.category_log_id == 51
    assert tracking.user_id == 7
    assert tracking.conversation_id == 9
    assert db.added == [tracking]


def test_generated_complaint_token_has_required_format() -> None:
    assert re.fullmatch(r"PMC-\d{6}", complaint_tracking.generate_complaint_token())


def test_suggestion_tracking_stores_suggestion_category(monkeypatch) -> None:
    monkeypatch.setattr(
        complaint_tracking, "generate_complaint_token", lambda: "PMC-123456"
    )
    db = TrackingSession(existing_tokens=set())

    tracking = complaint_tracking.create_complaint_tracking(
        category_log=SimpleNamespace(id=52),
        user_id=7,
        conversation_id=9,
        db=db,
        category="suggestion",
    )

    assert tracking.token == "PMC-123456"
    assert tracking.category == "suggestion"


def test_suggestion_confirmation_is_feedback_acknowledgment() -> None:
    reply = complaint_tracking.suggestion_confirmation_reply(
        "PMC-123456", "Please add more bicycle parking", language="english"
    )

    assert reply == (
        "Thanks for the suggestion! We've noted it with reference ID PMC-123456. "
        "Our team reviews suggestions periodically as we plan improvements."
    )
    assert "complaint" not in reply.casefold()
    assert "status" not in reply.casefold()
    assert "update you" not in reply.casefold()