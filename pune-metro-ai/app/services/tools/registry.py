"""Provider-neutral tool schemas and trusted execution context."""

from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.services.brain_models import Channel
from app.services.tools.complaints import log_complaint, log_suggestion
from app.services.tools.fares import get_fare
from app.services.tools.stations import get_station_info
from app.services.tools.tracking import check_tracking


@dataclass(frozen=True)
class ToolContext:
    user_id: int
    conversation_id: int
    channel: Channel
    db: Session
    session_id: int | None = None


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "log_complaint",
            "description": "Register a Pune Metro complaint only when every required detail is known.",
            "parameters": {
                "type": "object",
                "properties": {
                    "full_name": {"type": "string"},
                    "contact_number": {"type": "string"},
                    "station": {"type": "string"},
                    "description": {"type": "string"},
                    "subcategory": {"type": ["string", "null"]},
                },
                "required": ["full_name", "contact_number", "station", "description"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "log_suggestion",
            "description": "Register a Pune Metro suggestion when the name and useful description are known.",
            "parameters": {
                "type": "object",
                "properties": {
                    "full_name": {"type": "string"},
                    "description": {"type": "string"},
                    "station": {"type": ["string", "null"]},
                    "subcategory": {"type": ["string", "null"]},
                },
                "required": ["full_name", "description"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_tracking",
            "description": "Look up a complaint or suggestion by its PMC tracking ID.",
            "parameters": {
                "type": "object",
                "properties": {"tracking_id": {"type": "string", "pattern": "^PMC-[0-9]{6}$"}},
                "required": ["tracking_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_fare",
            "description": "Return the exact repository fare between two operational stations.",
            "parameters": {
                "type": "object",
                "properties": {"origin": {"type": "string"}, "destination": {"type": "string"}},
                "required": ["origin", "destination"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_station_info",
            "description": "Resolve a Pune Metro station and return verified operational information.",
            "parameters": {
                "type": "object",
                "properties": {
                    "station": {"type": "string"},
                    "information_needed": {
                        "type": "string",
                        "enum": ["canonical_name", "route", "facilities", "status", "general"],
                    },
                },
                "required": ["station", "information_needed"],
                "additionalProperties": False,
            },
        },
    },
]


def execute_tool(name: str, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    """Execute an allow-listed tool; identity and DB context never come from the model."""
    readers: dict[str, Callable[..., dict[str, Any]]] = {
        "get_fare": get_fare,
        "get_station_info": get_station_info,
    }
    if name in readers:
        return readers[name](**arguments)
    if name == "check_tracking":
        return check_tracking(db=context.db, **arguments)
    if name == "log_complaint":
        return log_complaint(
            user_id=context.user_id,
            conversation_id=context.conversation_id,
            db=context.db,
            **arguments,
        )
    if name == "log_suggestion":
        return log_suggestion(
            user_id=context.user_id,
            conversation_id=context.conversation_id,
            db=context.db,
            **arguments,
        )
    raise ValueError(f"Unknown tool: {name}")
