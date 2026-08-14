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
    CLASSIFICATION_SYSTEM_MESSAGE,
    classify_message,
    generate_out_of_scope_reply,
    is_confusion_message,
    short_message_intent,
)
from app.services.whatsapp_client import WHATSAPP_LIST_TITLE_MAX_LENGTH


class FakeSession:
    def __init__(self, recent_messages: list[SimpleNamespace]) -> None:
        self.recent_messages = recent_messages
        self.commit_count = 0

    def commit(self) -> None:
        self.commit_count += 1

    def scalars(self, _query: object) -> list[SimpleNamespace]:
        return self.recent_messages


@pytest.mark.parametrize(
    "message",
    [
        "I am confused",
        "I am confused about the fare",
        "I don't understand",
        "what do you mean",
        "huh?",
    ],
)
def test_confusion_signals_are_deterministic(message: str) -> None:
    assert is_confusion_message(message)


@pytest.mark.parametrize("message", ["Heyy", "Heyyy", "Hii", "Helloo"])
def test_casual_greeting_variants_never_match_confusion(message: str) -> None:
    assert is_confusion_message(message) is False
    assert short_message_intent(message) == "greeting"


def test_classifier_contract_requires_an_explicit_ambiguity_confidence_signal() -> None:
    assert '"classification_confident": boolean' in CLASSIFICATION_SYSTEM_MESSAGE
    assert 'intent to "ambiguous", classification_confident to false' in CLASSIFICATION_SYSTEM_MESSAGE
    assert '"out_of_scope"' in CLASSIFICATION_SYSTEM_MESSAGE


def _classification(*, intent: str, confident: bool, categories: list[str]) -> dict:
    return {
        "intent": intent,
        "detected_language": "english",
        "classification_confident": confident,
        "categories": categories,
        "subcategories": [],
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


def test_main_menu_has_exactly_the_required_five_short_titles() -> None:
    titles = [title for _, title in webhook.CATEGORY_MENU_OPTIONS]
    assert titles == ["Complaint", "Suggestion", "Appreciation", "Enquiry", "Others"]
    assert all(len(title) <= WHATSAPP_LIST_TITLE_MAX_LENGTH for title in titles)


@pytest.mark.asyncio
@pytest.mark.parametrize("pending_category", ["suggestion", "complaint", "enquiry"])
async def test_confusion_resets_every_nested_category_to_the_shared_main_menu(
    monkeypatch: pytest.MonkeyPatch, pending_category: str
) -> None:
    sent: list[dict] = []

    async def send_list(**kwargs: object) -> None:
        sent.append(kwargs)

    monkeypatch.setattr(webhook.whatsapp_client, "send_interactive_list", send_list)
    conversation = SimpleNamespace(id=1, pending_category=pending_category)
    db = FakeSession(
        [
            SimpleNamespace(content="I am confused"),
            SimpleNamespace(content="my previous detail"),
        ]
    )

    await webhook._handle_confusion_message(
        sender="919999999999",
        message_text="I am confused",
        conversation=conversation,
        db=db,
    )

    assert conversation.pending_category is None
    assert db.commit_count == 1
    assert len(sent) == 1
    rows = sent[0]["sections"][0]["rows"]
    assert [row["title"] for row in rows] == [
        "Complaint", "Suggestion", "Appreciation", "Enquiry", "Others",
    ]


@pytest.mark.asyncio
async def test_second_consecutive_confusion_offers_handoff_not_a_third_menu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    texts: list[dict] = []
    lists: list[dict] = []

    async def send_text(**kwargs: object) -> None:
        texts.append(kwargs)

    async def send_list(**kwargs: object) -> None:
        lists.append(kwargs)

    monkeypatch.setattr(webhook.whatsapp_client, "send_text_message", send_text)
    monkeypatch.setattr(webhook.whatsapp_client, "send_interactive_list", send_list)
    conversation = SimpleNamespace(
        id=1, pending_category="suggestion", unclear_streak_count=1
    )
    db = FakeSession(
        [SimpleNamespace(content="huh"), SimpleNamespace(content="I am confused")]
    )

    await webhook._handle_confusion_message(
        sender="919999999999",
        message_text="huh",
        conversation=conversation,
        db=db,
    )

    assert conversation.pending_category is None
    assert not lists
    assert texts == [{"to": "919999999999", "body": webhook.CONFUSION_HANDOFF_REPLY}]


@pytest.mark.asyncio
async def test_handoff_state_restarts_with_menu_before_the_next_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    texts: list[dict] = []
    lists: list[dict] = []

    async def send_text(**kwargs: object) -> None:
        texts.append(kwargs)

    async def send_list(**kwargs: object) -> None:
        lists.append(kwargs)

    monkeypatch.setattr(webhook.whatsapp_client, "send_text_message", send_text)
    monkeypatch.setattr(webhook.whatsapp_client, "send_interactive_list", send_list)
    conversation = SimpleNamespace(
        id=1,
        pending_category=None,
        confusion_handoff_shown=False,
        unclear_streak_count=1,
    )
    db = FakeSession(
        [SimpleNamespace(content="huh"), SimpleNamespace(content="I am confused")]
    )

    # Second unclear message: handoff.
    await webhook._handle_confusion_message(
        sender="919999999999",
        message_text="huh",
        conversation=conversation,
        db=db,
    )
    assert conversation.confusion_handoff_shown is True
    # First new unclear message after handoff: menu, never a repeated handoff.
    db.recent_messages = [SimpleNamespace(content="wait what"), SimpleNamespace(content="huh")]
    await webhook._handle_confusion_message(
        sender="919999999999",
        message_text="wait what",
        conversation=conversation,
        db=db,
    )
    assert conversation.confusion_handoff_shown is False
    # Second unclear message in the new cycle: handoff again.
    db.recent_messages = [SimpleNamespace(content="asdkjfh"), SimpleNamespace(content="wait what")]
    await webhook._handle_confusion_message(
        sender="919999999999",
        message_text="asdkjfh",
        conversation=conversation,
        db=db,
    )

    assert len(lists) == 1
    assert len(texts) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["wait what", "??", "this isn't helping", "asdkjfh"])
