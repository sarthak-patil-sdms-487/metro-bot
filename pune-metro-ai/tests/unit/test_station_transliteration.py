import os

os.environ.setdefault("WHATSAPP_VERIFY_TOKEN", "test")
os.environ.setdefault("WHATSAPP_ACCESS_TOKEN", "test")
os.environ.setdefault("WHATSAPP_PHONE_NUMBER_ID", "test")
os.environ.setdefault("PRIMARY_LLM_API_KEY", "test")
os.environ.setdefault("FALLBACK_LLM_API_KEY", "test")
os.environ.setdefault("DATABASE_URL", "sqlite://")

from app.services.llm_client import (
    MATRIX_STATIONS,
    STATION_DEVANAGARI_NAMES,
    _build_reply_system_instruction,
    _generate_reply_gemini,
    _generate_reply_openrouter,
    calculate_fare_estimate,
    find_station_names,
    resolve_station_alias,
)
import pytest


def test_marathi_timetable_instruction_contains_phonetic_station_glossary() -> None:
    instruction = _build_reply_system_instruction(
        reference_data="Pune Metro timetable reference",
        fare_context=None,
        complaint_status_context=None,
        detected_language="marathi",
        reference_topics=["timetable"],
    )

    assert "When writing station names in this language" in instruction
    assert "Swargate -> स्वारगेट" in instruction
    assert "स्वर्गद्वार" not in instruction
    assert "Deccan Gymkhana -> डेक्कन जिमखाना" in instruction
    assert "Bund Garden -> बंड गार्डन" in instruction
    assert "Range Hill -> रेंज हिल" in instruction


def test_every_fare_matrix_station_has_a_canonical_devanagari_transliteration() -> None:
    assert set(MATRIX_STATIONS) <= set(STATION_DEVANAGARI_NAMES)
    assert STATION_DEVANAGARI_NAMES["Swargate"] == "स्वारगेट"


@pytest.mark.asyncio
async def test_provider_helpers_call_builder_with_matching_keyword_argument_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_instructions: list[str] = []

    async def openrouter_chat(messages: list[dict]) -> str:
        captured_instructions.append(messages[0]["content"])
        return "पुणे मेट्रोचे वेळापत्रक उपलब्ध आहे."

    async def gemini_generate(system_instruction: str, _contents: list[dict]) -> str:
        captured_instructions.append(system_instruction)
        return "पुणे मेट्रोचे वेळापत्रक उपलब्ध आहे."

    monkeypatch.setattr("app.services.llm_client._openrouter_chat", openrouter_chat)
    monkeypatch.setattr("app.services.llm_client._gemini_generate", gemini_generate)
    args = {
        "user_message": "पुणे मेट्रो वेळापत्रक",
        "conversation_history": [],
        "reference_data": "Pune Metro timetable",
        "fare_context": None,
        "complaint_status_context": None,
        "detected_language": "marathi",
        "reference_topics": ["timetable"],
    }

    openrouter_reply = await _generate_reply_openrouter(**args)
    gemini_reply = await _generate_reply_gemini(**args)

    assert openrouter_reply != "Sorry, I'm having trouble answering right now. Please try again."
    assert gemini_reply != "Sorry, I'm having trouble answering right now. Please try again."
    assert len(captured_instructions) == 2
    assert all("Swargate -> स्वारगेट" in instruction for instruction in captured_instructions)


def test_nal_stop_alias_resolves_to_sndt_college() -> None:
    """Verify that 'Nal Stop' resolves to 'S.N.D.T. College' for fare calculation."""
    fare_estimate = calculate_fare_estimate("Nal Stop", "Garware College")
    assert fare_estimate is not None
    assert "S.N.D.T. College" in fare_estimate
    assert "Garware College" in fare_estimate


@pytest.mark.parametrize(
    "spoken",
    ("Bun Garden", "Bun Garden, Bun Garden", "बन गार्डन", "बन Garden"),
)
def test_sarvam_bund_garden_variants_are_recognized(spoken: str) -> None:
    assert resolve_station_alias(spoken) == "Bund Garden"
    assert find_station_names(spoken) == ["Bund Garden"]


def test_latin_station_with_marathi_suffix_is_recognized() -> None:
    assert find_station_names("Vanajला जायचं आहे") == ["Vanaz"]
