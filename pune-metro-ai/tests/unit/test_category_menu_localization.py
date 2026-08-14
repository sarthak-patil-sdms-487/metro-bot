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
from app.services.whatsapp_client import WHATSAPP_LIST_TITLE_MAX_LENGTH


class GreetingSession:
    """Small session double for the complete deterministic greeting route."""

    def __init__(self) -> None:
        self.user = SimpleNamespace(id=1, name=None)
        self.conversation = SimpleNamespace(
            id=1,
            pending_category=None,
            status="active",
            confusion_handoff_shown=False,
            unclear_streak_count=0,
        )
        self.messages: list[Message] = []

    def scalar(self, query: object) -> object | None:
        query_text = str(query)
        if "messages.whatsapp_message_id" in query_text:
            message_id = next(iter(query.compile().params.values()))
            return next(
                (
                    message
                    for message in self.messages
                    if message.whatsapp_message_id == message_id
                ),
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


class InteractiveReplySession:
    def __init__(self, previous_message: str) -> None:
        self.previous_message = SimpleNamespace(content=previous_message)
        self.commit_count = 0

    def scalar(self, _query: object) -> SimpleNamespace:
        return self.previous_message

    def commit(self) -> None:
        self.commit_count += 1


def _text_webhook(message_id: str, body: str) -> dict:
    return {
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{
                        "id": message_id,
                        "from": "919999999999",
                        "type": "text",
                        "text": {"body": body},
                    }]
                }
            }]
        }]
    }


@pytest.mark.asyncio
async def test_marathi_category_list_localizes_all_visible_menu_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[dict] = []

    async def send_list(**kwargs: object) -> None:
        sent.append(kwargs)

    monkeypatch.setattr(webhook.whatsapp_client, "send_interactive_list", send_list)

    await webhook._send_category_list("919999999999", "marathi")

    assert sent == [{
        "to": "919999999999",
        "header": "आम्ही तुम्हाला कशी मदत करू शकतो?",
        "body": "तुमच्या संदेशाशी सर्वात जुळणारा विषय निवडा.",
        "button_text": "विषय पहा",
        "sections": [{
            "title": "श्रेणी",
            "rows": [
                {
                    "id": "category:complaint",
                    "title": "तक्रार",
                    "description": "तक्रार बद्दल",
                },
                {
                    "id": "category:suggestion",
                    "title": "सूचना",
                    "description": "सूचना बद्दल",
                },
                {
                    "id": "category:appreciation",
                    "title": "कौतुक",
                    "description": "कौतुक बद्दल",
                },
                {
                    "id": "category:enquiry",
                    "title": "चौकशी",
                    "description": "चौकशी बद्दल",
                },
                {
                    "id": "category:other_help",
                    "title": "इतर",
                    "description": "इतर बद्दल",
                },
            ],
        }],
    }]


@pytest.mark.asyncio
async def test_marathi_greeting_sends_marathi_category_menu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent_lists: list[dict] = []
    sent_texts: list[dict] = []

    async def send_list(**kwargs: object) -> None:
        sent_lists.append(kwargs)

    async def send_text(**kwargs: object) -> None:
        sent_texts.append(kwargs)

    monkeypatch.setattr(webhook.whatsapp_client, "send_interactive_list", send_list)
    monkeypatch.setattr(webhook.whatsapp_client, "send_text_message", send_text)

    await webhook.receive_webhook(
        _text_webhook("wamid.greeting.marathi", "नमस्कार"),
        GreetingSession(),
    )

    assert len(sent_lists) == 1
    # A direct greeting gets a natural Marathi greeting in the header; the
    # static menu-copy test above covers the generic menu fallback separately.
    assert "पुणे मेट्रो" in sent_lists[0]["header"]
    assert any(char in sent_lists[0]["header"] for char in "नमस्कारस्वागत")
    assert sent_lists[0]["button_text"] == "विषय पहा"
    assert [row["title"] for row in sent_lists[0]["sections"][0]["rows"]] == [
        "तक्रार",
        "सूचना",
        "कौतुक",
        "चौकशी",
        "इतर",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["english", "hindi", "marathi"])
async def test_category_ids_stay_stable_and_translations_fit_whatsapp_limits(
    monkeypatch: pytest.MonkeyPatch, language: str
) -> None:
    sent: list[dict] = []

    async def send_list(**kwargs: object) -> None:
        sent.append(kwargs)

    monkeypatch.setattr(webhook.whatsapp_client, "send_interactive_list", send_list)

    await webhook._send_category_list("919999999999", language)

    rows = sent[0]["sections"][0]["rows"]
    assert [row["id"] for row in rows] == [
        "category:complaint",
        "category:suggestion",
        "category:appreciation",
        "category:enquiry",
        "category:other_help",
    ]
    assert all(len(row["title"]) <= WHATSAPP_LIST_TITLE_MAX_LENGTH for row in rows)
    assert all(len(row["description"]) <= 72 for row in rows)


@pytest.mark.asyncio
async def test_category_prompt_failure_uses_previous_message_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[dict] = []

    async def failed_prompt(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("provider unavailable")

    async def send_text(**kwargs: object) -> None:
        sent.append(kwargs)

    monkeypatch.setattr(webhook, "generate_category_prompt", failed_prompt)
    monkeypatch.setattr(webhook.whatsapp_client, "send_text_message", send_text)
    conversation = SimpleNamespace(
        id=1,
        pending_category=None,
        preferred_language=None,
        confusion_handoff_shown=False,
        unclear_streak_count=0,
    )

    await webhook._handle_interactive_reply(
        reply_id="category:suggestion",
        interactive_message_id="wamid.interactive.1",
        sender="919999999999",
        conversation=conversation,
        db=InteractiveReplySession("मला एक सूचना द्यायची आहे"),
    )

    assert sent == [{
        "to": "919999999999",
        "body": webhook.CATEGORY_INPUT_PROMPTS["marathi"]["suggestion"],
    }]