async def test_llm_low_confidence_routes_novel_ambiguous_text_to_main_menu(
    monkeypatch: pytest.MonkeyPatch, message: str
) -> None:
    outbound_lists: list[dict] = []

    async def low_confidence_classifier(*_args: object, **_kwargs: object) -> dict:
        return _classification(intent="ambiguous", confident=False, categories=[])

    async def send_list(**kwargs: object) -> None:
        outbound_lists.append(kwargs)

    monkeypatch.setattr(webhook, "classify_message", low_confidence_classifier)
    monkeypatch.setattr(webhook.whatsapp_client, "send_interactive_list", send_list)
    db = WebhookSession()

    assert is_confusion_message(message) is False
    await webhook.receive_webhook(_text_webhook(f"wamid.unclear.{message}", message), db)

    assert len(outbound_lists) == 1
    assert [row["title"] for row in outbound_lists[0]["sections"][0]["rows"]] == [
        "Complaint", "Suggestion", "Appreciation", "Enquiry", "Others",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["I am confused", "huh?"])
async def test_deterministic_confusion_skips_the_llm_classifier(
    monkeypatch: pytest.MonkeyPatch, message: str
) -> None:
    outbound_lists: list[dict] = []

    async def classifier_must_not_run(*_args: object, **_kwargs: object) -> dict:
        raise AssertionError("deterministic confusion should bypass the LLM")

    async def send_list(**kwargs: object) -> None:
        outbound_lists.append(kwargs)

    monkeypatch.setattr(webhook, "classify_message", classifier_must_not_run)
    monkeypatch.setattr(webhook.whatsapp_client, "send_interactive_list", send_list)
    db = WebhookSession()

    await webhook.receive_webhook(_text_webhook(f"wamid.fast.{message}", message), db)

    assert len(outbound_lists) == 1


def test_clear_classification_is_confident_and_not_an_unclear_signal() -> None:
    fare_question = "What is the fare from PCMC to Vanaz?"
    result = _classification(intent="direct_query", confident=True, categories=["enquiry"])
    assert is_confusion_message(fare_question) is False
    assert result["classification_confident"] is True
    assert result["categories"] == ["enquiry"]


@pytest.mark.asyncio
async def test_classifier_returns_out_of_scope_for_a_clear_unrelated_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def out_of_scope_provider(*_args: object, **_kwargs: object) -> dict:
        return _classification(intent="out_of_scope", confident=True, categories=[])

    monkeypatch.setattr(
        "app.services.llm_client._classify_message_openrouter", out_of_scope_provider
    )

    result = await classify_message("how to book a bus from Mumbai to Pune")

    assert result["intent"] == "out_of_scope"
    assert result["classification_confident"] is True
    assert result["categories"] == []


