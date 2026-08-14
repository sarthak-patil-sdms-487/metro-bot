"""Chat must never reuse an active voice-call conversation."""

from sqlalchemy.orm import Session

from app.api.whatsapp_webhook import _get_or_create_active_conversation
from app.db.models import Conversation, User
from app.services.llm_client import is_station_or_route_question
from app.services.voice_agent import voice_agent
from app.services.collection_flow import _yes


def test_chat_gets_separate_conversation_from_active_call(db: Session) -> None:
    user = User(whatsapp_number="919999999999", name="Caller")
    db.add(user)
    db.flush()
    call = Conversation(user_id=user.id, channel="call", status="active")
    db.add(call)
    db.commit()

    _, chat = _get_or_create_active_conversation(
        sender=user.whatsapp_number, profile_name=user.name, db=db
    )

    assert chat.id != call.id
    assert chat.channel == "chat"


def test_metro_timing_question_is_not_mistaken_for_station_question() -> None:
    assert not is_station_or_route_question("What are the operating hours of Pune Metro?")
    assert is_station_or_route_question("What is the closest Metro station?")


def test_voice_agent_answers_common_timing_without_llm() -> None:
    reply = voice_agent.common_information_reply("What are Pune Metro operating hours?")
    assert reply == (
        "Pune Metro operates daily from 6 AM to 11 PM on both the Purple and Aqua Lines.",
        "english",
    )


def test_voice_agent_localizes_common_timing() -> None:
    reply = voice_agent.common_information_reply("पुणे मेट्रोची वेळ काय आहे?")
    assert reply is not None
    assert reply[1] == "marathi"
    assert "सकाळी 6" in reply[0]


def test_marathi_proceed_confirmation_is_accepted() -> None:
    assert _yes("हो, पुढे जावा.")


def test_voice_agent_detects_enquiry_switch_during_complaint() -> None:
    assert voice_agent.is_explicit_enquiry("स्वारगेट ते पीसीएमसी तिकीट किती आहे?")
    assert not voice_agent.is_explicit_enquiry("आणि स्वच्छता नाहीये")
