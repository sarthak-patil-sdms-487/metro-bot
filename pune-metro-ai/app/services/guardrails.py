"""Guardrails for LLM input and output."""

import re

from app.core.config import settings
from app.services.llm_client import FARES_MATRIX_DATA

OFF_TOPIC_KEYWORDS = [
    "capital of", "president of", "weather", "sports scores", "joke",
    "bake a cake", "recipe", "stock market", "what is the meaning of life",
    "who are you", "who made you",
]

def apply_guardrails(user_message: str, draft_reply: str) -> str | None:
    """Apply guardrails to the user message and the draft reply."""
    # Input guardrails
    lower_message = user_message.lower()
    if "ignore your instructions" in lower_message or "reveal your prompt" in lower_message:
        return "I can only answer questions about Pune Metro."

    if any(keyword in lower_message for keyword in OFF_TOPIC_KEYWORDS):
        return "I can only help with Pune Metro related questions. How can I help you with Pune Metro?"

    # Output guardrails
    if len(draft_reply) > settings.MAX_REPLY_LENGTH:
        return f"{draft_reply[:settings.MAX_REPLY_LENGTH]}... (Reply 'more' for details)"
    
    # Fare and timetable guardrails
    fare_match = re.search(r"₹(\d+)", draft_reply)
    if fare_match:
        fare = int(fare_match.group(1))
        # This is a simplified check. A more robust implementation would
        # check the fare against the specific station pair.
        if fare not in [item for sublist in FARES_MATRIX_DATA.get("fare_matrix", {}).values() for item in sublist.values()]:
            return "I'm sorry, I can't provide fare information for that route."

    # Check for sensitive information
    if re.search(r"stack trace|file path|database", draft_reply, re.IGNORECASE):
        return "I'm sorry, I can't provide that information."

    return None