@pytest.mark.asyncio
async def test_out_of_scope_webhook_declines_without_menu_even_mid_complaint_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outbound_texts: list[dict] = []
    outbound_lists: list[dict] = []

    async def out_of_scope_classifier(*_args: object, **_kwargs: object) -> dict:
        return _classification(intent="out_of_scope", confident=True, categories=[])

    async def send_text(**kwargs: object) -> None:
        outbound_texts.append(kwargs)

    async def send_list(**kwargs: object) -> None:
        outbound_lists.append(kwargs)

    monkeypatch.setattr(webhook, "classify_message", out_of_scope_classifier)
    monkeypatch.setattr(webhook.whatsapp_client, "send_text_message", send_text)
    monkeypatch.setattr(webhook.whatsapp_client, "send_interactive_list", send_list)
    db = WebhookSession()
    db.conversation.pending_category = "complaint"
    question = "how to book a bus from Mumbai to Pune"

    await webhook.receive_webhook(_text_webhook("wamid.scope.1", question), db)

    assert outbound_texts == [{
        "to": "919999999999",
        "body": generate_out_of_scope_reply(question, "english"),
    }]
    assert outbound_lists == []
    assert db.conversation.pending_category == "complaint"


@pytest.mark.asyncio
@pytest.mark.parametrize("question", ["123 x 456 kiti?", "Wi-Fi का चालत नाही?"])
async def test_math_and_generic_wifi_out_of_scope_results_send_scope_reply_not_menu(
    monkeypatch: pytest.MonkeyPatch, question: str
) -> None:
    outbound_texts: list[dict] = []
    outbound_lists: list[dict] = []

    async def out_of_scope_classifier(*_args: object, **_kwargs: object) -> dict:
        return _classification(intent="out_of_scope", confident=True, categories=[])

    async def send_text(**kwargs: object) -> None:
        outbound_texts.append(kwargs)

    async def send_list(**kwargs: object) -> None:
        outbound_lists.append(kwargs)

    monkeypatch.setattr(webhook, "classify_message", out_of_scope_classifier)
    monkeypatch.setattr(webhook.whatsapp_client, "send_text_message", send_text)
    monkeypatch.setattr(webhook.whatsapp_client, "send_interactive_list", send_list)
    db = WebhookSession()

    await webhook.receive_webhook(_text_webhook(f"wamid.scope.{question}", question), db)

    assert outbound_texts == [{
        "to": "919999999999",
        "body": generate_out_of_scope_reply(question, "english"),
    }]
    assert outbound_lists == []


@pytest.mark.parametrize(
    ("message", "language", "expected_fragment"),
    [
        ("How do I book a Mumbai bus?", "english", "I can only help"),
        ("मुंबई बस कैसे बुक करूं?", "hindi", "मैं केवल पुणे मेट्रो"),
        ("मुंबईची बस कुठे आहे?", "marathi", "मी फक्त पुणे मेट्रो"),
    ],
)
def test_out_of_scope_reply_uses_classified_supported_language(
    message: str, language: str, expected_fragment: str
) -> None:
    assert expected_fragment in generate_out_of_scope_reply(message, language)


@pytest.mark.asyncio
async def test_genuine_pune_metro_fare_question_remains_a_normal_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fare_provider(*_args: object, **_kwargs: object) -> dict:
        return _classification(intent="direct_query", confident=True, categories=["enquiry"])

    monkeypatch.setattr("app.services.llm_client._classify_message_openrouter", fare_provider)

    result = await classify_message("What is the Pune Metro fare from PCMC to Vanaz?")

    assert result["intent"] == "direct_query"
    assert result["classification_confident"] is True
    assert result["categories"] == ["enquiry"]


@pytest.mark.asyncio
async def test_others_selection_starts_the_open_ended_enquiry_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompts: list[dict] = []

    async def category_prompt(
        _category: str, _last_message: str, _language: str, _script: str
    ) -> str:
        return "Please share your question."

    async def send_text(**kwargs: object) -> None:
        prompts.append(kwargs)

    monkeypatch.setattr(webhook, "generate_category_prompt", category_prompt)
    monkeypatch.setattr(webhook.whatsapp_client, "send_text_message", send_text)
    conversation = SimpleNamespace(id=1, pending_category=None)
    db = FakeSession([SimpleNamespace(content="I need something else")])
    db.scalar = lambda _query: SimpleNamespace(content="I need something else")

    await webhook._handle_interactive_reply(
        reply_id="category:other_help",
        interactive_message_id="wamid.1",
        sender="919999999999",
        conversation=conversation,
        db=db,
    )

    assert conversation.pending_category == "enquiry"
    assert prompts == [{"to": "919999999999", "body": "Please share your question."}]


