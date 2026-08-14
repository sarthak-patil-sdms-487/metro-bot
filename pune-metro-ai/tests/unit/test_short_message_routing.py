import os

os.environ.setdefault("WHATSAPP_VERIFY_TOKEN", "test")
os.environ.setdefault("WHATSAPP_ACCESS_TOKEN", "test")
os.environ.setdefault("WHATSAPP_PHONE_NUMBER_ID", "test")
os.environ.setdefault("PRIMARY_LLM_API_KEY", "test")
os.environ.setdefault("FALLBACK_LLM_API_KEY", "test")
os.environ.setdefault("DATABASE_URL", "sqlite://")

import pytest

from app.services.whatsapp_client import WHATSAPP_LIST_TITLE_MAX_LENGTH, whatsapp_list_title
from app.services.llm_client import (
    CLASSIFICATION_SYSTEM_MESSAGE,
    _normalize_classification,
    classify_message,
    detect_language,
    short_message_intent,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    ["Sure", "Ok", "Yes", "Thanks", "Hi", "No", "Heyy", "Heyyy", "Hii", "Helloo"],
)
async def test_short_english_messages_have_deterministic_routes(message: str) -> None:
    """Low-signal English words never invoke provider-dependent classification."""
    assert detect_language(message) == "english"

    result = await classify_message(message)

    expected_intent = (
        "greeting" if message in {"Hi", "Heyy", "Heyyy", "Hii", "Helloo"} else "acknowledgment"
    )
    assert result["intent"] == expected_intent
    assert result["categories"] == []
    assert result["clarification_question"] is None
    assert result["clarification_options"] is None


@pytest.mark.parametrize(
    ("message", "intent"),
    [("hello", "greeting"), ("नमस्ते", "greeting"), ("thank you", "acknowledgment")],
)
def test_known_short_message_intents(message: str, intent: str) -> None:
    assert short_message_intent(message) == intent


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    ["Namaste", "नमस्ते", "gm", "Good evening!", "hii there", "राम राम"],
)
async def test_common_greeting_variants_use_deterministic_greeting_route(
    monkeypatch: pytest.MonkeyPatch, message: str
) -> None:
    async def provider_must_not_run(*_args: object, **_kwargs: object) -> dict:
        raise AssertionError("short greetings must bypass LLM classification")

    monkeypatch.setattr(
        "app.services.llm_client._classify_message_openrouter",
        provider_must_not_run,
    )
    monkeypatch.setattr(
        "app.services.llm_client._classify_message_gemini",
        provider_must_not_run,
    )

    result = await classify_message(message)

    assert result["intent"] == "greeting"
    assert result["classification_confident"] is True
    assert result["categories"] == []


@pytest.mark.parametrize(
    "title",
    [
        "Exactly twenty four chars",
        "A clarification option that is much longer than WhatsApp permits",
    ],
)
def test_clarification_titles_never_exceed_whatsapp_limit(title: str) -> None:
    shortened = whatsapp_list_title(title)
    assert shortened
    assert len(shortened) <= WHATSAPP_LIST_TITLE_MAX_LENGTH


def test_legacy_category_outputs_normalize_to_enquiry() -> None:
    result = _normalize_classification(
        {
            "categories": ["query", "en", "others", "complaint"],
            "subcategories": [],
            "reference_topics": [],
            "extracted_details": {},
        }
    )
    assert result["categories"] == ["enquiry", "enquiry", "enquiry", "complaint"]


def test_out_of_scope_contract_explicitly_covers_math_and_non_metro_troubleshooting() -> None:
    assert "Standalone arithmetic, unit conversions, or general-trivia questions" in (
        CLASSIFICATION_SYSTEM_MESSAGE
    )
    assert '"What is 123 x 456?" -> out_of_scope' in CLASSIFICATION_SYSTEM_MESSAGE
    assert '"20% of 500?" -> out_of_scope' in CLASSIFICATION_SYSTEM_MESSAGE
    assert '"Wi-Fi का चालत नाही?" -> out_of_scope' in CLASSIFICATION_SYSTEM_MESSAGE
    assert '"Wi-Fi at PCMC station isn\'t working" -> direct_query' in (
        CLASSIFICATION_SYSTEM_MESSAGE
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "expected_intent", "expected_categories", "expected_subcategories"),
    [
        ("123 x 456 kiti?", "out_of_scope", [], []),
        ("१२३ x ४५६ किती?", "out_of_scope", [], []),
        ("Wi-Fi का चालत नाही?", "out_of_scope", [], []),
        (
            "PCMC station wifi is not working",
            "direct_query",
            ["complaint"],
            ["Passenger Amenities"],
        ),
    ],
)
async def test_classifier_contract_routes_general_questions_outside_scope_but_keeps_station_wifi(
    monkeypatch: pytest.MonkeyPatch,
    message: str,
    expected_intent: str,
    expected_categories: list[str],
    expected_subcategories: list[str],
) -> None:
    """Verify classifier-provider results use the new, explicit routing contract."""

    async def provider(_message: str, instruction: str) -> dict:
        assert "Standalone arithmetic" in instruction
        return {
            "intent": expected_intent,
            "detected_language": "english",
            "classification_confident": True,
            "categories": expected_categories,
            "subcategories": expected_subcategories,
            "extracted_details": {
                "metro_station": "PCMC" if expected_categories else None,
                "ticket_number": None,
                "payment_method": None,
                "passenger_name": None,
            },
            "clarification_question": None,
            "clarification_options": None,
            "reference_topics": [],
            "asking_about_complaint_status": False,
        }

    monkeypatch.setattr("app.services.llm_client._classify_message_openrouter", provider)
    result = await classify_message(message)

    assert result["intent"] == expected_intent
    assert result["classification_confident"] is True
    assert result["categories"] == expected_categories
    assert result["subcategories"] == expected_subcategories
