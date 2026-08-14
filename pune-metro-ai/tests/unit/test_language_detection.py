import os

os.environ.setdefault("WHATSAPP_VERIFY_TOKEN", "test")
os.environ.setdefault("WHATSAPP_ACCESS_TOKEN", "test")
os.environ.setdefault("WHATSAPP_PHONE_NUMBER_ID", "test")
os.environ.setdefault("PRIMARY_LLM_API_KEY", "test")
os.environ.setdefault("FALLBACK_LLM_API_KEY", "test")
os.environ.setdefault("DATABASE_URL", "sqlite://")

import pytest

from types import SimpleNamespace

from app.api import whatsapp_webhook as webhook
from app.db.models import Message
from app.services import llm_client
from app.services import complaint_tracking
from app.services.llm_client import (
    classify_message,
    detect_language,
    detect_script,
    detect_language_switch_request,
    generate_language_switch_confirmation,
    generate_reply,
    generate_out_of_scope_reply,
    resolve_reply_language,
)


def test_romanized_marathi_switch_request_and_language_detection() -> None:
    message = "Mahiti mala marathi madhe bhetu shakel"

    assert detect_language_switch_request(message) == "marathi"
    assert detect_language(message) == "marathi"
    assert "मराठीत" in generate_language_switch_confirmation("marathi")


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("मेट्रो का किराया कितना है?", "hindi"),
        ("मेट्रोचे भाडे किती आहे?", "marathi"),
        ("hello there", "english"),
    ],
)
def test_existing_devanagari_and_english_detection_remains_stable(
    message: str, expected: str
) -> None:
    assert detect_language(message) == expected


@pytest.mark.asyncio
async def test_markerless_marathi_suggestion_uses_classifier_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = "पीक अवर्समध्ये गाड्यांची फ्रिक्वेन्सी अजून वाढवावी"

    async def marathi_classifier(_message: str, instruction: str) -> dict:
        assert '"detected_language": "english" | "hindi" | "marathi"' in instruction
        return {
            "intent": "direct_query",
            "detected_language": "marathi",
            "classification_confident": True,
            "categories": ["suggestion"],
            "subcategories": ["Train Operation & Services"],
            "extracted_details": {
                "metro_station": None,
                "ticket_number": None,
                "payment_method": None,
                "passenger_name": None,
            },
            "clarification_question": None,
            "clarification_options": None,
            "reference_topics": [],
            "asking_about_complaint_status": False,
        }

    monkeypatch.setattr(llm_client, "_classify_message_openrouter", marathi_classifier)

    classification = await classify_message(message)
    reply = complaint_tracking.suggestion_confirmation_reply(
        "PMC-123456",
        message,
        language=classification["detected_language"],
    )

    assert classification["detected_language"] == "marathi"
    assert reply.startswith("सूचनेबद्दल धन्यवाद!")
    assert "संदर्भ आयडी PMC-123456" in reply


def test_zero_marker_devanagari_confirmation_never_falls_back_to_english() -> None:
    message = "प्रवासी सुविधा उत्कृष्ट"
    language = detect_language(message)

    complaint_reply = complaint_tracking.complaint_confirmation_reply(
        "PMC-111111", message, language=language
    )
    suggestion_reply = complaint_tracking.suggestion_confirmation_reply(
        "PMC-222222", message, language=language
    )

    assert language == "marathi"
    assert "Thank you for reporting this" not in complaint_reply
    assert "Thanks for the suggestion" not in suggestion_reply
    assert "तक्रार" in complaint_reply
    assert "सूचनेबद्दल" in suggestion_reply


def test_romanized_marathi_gets_romanized_out_of_scope_reply() -> None:
    message = "Kiti Astra"
    language, script = resolve_reply_language(message)

    reply = generate_out_of_scope_reply(message, language, script)

    assert (language, script) == ("marathi", "latin")
    assert reply.startswith("Mi tumhala phakt Pune Metro shi sambandhit")
    assert not any("\u0900" <= character <= "\u097f" for character in reply)


def test_devanagari_marathi_gets_devanagari_out_of_scope_reply() -> None:
    message = "किती आहे"
    language, script = resolve_reply_language(message)

    reply = generate_out_of_scope_reply(message, language, script)

    assert (language, script) == ("marathi", "devanagari")
    assert reply.startswith("मी फक्त पुणे मेट्रोशी")


@pytest.mark.parametrize(
    ("message", "expected_script"),
    [
        ("किती आहे please tell me the complete fare details", "latin"),
        ("किती आहे भाडे सांगा please", "devanagari"),
    ],
)
def test_mixed_input_uses_dominant_script(
    message: str, expected_script: str
) -> None:
    language, script = resolve_reply_language(message)

    assert language == "marathi"
    assert script == expected_script
    assert script == detect_script(message)


@pytest.mark.asyncio
async def test_preferred_marathi_overrides_english_followup_for_generated_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_languages: list[str] = []

    async def marathi_provider(*args: object, **_kwargs: object) -> str:
        captured_languages.append(args[-1])
        return "पुढील मेट्रोची वेळ सकाळी ६ वाजता आहे."

    monkeypatch.setattr(llm_client, "_generate_reply_openrouter", marathi_provider)

    reply = await generate_reply(
        "What are the metro timings?",
        [],
        preferred_language="marathi",
    )

    assert captured_languages == ["marathi"]
    assert reply == "पुढील मेट्रोची वेळ सकाळी ६ वाजता आहे."
    assert "rephrase in English" not in reply


@pytest.mark.asyncio
async def test_language_switch_webhook_persists_marathi_and_bypasses_classifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Session:
        def __init__(self) -> None:
            self.user = SimpleNamespace(id=1, name=None)
            self.conversation = SimpleNamespace(id=1, pending_category=None, status="active")
            self.messages: list[Message] = []

        def scalar(self, query: object) -> object | None:
            query_text = str(query)
            if "messages.whatsapp_message_id" in query_text:
                message_id = next(iter(query.compile().params.values()))
                return next(
                    (item for item in self.messages if item.whatsapp_message_id == message_id),
                    None,
                )
            if "users.whatsapp_number" in query_text:
                return self.user
            if "conversations.user_id" in query_text:
                return self.conversation
            return None

        def add(self, instance: object) -> None:
            if isinstance(instance, Message):
                self.messages.append(instance)

        def commit(self) -> None:
            pass

    async def classifier_must_not_run(*_args: object, **_kwargs: object) -> dict:
        raise AssertionError("language control message must bypass classification")

    outbound: list[dict] = []

    async def send_text(**kwargs: object) -> None:
        outbound.append(kwargs)

    monkeypatch.setattr(webhook, "classify_message", classifier_must_not_run)
    monkeypatch.setattr(webhook.whatsapp_client, "send_text_message", send_text)
    db = Session()
    payload = {
        "entry": [{"changes": [{"value": {"messages": [{
            "id": "wamid.language.1",
            "from": "919999999999",
            "type": "text",
            "text": {"body": "Mahiti mala marathi madhe bhetu shakel"},
        }]}}]}]
    }

    await webhook.receive_webhook(payload, db)

    assert db.conversation.preferred_language == "marathi"
    assert outbound == [{
        "to": "919999999999",
        "body": "Nakki, ata mi tumhala Marathit uttar dein.",
    }]