class WebhookSession:
    """Small stateful session double for exercising the complete webhook route."""

    def __init__(self) -> None:
        self.user = SimpleNamespace(id=1, name=None)
        self.conversation = SimpleNamespace(id=1, pending_category=None, status="active")
        self.messages: list[Message] = []

    def scalar(self, query: object) -> object | None:
        query_text = str(query)
        if "messages.whatsapp_message_id" in query_text:
            # The only parameterized id lookup in this endpoint is the idempotency check.
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
        if "messages.role" in query_text:
            return next(
                (message for message in self.messages if message.role == "assistant"),
                None,
            )
        return None

    def scalars(self, _query: object) -> list[Message]:
        return list(reversed([message for message in self.messages if message.role == "user"]))

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
            "id": message_id, "from": "919999999999", "type": "text", "text": {"body": body},
        }]}}]}]
    }


@pytest.mark.asyncio
async def test_full_confusion_repro_ends_with_one_greeting_menu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outbound_lists: list[dict] = []
    outbound_texts: list[dict] = []

    async def send_list(**kwargs: object) -> None:
        outbound_lists.append(kwargs)

    async def send_text(**kwargs: object) -> None:
        outbound_texts.append(kwargs)

    monkeypatch.setattr(webhook.whatsapp_client, "send_interactive_list", send_list)
    monkeypatch.setattr(webhook.whatsapp_client, "send_text_message", send_text)
    db = WebhookSession()

    await webhook.receive_webhook(_text_webhook("wamid.confusion.1", "I am confused"), db)
    await webhook.receive_webhook(_text_webhook("wamid.confusion.2", "huh?"), db)
    await webhook.receive_webhook(_text_webhook("wamid.greeting.1", "Heyy"), db)

    assert len(outbound_lists) == 2
    assert len(outbound_texts) == 1
    assert outbound_texts[0]["body"] == webhook.CONFUSION_HANDOFF_REPLY
    assert [row["title"] for row in outbound_lists[-1]["sections"][0]["rows"]] == [
        "Complaint", "Suggestion", "Appreciation", "Enquiry", "Others",
    ]


@pytest.mark.asyncio
async def test_duplicate_greeting_webhook_id_sends_exactly_one_outbound_menu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outbound_lists: list[dict] = []
    outbound_texts: list[dict] = []

    async def send_list(**kwargs: object) -> None:
        outbound_lists.append(kwargs)

    async def send_text(**kwargs: object) -> None:
        outbound_texts.append(kwargs)

    monkeypatch.setattr(webhook.whatsapp_client, "send_interactive_list", send_list)
    monkeypatch.setattr(webhook.whatsapp_client, "send_text_message", send_text)
    db = WebhookSession()
    payload = _text_webhook("wamid.greeting.duplicate", "Hey")

    await webhook.receive_webhook(payload, db)
    await webhook.receive_webhook(payload, db)

    assert len(outbound_lists) == 1
    assert len(db.messages) == 2
    assert [message.role for message in db.messages] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_complaint_requires_validated_fields_and_confirmation_before_tracking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outbound_texts: list[dict] = []
    created: list[dict] = []

    async def complaint_classifier(*_args: object, **_kwargs: object) -> dict:
        return _classification(intent="direct_query", confident=True, categories=["complaint"])

    def create_tracking(**kwargs: object) -> SimpleNamespace:
        created.append(kwargs)
        return SimpleNamespace(token="PMC-482913", status="pending")

    async def send_text(**kwargs: object) -> None:
        outbound_texts.append(kwargs)

    monkeypatch.setattr(webhook, "classify_message", complaint_classifier)
    monkeypatch.setattr(
        "app.services.collection_flow.create_complaint_tracking", create_tracking
    )
    monkeypatch.setattr(webhook.whatsapp_client, "send_text_message", send_text)
    db = WebhookSession()

    await webhook.receive_webhook(
        _text_webhook("wamid.complaint.1", "The lift at PCMC is not working"), db
    )

    assert created == []
    assert db.conversation.pending_category == "complaint"
    assert db.conversation.complaint_collection_state == "collecting_name"
    reply = outbound_texts[0]["body"].casefold()
    assert "name" in reply
    assert "pmc-" not in reply
    assert "phone" not in reply
    assert "helpline" not in reply
    assert "1800" not in reply


