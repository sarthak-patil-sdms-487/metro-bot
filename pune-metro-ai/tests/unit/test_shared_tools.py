"""Channel-neutral deterministic tool tests."""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.models import Base, Conversation, User
from app.services.tools import ToolContext, execute_tool


def _context() -> ToolContext:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    user = User(whatsapp_number="919999999999")
    db.add(user)
    db.flush()
    conversation = Conversation(user_id=user.id, channel="call")
    db.add(conversation)
    db.commit()
    return ToolContext(
        user_id=user.id,
        conversation_id=conversation.id,
        channel="call",
        db=db,
    )


def test_get_fare_uses_exact_matrix() -> None:
    result = execute_tool(
        "get_fare", {"origin": "PCMC", "destination": "Swargate"}, _context()
    )
    assert result["found"] is True
    assert result["cash_fare_inr"] == 30


def test_station_alias_is_canonicalized() -> None:
    result = execute_tool(
        "get_station_info",
        {"station": "civil court", "information_needed": "route"},
        _context(),
    )
    assert result["found"] is True
    assert result["canonical_name"] == "District Court"


def test_complaint_tool_is_idempotent() -> None:
    context = _context()
    arguments = {
        "full_name": "Test Passenger",
        "contact_number": "919999999999",
        "station": "PCMC",
        "description": "The lift has not been working since this morning.",
    }
    first = execute_tool("log_complaint", arguments, context)
    second = execute_tool("log_complaint", arguments, context)
    assert first["created"] is True
    assert first["tracking_id"].startswith("PMC-")
    assert second == {
        "created": False,
        "idempotent": True,
        "tracking_id": first["tracking_id"],
    }


def test_tracking_tool_returns_created_ticket() -> None:
    context = _context()
    created = execute_tool(
        "log_suggestion",
        {
            "full_name": "Test Passenger",
            "description": "Please add clearer platform signs near the entrance.",
            "station": "Swargate",
        },
        context,
    )
    result = execute_tool("check_tracking", {"tracking_id": created["tracking_id"]}, context)
    assert result["found"] is True
    assert result["category"] == "suggestion"
    assert result["status"] == "pending"
