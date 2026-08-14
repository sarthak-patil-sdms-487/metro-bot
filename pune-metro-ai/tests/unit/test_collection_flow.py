import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import CategoryLog, Conversation, User
from app.services.collection_flow import (
    advance_collection,
    collection_resume_reply,
    compact_voice_confirmation,
    detect_collection_category,
    parse_contact_number,
    start_collection,
)


def test_detects_all_supported_collection_categories() -> None:
    assert detect_collection_category("I have a complaint about the lift") == "complaint"
    assert detect_collection_category("माझी एक सूचना आहे") == "suggestion"
    assert detect_collection_category("I want to share appreciation for staff") == "appreciation"


def test_parses_spoken_contact_numbers_for_voice() -> None:
    assert parse_contact_number("nine eight seven six five four three two one zero") == "9876543210"
    assert parse_contact_number("नऊ आठ सात सहा पाच चार तीन दोन एक शून्य") == "9876543210"
    assert parse_contact_number("नौ आठ सात छः पांच चार तीन दो एक शून्य") == "9876543210"


def test_compact_voice_confirmation_does_not_repeat_long_description(db: Session) -> None:
    conversation = _collection_conversation(db)
    conversation.preferred_language = "english"
    conversation.complaint_collection_full_name = "Sarthak Patil"
    conversation.complaint_collection_contact_number = "9876543210"
    conversation.complaint_collection_station = "Swargate"
    conversation.complaint_collection_description = "A very long incident description " * 20

    reply = compact_voice_confirmation(conversation)

    assert "Sarthak Patil" in reply
    assert "Swargate" in reply
    assert "very long incident" not in reply
    assert len(reply) < 220


def _collection_conversation(db: Session) -> Conversation:
    user = User(whatsapp_number="919999999999")
    db.add(user)
    db.flush()
    conversation = Conversation(
        user_id=user.id,
        pending_category="complaint",
        preferred_language="english",
        complaint_collection_state="collecting_name",
    )
    db.add(conversation)
    db.flush()
    return conversation


def test_repeats_captured_name_before_moving_to_contact(db: Session) -> None:
    conversation = _collection_conversation(db)

    reply, completed = advance_collection(conversation, "Sarthak Patil", db)

    assert completed is False
    assert conversation.complaint_collection_full_name == "Sarthak Patil"
    assert conversation.complaint_collection_state == "collecting_contact"
    assert "Sarthak Patil" in reply
    assert "10-digit" in reply


def test_invalid_name_repeats_name_question_without_advancing(db: Session) -> None:
    conversation = _collection_conversation(db)

    reply, completed = advance_collection(conversation, "Food stalls", db)

    assert completed is False
    assert conversation.complaint_collection_full_name is None
    assert conversation.complaint_collection_state == "collecting_name"
    assert "full name" in reply


@pytest.mark.parametrize(
    "transcript",
    (
        "माझं नाव आहे.",
        "मेरा नाम है।",
        "My name is",
        "I want to register a complaint",
    ),
)
def test_incomplete_or_non_name_transcript_is_never_stored(
    db: Session, transcript: str
) -> None:
    conversation = _collection_conversation(db)
    conversation.preferred_language = "marathi"

    reply, completed = advance_collection(conversation, transcript, db)

    assert completed is False
    assert conversation.complaint_collection_full_name is None
    assert conversation.complaint_collection_state == "collecting_name"
    assert "नाव" in reply


def test_natural_description_without_keyword_advances_to_confirmation(db: Session) -> None:
    conversation = _collection_conversation(db)
    conversation.complaint_collection_full_name = "Sarthak Patil"
    conversation.complaint_collection_contact_number = "9876543210"
    conversation.complaint_collection_station = "Swargate"
    conversation.complaint_collection_state = "collecting_description"

    reply, completed = advance_collection(
        conversation,
        "The staff member spoke to my family in a way that made us uncomfortable",
        db,
    )

    assert completed is False
    assert conversation.complaint_collection_state == "confirming"
    assert "made us uncomfortable" in conversation.complaint_collection_description
    assert "Sarthak Patil" in reply