@pytest.mark.asyncio
async def test_suggestion_requires_details_and_confirmation_before_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outbound_texts: list[dict] = []
    created: list[dict] = []

    async def suggestion_classifier(*_args: object, **_kwargs: object) -> dict:
        return _classification(intent="direct_query", confident=True, categories=["suggestion"])

    def create_tracking(**kwargs: object) -> SimpleNamespace:
        created.append(kwargs)
        return SimpleNamespace(token="PMC-123456", status="pending")

    async def send_text(**kwargs: object) -> None:
        outbound_texts.append(kwargs)

    monkeypatch.setattr(webhook, "classify_message", suggestion_classifier)
    monkeypatch.setattr(
        "app.services.collection_flow.create_complaint_tracking", create_tracking
    )
    monkeypatch.setattr(webhook.whatsapp_client, "send_text_message", send_text)
    db = WebhookSession()

    await webhook.receive_webhook(
        _text_webhook("wamid.suggestion.1", "Please add more bicycle parking"), db
    )

    assert created == []
    assert db.conversation.pending_category == "suggestion"
    assert db.conversation.complaint_collection_state == "collecting_name"
    assert len(outbound_texts) == 1
    assert "name" in outbound_texts[0]["body"].casefold()
    assert "PMC-" not in outbound_texts[0]["body"]
    assert "complaint" not in outbound_texts[0]["body"].casefold()


@pytest.mark.asyncio
@pytest.mark.parametrize("category", ["appreciation", "enquiry"])
async def test_untracked_category_creates_no_tracking_row(
    monkeypatch: pytest.MonkeyPatch, category: str
) -> None:
    async def classifier(*_args: object, **_kwargs: object) -> dict:
        return _classification(intent="direct_query", confident=True, categories=[category])

    async def normal_reply(*_args: object, **_kwargs: object) -> str:
        return "Normal reply"

    async def send_text(**_kwargs: object) -> None:
        pass

    def tracking_must_not_run(**_kwargs: object) -> SimpleNamespace:
        raise AssertionError(f"{category} must not create a tracking row")

    monkeypatch.setattr(webhook, "classify_message", classifier)
    monkeypatch.setattr(webhook, "generate_reply", normal_reply)
    monkeypatch.setattr(
        "app.services.collection_flow.create_complaint_tracking", tracking_must_not_run
    )
    monkeypatch.setattr(webhook.whatsapp_client, "send_text_message", send_text)

    await webhook.receive_webhook(
        _text_webhook(f"wamid.{category}.1", f"A {category} message"), WebhookSession()
    )


@pytest.mark.asyncio
async def test_multi_category_turn_starts_one_confirmable_workflow_without_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outbound_texts: list[dict] = []
    created: list[dict] = []
    tokens = iter(["PMC-111111", "PMC-222222"])

    async def classifier(*_args: object, **_kwargs: object) -> dict:
        return _classification(
            intent="direct_query",
            confident=True,
            categories=["complaint", "suggestion"],
        )

    def create_tracking(**kwargs: object) -> SimpleNamespace:
        created.append(kwargs)
        return SimpleNamespace(token=next(tokens), status="pending")

    async def send_text(**kwargs: object) -> None:
        outbound_texts.append(kwargs)

    monkeypatch.setattr(webhook, "classify_message", classifier)
    monkeypatch.setattr(
        "app.services.collection_flow.create_complaint_tracking", create_tracking
    )
    monkeypatch.setattr(webhook.whatsapp_client, "send_text_message", send_text)

    await webhook.receive_webhook(
        _text_webhook(
            "wamid.complaint-suggestion.1",
            "The lift is broken, and please add better signs",
        ),
        WebhookSession(),
    )

    assert created == []
    assert len(outbound_texts) == 1
    assert "name" in outbound_texts[0]["body"].casefold()
    assert "PMC-" not in outbound_texts[0]["body"]


