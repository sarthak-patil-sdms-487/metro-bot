from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import CategoryLog, ComplaintTracking, Conversation, User
from app.services.voice_dialogue import (
    VoiceFieldUpdates,
    VoiceTurnDecision,
    apply_voice_decision,
)


def _conversation(db: Session) -> Conversation:
    user = User(whatsapp_number="919999999999")
    db.add(user)
    db.flush()
    conversation = Conversation(user_id=user.id, channel="call")
    db.add(conversation)
    db.flush()
    return conversation


def _apply(
    db: Session,
    conversation: Conversation,
    *,
    text: str,
    intent: str = "provide_fields",
    category: str | None = None,
    next_field: str,
    reply: str,
    **fields: str,
):
    return apply_voice_decision(
        decision=VoiceTurnDecision(
            intent=intent,
            language="hindi",
            category=category,
            fields=VoiceFieldUpdates(**fields),
            next_field=next_field,
            reply_text=reply,
            confidence=0.95,
        ),
        text=text,
        conversation=conversation,
        db=db,
        provider="test",
        model="test-controller",
        controller_latency_ms=12,
    )


def test_last_call_replay_collects_facts_answers_status_and_registers_once(
    db: Session,
) -> None:
    conversation = _conversation(db)

    _apply(
        db,
        conversation,
        text="मुझे एक कंप्लेंट करनी थी।",
        intent="start_complaint",
        category="complaint",
        next_field="name",
        reply="ज़रूर, इसे दर्ज करने में मैं आपकी मदद करूँगी। आपका पूरा नाम क्या है?",
    )
    name = _apply(
        db,
        conversation,
        text="मेरा पूरा नाम मनन जैन है।",
        next_field="contact",
        reply="धन्यवाद मनन जैन। अब अपना 10 अंकों का संपर्क नंबर बताइए।",
        full_name="मनन जैन",
    )
    assert conversation.complaint_collection_full_name == "मनन जैन"
    assert name.state_after == "collecting_contact"

    _apply(
        db,
        conversation,
        text="नौ आठ सात छह पांच चार तीन दो एक शून्य",
        next_field="station",
        reply="नंबर मिल गया। यह किस पुणे मेट्रो स्टेशन की बात है?",
        contact_number="9876543210",
    )
    _apply(
        db,
        conversation,
        text="यह वनास पुणे मेट्रो स्टेशन की बात है।",
        next_field="description",
        reply="ठीक है, वनाज़ स्टेशन। वहाँ क्या समस्या हुई?",
        station="वनास",
    )
    confirmation = _apply(
        db,
        conversation,
        text="वहाँ कुत्ते बहुत बढ़ गए हैं।",
        next_field="confirmation",
        reply=(
            "मनन जैन, नंबर 9876543210, वनाज़ स्टेशन—वहाँ आवारा कुत्तों की "
            "संख्या बहुत बढ़ गई है। क्या मैं यह शिकायत दर्ज कर दूँ?"
        ),
        description="आवारा कुत्तों की संख्या बहुत बढ़ गई है",
    )
    assert conversation.complaint_collection_station == "Vanaz"
    assert conversation.complaint_collection_description == (
        "आवारा कुत्तों की संख्या बहुत बढ़ गई है"
    )
    # The deterministic fallback read-back uses canonical data if the model
    # transliterates a stored station differently in its proposed reply.
    assert "कुत्तों" in confirmation.reply_text
    assert confirmation.state_after == "confirming"

    status = _apply(
        db,
        conversation,
        text="कंप्लेंट दर्ज की आपने मेरी?",
        # Even if the model confuses this question with authorization, the
        # deterministic reducer must treat it as status-only.
        intent="confirm",
        next_field="confirmation",
        reply="क्या शिकायत दर्ज हुई है?",
    )
    assert "अभी दर्ज नहीं हुई" in status.reply_text
    assert conversation.complaint_collection_state == "confirming"
    assert db.scalar(select(func.count()).select_from(CategoryLog)) == 0

    completed = _apply(
        db,
        conversation,
        text="दर्ज करिए। दर्ज करिए।",
        # Explicit authorization is also recognized if a small model labels it
        # as a generic field turn.
        intent="provide_fields",
        next_field="none",
        reply="कृपया शिकायत दर्ज करें।",
    )
    assert completed.completed is True
    assert completed.tracking_id and completed.tracking_id.startswith("PMC-")
    assert conversation.complaint_collection_state is None
    assert db.scalar(select(func.count()).select_from(CategoryLog)) == 1
    assert db.scalar(select(func.count()).select_from(ComplaintTracking)) == 1


def test_non_name_is_rejected_and_gets_clear_human_repair(db: Session) -> None:
    conversation = _conversation(db)
    _apply(
        db,
        conversation,
        text="I need to make a complaint",
        intent="start_complaint",
        category="complaint",
        next_field="name",
        reply="What is your full name?",
    )

    result = _apply(
        db,
        conversation,
        text="The washroom is dirty and the lift is broken",
        next_field="name",
        reply="I heard the issue. What is your full name?",
        full_name="The washroom is dirty and the lift is broken",
    )

    assert conversation.complaint_collection_full_name is None
    assert result.validation_errors == ("invalid_name",)
    assert "नाम समझ नहीं पाई" in result.reply_text


def test_non_name_is_not_inferred_from_raw_text_when_model_extracts_no_name(
    db: Session,
) -> None:
    conversation = _conversation(db)
    conversation.pending_category = "complaint"
    conversation.preferred_language = "english"
    conversation.complaint_collection_state = "collecting_name"

    result = apply_voice_decision(
        decision=VoiceTurnDecision(
            intent="provide_fields",
            language="english",
            fields=VoiceFieldUpdates(full_name=None),
            next_field="name",
            reply_text="Could you repeat your name?",
            confidence=0.95,
        ),
        text="Security guard behaved rudely",
        conversation=conversation,
        db=db,
        provider="test",
        model="test-controller",
        controller_latency_ms=1,
    )

    assert conversation.complaint_collection_full_name is None
    assert conversation.complaint_collection_state == "collecting_name"
    assert result.validation_errors == ("missing_name",)
    assert "couldn't catch your name" in result.reply_text.casefold()


def test_related_fare_diversion_uses_exact_tool_and_keeps_collection_state(
    db: Session,
) -> None:
    conversation = _conversation(db)
    conversation.pending_category = "complaint"
    conversation.preferred_language = "english"
    conversation.complaint_collection_state = "collecting_name"

    decision = VoiceTurnDecision(
        intent="fare_enquiry",
        language="english",
        origin_station="PCMC",
        destination_station="Swargate",
        next_field="name",
        reply_text="The fare is ₹999. Now, what is your name?",
    )
    result = apply_voice_decision(
        decision=decision,
        text="Before that, what is the fare from PCMC to Swargate?",
        conversation=conversation,
        db=db,
        provider="test",
        model="test",
        controller_latency_ms=1,
    )

    assert "₹30" in result.reply_text
    assert "₹999" not in result.reply_text
    assert conversation.complaint_collection_state == "collecting_name"
