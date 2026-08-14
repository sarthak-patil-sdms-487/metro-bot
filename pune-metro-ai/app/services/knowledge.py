"""Shared Pune Metro knowledge access for every delivery channel."""

from app.services.llm_client import load_reference_data


ALLOWED_TOPICS = frozenset(
    {"fares", "stations", "rules", "card", "card_non_kyc", "vidyarthi_pass", "contact", "timetable"}
)


def get_knowledge(topics: list[str]) -> str:
    """Return only repository-owned, allow-listed reference material."""
    return load_reference_data([topic for topic in topics if topic in ALLOWED_TOPICS])