@pytest.mark.asyncio
async def test_complaint_phone_followup_redirects_to_existing_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outbound_texts: list[dict] = []

    async def classifier_must_not_run(*_args: object, **_kwargs: object) -> dict:
        raise AssertionError("complaint phone follow-up must bypass normal classification")

    async def send_text(**kwargs: object) -> None:
        outbound_texts.append(kwargs)

    monkeypatch.setattr(webhook, "classify_message", classifier_must_not_run)
    monkeypatch.setattr(
        webhook,
        "get_latest_complaint_tracking",
        lambda *_args: SimpleNamespace(
            token="PMC-482913", status="pending", conversation_id=1
        ),
    )
    monkeypatch.setattr(webhook.whatsapp_client, "send_text_message", send_text)
    db = WebhookSession()

    await webhook.receive_webhook(
        _text_webhook("wamid.complaint.phone.1", "What phone number can I call?"),
        db,
    )

    assert outbound_texts == [{
        "to": "919999999999",
        "body": (
            "Your complaint PMC-482913 is being reviewed by our team; you'll receive an "
            "update directly here once it's processed."
        ),
    }]
    assert "1800" not in outbound_texts[0]["body"]


@pytest.mark.asyncio
async def test_general_contact_query_remains_outside_the_complaint_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outbound_texts: list[dict] = []

    async def contact_classifier(*_args: object, **_kwargs: object) -> dict:
        result = _classification(intent="direct_query", confident=True, categories=["enquiry"])
        result["reference_topics"] = ["contact"]
        return result

    async def contact_reply(*_args: object, **_kwargs: object) -> str:
        return "The customer-care number is 1800 270 5501."

    async def send_text(**kwargs: object) -> None:
        outbound_texts.append(kwargs)

    monkeypatch.setattr(webhook, "classify_message", contact_classifier)
    monkeypatch.setattr(webhook, "generate_reply", contact_reply)
    monkeypatch.setattr(webhook.whatsapp_client, "send_text_message", send_text)
    db = WebhookSession()

    await webhook.receive_webhook(_text_webhook("wamid.contact.1", "customer care number"), db)

    assert outbound_texts == [{
        "to": "919999999999",
        "body": "Namaskar! The customer-care number is 1800 270 5501.",
    }]


@pytest.mark.asyncio
async def test_only_first_direct_query_reply_gets_language_matched_greeting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outbound_texts: list[dict] = []

    async def fare_classifier(*_args: object, **_kwargs: object) -> dict:
        result = _classification(
            intent="direct_query", confident=True, categories=["enquiry"]
        )
        result["detected_language"] = "marathi"
        return result

    async def fare_reply(*_args: object, **_kwargs: object) -> str:
        return "पीसीएमसी ते स्वारगेट भाडे ₹30 आहे."

    async def send_text(**kwargs: object) -> None:
        outbound_texts.append(kwargs)

    monkeypatch.setattr(webhook, "classify_message", fare_classifier)
    monkeypatch.setattr(webhook, "generate_reply", fare_reply)
    monkeypatch.setattr(webhook.whatsapp_client, "send_text_message", send_text)
    db = WebhookSession()

    await webhook.receive_webhook(
        _text_webhook("wamid.fare.first", "PCMC to Swargate fare kiti?"), db
    )
    await webhook.receive_webhook(
        _text_webhook("wamid.fare.second", "Return fare kiti?"), db
    )

    assert outbound_texts[0]["body"] == (
        "Namaskar! पीसीएमसी ते स्वारगेट भाडे ₹30 आहे."
    )
    assert outbound_texts[1]["body"] == "पीसीएमसी ते स्वारगेट भाडे ₹30 आहे."


@pytest.mark.asyncio
async def test_typing_indicator_starts_before_reply_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_order: list[tuple[str, str]] = []

    async def mark_as_read_and_typing(message_id: str) -> None:
        call_order.append(("typing", message_id))

    async def classifier(message: str, *_args: object) -> dict:
        call_order.append(("classify", message))
        return _classification(
            intent="direct_query", confident=True, categories=["enquiry"]
        )

    async def reply_generator(message: str, *_args: object, **_kwargs: object) -> str:
        call_order.append(("reply", message))
        return "The fare is ₹30."

    async def send_text(**_kwargs: object) -> None:
        pass

    monkeypatch.setattr(
        webhook.whatsapp_client,
        "mark_as_read_and_typing",
        mark_as_read_and_typing,
    )
    monkeypatch.setattr(webhook, "classify_message", classifier)
    monkeypatch.setattr(webhook, "generate_reply", reply_generator)
    monkeypatch.setattr(webhook.whatsapp_client, "send_text_message", send_text)

    await webhook.receive_webhook(
        _text_webhook("wamid.typing.1", "What is the fare?"),
        WebhookSession(),
    )

    assert call_order == [
        ("typing", "wamid.typing.1"),
        ("classify", "What is the fare?"),
        ("reply", "What is the fare?"),
    ]