@pytest.mark.parametrize(
    ("message", "expected"),
    (
        (
            "I want to register a complaint that the lift has been broken for two days.",
            "the lift has been broken for two days",
        ),
        (
            "मला तक्रार नोंदवायची आहे की शिवाजीनगर स्थानकावर कुत्र्यांची संख्या खूप वाढली आहे.",
            "शिवाजीनगर स्थानकावर कुत्र्यांची संख्या खूप वाढली आहे.",
        ),
        (
            "मेरी शिकायत दर्ज करनी है कि टिकट मशीन पैसे काट रही है लेकिन टिकट नहीं दे रही।",
            "टिकट मशीन पैसे काट रही है लेकिन टिकट नहीं दे रही।",
        ),
        (
            "वहाँ कुत्ते बहुत बढ़ गए हैं।",
            "कुत्ते बहुत बढ़ गए हैं।",
        ),
    ),
)
def test_description_keeps_only_substantive_complaint(
    db: Session, message: str, expected: str
) -> None:
    conversation = _collection_conversation(db)
    conversation.complaint_collection_full_name = "Sarthak Patil"
    conversation.complaint_collection_contact_number = "9876543210"
    conversation.complaint_collection_station = "Shivaji Nagar"
    conversation.complaint_collection_state = "collecting_description"

    _, completed = advance_collection(conversation, message, db)

    assert completed is False
    assert conversation.complaint_collection_state == "confirming"
    assert conversation.complaint_collection_description == expected


def test_natural_hindi_confirmation_registers_without_reasking(db: Session) -> None:
    conversation = _collection_conversation(db)
    conversation.complaint_collection_full_name = "Raju Chauhan"
    conversation.complaint_collection_contact_number = "9453216451"
    conversation.complaint_collection_station = "Bund Garden"
    conversation.complaint_collection_description = "The elevator is not working"
    conversation.complaint_collection_state = "confirming"

    reply, completed = advance_collection(conversation, "नहीं, सब सही है।", db)

    assert completed is True
    assert "PMC-" in reply


def _filled_confirmation(db: Session, language: str = "english") -> Conversation:
    conversation = _collection_conversation(db)
    conversation.preferred_language = language
    conversation.complaint_collection_full_name = "Sarthak Patil"
    conversation.complaint_collection_contact_number = "9876543210"
    conversation.complaint_collection_station = "Swargate"
    conversation.complaint_collection_description = "The washroom is not clean"
    conversation.complaint_collection_state = "confirming"
    return conversation


@pytest.mark.parametrize(
    ("language", "confirmation"),
    (
        ("english", "Yes, you can do it"),
        ("hindi", "हाँ, आप कर सकते हैं"),
        ("hindi", "हां, आगे बढ़िए"),
        ("hindi", "आगे बढ़िए"),
        ("marathi", "हो, तुम्ही करू शकता"),
        ("marathi", "हो, पुढे जावा"),
        ("marathi", "पुढं जा"),
    ),
)
def test_natural_multilingual_approval_registers_once(
    db: Session, language: str, confirmation: str
) -> None:
    conversation = _filled_confirmation(db, language)

    reply, completed = advance_collection(conversation, confirmation, db)

    assert completed is True
    assert "PMC-" in reply
    assert conversation.complaint_collection_state is None


def test_change_request_updates_only_requested_field_then_reconfirms(db: Session) -> None:
    conversation = _filled_confirmation(db)

    reply, completed = advance_collection(conversation, "Yes, I want to change it", db)
    assert completed is False
    assert conversation.complaint_collection_state == "awaiting_correction"
    assert "What would you like to change" in reply

    reply, completed = advance_collection(conversation, "the contact number", db)
    assert completed is False
    assert conversation.complaint_collection_state == "correcting_contact"
    assert "correct 10-digit contact number" in reply

    reply, completed = advance_collection(conversation, "9123456780", db)
    assert completed is False
    assert conversation.complaint_collection_contact_number == "9123456780"
    assert conversation.complaint_collection_full_name == "Sarthak Patil"
    assert conversation.complaint_collection_station == "Swargate"
    assert conversation.complaint_collection_state == "confirming"
    assert "Should I proceed with this information" in reply

    reply, completed = advance_collection(conversation, "yes, you can go ahead", db)
    assert completed is True
    assert "PMC-" in reply


