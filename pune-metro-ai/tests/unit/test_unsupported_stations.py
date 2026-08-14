import os
from types import SimpleNamespace

os.environ.setdefault("WHATSAPP_VERIFY_TOKEN", "test")
os.environ.setdefault("WHATSAPP_ACCESS_TOKEN", "test")
os.environ.setdefault("WHATSAPP_PHONE_NUMBER_ID", "test")
os.environ.setdefault("PRIMARY_LLM_API_KEY", "test")
os.environ.setdefault("FALLBACK_LLM_API_KEY", "test")
os.environ.setdefault("DATABASE_URL", "sqlite://")

import pytest

from app.api import whatsapp_webhook as webhook
from app.db.models import Message
from app.services.llm_client import (
    _is_unsupported_station,
    find_unsupported_station_names,
    generate_unsupported_station_reply,
    is_station_or_route_question,
)


class WebhookSession:
    """Small stateful session double for planned-station webhook routing."""

    def __init__(self) -> None:
        self.user = SimpleNamespace(id=1, name=None)
        self.conversation = SimpleNamespace(id=1, pending_category=None, status="active")
        self.messages: list[Message] = []

    def scalar(self, query: object) -> object | None:
        query_text = str(query)
        if "messages.whatsapp_message_id" in query_text:
            message_id = next(iter(query.compile().params.values()))
            return next(
                (message for message in self.messages if message.whatsapp_message_id == message_id),
                None,
            )
        if "users.whatsapp_number" in query_text:
            return self.user
        if "conversations.user_id" in query_text:
            return self.conversation
        return None

    def scalars(self, _query: object) -> list[Message]:
        return list(reversed(self.messages))

    def add(self, instance: object) -> None:
        if isinstance(instance, Message):
            self.messages.append(instance)

    def flush(self) -> None:
        pass

    def commit(self) -> None:
        pass


def _text_webhook(message_id: str, body: str) -> dict:
    return {
        "entry": [{"changes": [{"value": {"messages": [{
            "id": message_id,
            "from": "919999999999",
            "type": "text",
            "text": {"body": body},
        }]}}]}]
    }


def test_hinjewadi_is_recognized_as_planned_but_operational_stations_are_not() -> None:
    assert _is_unsupported_station("Hinjewadi") is True
    assert _is_unsupported_station("Hinjawadi") is True
    assert _is_unsupported_station("Hinjavadi") is True
    assert _is_unsupported_station("Bane") is True
    assert _is_unsupported_station("Swargate") is False
    assert _is_unsupported_station("Shivaji Nagar") is False
    assert find_unsupported_station_names("PCMC to Hinjewadi kasa jaycha?") == ["Hinjewadi"]
    assert find_unsupported_station_names("PCMC to Swargate") == []
    assert find_unsupported_station_names(
        "भानेर ते हिंजवडी मेट्रो लाईन कधी चालू होणार आहे?"
    ) == ["Baner", "Hinjewadi"]
    assert find_unsupported_station_names(
        "I want to go from Banner to Hinjavadi"
    ) == ["Baner", "Hinjewadi"]


def test_appreciation_and_operating_hours_are_not_mistaken_for_station_query() -> None:
    message = (
        "Hey I want to give an appreciation regarding Pune Metro. It gives good "
        "connection for Pune city and it is great that they are open from 6 to 11"
    )
    assert is_station_or_route_question(message) is False
    assert is_station_or_route_question("How can I go from Foo to Bar?") is True
    assert is_station_or_route_question("Which station is closest?") is True


def test_marathi_station_case_suffixes_preserve_station_matching() -> None:
    from app.services.llm_client import find_station_names

    assert find_station_names("बन गार्डन ते रामवाडीचं भाडं किती आहे?") == [
        "Bund Garden",
        "Ramwadi",
    ]
    assert find_station_names("पीसीएमसीवरती लिफ्ट बंद होती") == ["PCMC"]


@pytest.mark.asyncio
async def test_pcms_to_hinjewadi_uses_under_construction_reply_before_classifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outbound_texts: list[dict] = []

    async def classifier_must_not_run(*_args: object, **_kwargs: object) -> dict:
        raise AssertionError("planned-station route should bypass LLM classification")

    async def send_text(**kwargs: object) -> None:
        outbound_texts.append(kwargs)

    question = "PCMC to Hinjewadi kasa jaycha?"
    monkeypatch.setattr(webhook, "classify_message", classifier_must_not_run)
    monkeypatch.setattr(webhook.whatsapp_client, "send_text_message", send_text)

    await webhook.receive_webhook(_text_webhook("wamid.upcoming.1", question), WebhookSession())

    assert outbound_texts == [{
        "to": "919999999999", "body": generate_unsupported_station_reply(question)
    }]


@pytest.mark.asyncio
async def test_pcms_to_swargate_remains_on_normal_route_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outbound_texts: list[dict] = []

    async def normal_classifier(*_args: object, **_kwargs: object) -> dict:
        return {
            "intent": "direct_query",
            "detected_language": "english",
            "classification_confident": True,
            "categories": ["enquiry"],
            "subcategories": [],
            "extracted_details": {
                "metro_station": None,
                "ticket_number": None,
                "payment_method": None,
                "passenger_name": None,
            },
            "clarification_question": None,
            "clarification_options": None,
            "reference_topics": ["stations"],
            "asking_about_complaint_status": False,
        }

    async def normal_reply(*_args: object, **_kwargs: object) -> str:
        return "Normal operational-route reply."

    async def send_text(**kwargs: object) -> None:
        outbound_texts.append(kwargs)

    monkeypatch.setattr(webhook, "classify_message", normal_classifier)
    monkeypatch.setattr(webhook, "generate_reply", normal_reply)
    monkeypatch.setattr(webhook.whatsapp_client, "send_text_message", send_text)

    await webhook.receive_webhook(
        _text_webhook("wamid.operational.1", "PCMC to Swargate"), WebhookSession()
    )

    assert outbound_texts == [{
        "to": "919999999999",
        "body": "Namaskar! Normal operational-route reply.",
    }]