def test_bare_no_enters_change_flow_instead_of_cancelling(db: Session) -> None:
    conversation = _filled_confirmation(db, "marathi")

    reply, completed = advance_collection(conversation, "नाही", db)

    assert completed is False
    assert conversation.complaint_collection_state == "awaiting_correction"
    assert "काय बदलायचं आहे" in reply


def test_explicit_cancel_still_cancels_confirmation(db: Session) -> None:
    conversation = _filled_confirmation(db)

    reply, completed = advance_collection(conversation, "cancel it", db)

    assert completed is True
    assert conversation.complaint_collection_state is None
    assert "won't register" in reply


def test_additional_confirmed_detail_is_appended_without_replacing_complaint(
    db: Session,
) -> None:
    conversation = _filled_confirmation(db, "marathi")
    original = conversation.complaint_collection_description

    reply, completed = advance_collection(
        conversation,
        "मेट्रो स्टेशनवरती साफसफाई पण नाही आहे.",
        db,
    )

    assert completed is False
    assert original in conversation.complaint_collection_description
    assert "साफसफाई" in conversation.complaint_collection_description
    assert conversation.complaint_collection_state == "confirming"
    assert "साफसफाई" in reply


def test_unmarked_background_sentence_does_not_modify_confirmed_complaint(
    db: Session,
) -> None:
    conversation = _filled_confirmation(db, "marathi")
    original = conversation.complaint_collection_description

    reply, completed = advance_collection(
        conversation,
        "फोल्डरमध्ये बाकीचे काम उद्या करूया",
        db,
    )

    assert completed is False
    assert conversation.complaint_collection_description == original
    assert "पुढे जाऊ" in reply


def test_resuming_partial_collection_returns_to_missing_field(db: Session) -> None:
    conversation = _filled_confirmation(db, "english")
    conversation.complaint_collection_contact_number = None
    conversation.complaint_collection_state = "collecting_contact"

    reply = collection_resume_reply(conversation)

    assert "10-digit" in reply
    assert "None" not in reply


def test_another_complaint_request_does_not_discard_unconfirmed_item(db: Session) -> None:
    conversation = _filled_confirmation(db, "marathi")

    reply, completed = advance_collection(
        conversation, "मला अजून एक कंप्लेंट नोंदवायची आहे", db
    )

    assert completed is False
    assert conversation.complaint_collection_state == "confirming"
    assert conversation.complaint_collection_description == "The washroom is not clean"
    assert "आधी सध्याची तक्रार" in reply
    assert "याच कॉलमध्ये" in reply


@pytest.mark.parametrize("message", ("रद्द रद्द", "रद्द म्हटलोय मी", "पुढे नको जाऊ"))
def test_natural_marathi_cancel_variants_leave_call_ready_for_next_item(
    db: Session, message: str
) -> None:
    conversation = _filled_confirmation(db, "marathi")

    _reply, completed = advance_collection(conversation, message, db)

    assert completed is True
    assert conversation.complaint_collection_state is None


def test_two_complaints_in_one_conversation_create_two_clean_records(db: Session) -> None:
    conversation = _filled_confirmation(db)

    first_reply, first_completed = advance_collection(
        conversation, "yes, you can register it", db
    )
    assert first_completed is True
    assert "PMC-" in first_reply

    second_prompt = start_collection(
        conversation,
        "complaint",
        (
            "I have another complaint. My name is Neha Verma. My contact number is "
            "9000000012. This happened at PCMC station. The lift is not working."
        ),
        language="english",
    )
    assert conversation.complaint_collection_state == "confirming"
    assert conversation.complaint_collection_full_name == "Neha Verma"
    assert conversation.complaint_collection_contact_number == "9000000012"
    assert conversation.complaint_collection_station == "PCMC"
    assert "Sarthak Patil" not in second_prompt

    second_reply, second_completed = advance_collection(
        conversation, "yes, proceed", db
    )
    assert second_completed is True
    assert "PMC-" in second_reply
    logs = list(db.scalars(select(CategoryLog).order_by(CategoryLog.id)))
    assert [log.categories for log in logs] == [["complaint"], ["complaint"]]
    assert "Sarthak Patil" in logs[0].message
    assert "Neha Verma" in logs[1].message
