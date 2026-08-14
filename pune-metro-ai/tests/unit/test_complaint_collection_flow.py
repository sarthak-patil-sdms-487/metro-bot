"""Unit tests for the complaint and suggestion collection flow."""

import pytest
from unittest.mock import AsyncMock, patch
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import CategoryLog, ComplaintTracking, Conversation, User
from app.api.whatsapp_webhook import receive_webhook


@pytest.mark.asyncio
async def test_complaint_collection_happy_path(db: Session) -> None:
    """Verify the full complaint collection flow from start to finish."""
    user = User(whatsapp_number="1234567890")
    db.add(user)
    db.flush()
    conversation = Conversation(user_id=user.id, pending_category="complaint")
    db.add(conversation)
    db.commit()

    with patch("app.api.whatsapp_webhook.whatsapp_client", new_callable=AsyncMock) as mock_whatsapp_client:
        with patch("app.api.whatsapp_webhook.classify_message") as mock_classify:
            mock_classify.return_value = {
                "intent": "direct_query",
                "classification_confident": True,
                "categories": ["complaint"],
                "subcategories": [],
                "detected_language": "english",
                "extracted_details": {
                    "metro_station": None,
                    "ticket_number": None,
                    "payment_method": None,
                    "passenger_name": None,
                },
            }
            
            # 1. Initial message to trigger the flow
            payload = _get_whatsapp_payload("I have a complaint")
            await receive_webhook(payload, db)
            assert conversation.complaint_collection_state == "collecting_name"
            assert "name" in mock_whatsapp_client.send_text_message.call_args.kwargs["body"].casefold()

            # 2. User provides name
            payload = _get_whatsapp_payload("John Doe")
            await receive_webhook(payload, db)
            assert conversation.complaint_collection_full_name == "John Doe"
            assert conversation.complaint_collection_state == "collecting_contact"

            # 3. User provides contact number
            payload = _get_whatsapp_payload("0987654321")
            await receive_webhook(payload, db)
            assert conversation.complaint_collection_contact_number == "0987654321"
            assert conversation.complaint_collection_state == "collecting_station"

            # 4. User provides station
            payload = _get_whatsapp_payload("PCMC")
            await receive_webhook(payload, db)
            assert conversation.complaint_collection_station == "PCMC"
            assert conversation.complaint_collection_state == "collecting_description"

            # 5. User provides description
            payload = _get_whatsapp_payload("The escalator was not working.")
            await receive_webhook(payload, db)
            assert conversation.complaint_collection_state == "confirming"
            summary = mock_whatsapp_client.send_text_message.call_args.kwargs["body"]
            assert all(value in summary for value in ("John Doe", "PCMC", "escalator")), summary
            assert "0987654321" in summary.replace(" ", "")

            # 6. User confirms
            payload = _get_whatsapp_payload("yes")
            await receive_webhook(payload, db)
            assert conversation.complaint_collection_state is None
            assert db.scalar(select(func.count()).select_from(CategoryLog)) == 1
            assert db.scalar(select(func.count()).select_from(ComplaintTracking)) == 1
            assert "PMC-" in mock_whatsapp_client.send_text_message.call_args.kwargs["body"]


@pytest.mark.asyncio
async def test_complaint_confirmation_cancel_reply(db: Session) -> None:
    """Verify that an explicit cancellation clears the complaint flow."""
    user = User(whatsapp_number="1234567890")
    db.add(user)
    db.flush()
    conversation = Conversation(
        user_id=user.id,
        complaint_collection_state="confirming",
        complaint_collection_full_name="John Doe",
        complaint_collection_contact_number="0987654321",
        complaint_collection_station="PCMC",
        complaint_collection_description="The escalator was not working.",
        pending_category="complaint",
    )
    db.add(conversation)
    db.commit()

    with patch("app.api.whatsapp_webhook.whatsapp_client", new_callable=AsyncMock) as mock_whatsapp_client:
        payload = _get_whatsapp_payload("cancel it")
        await receive_webhook(payload, db)
        assert "won't register" in mock_whatsapp_client.send_text_message.call_args.kwargs["body"]
        assert conversation.complaint_collection_state is None


@pytest.mark.asyncio
async def test_complaint_collection_does_not_lose_data_on_diversion(db: Session) -> None:
    """A diversion must not silently discard already collected complaint data."""
    user = User(whatsapp_number="1234567890")
    db.add(user)
    db.flush()
    conversation = Conversation(
        user_id=user.id,
        complaint_collection_state="collecting_contact",
        complaint_collection_full_name="John Doe",
        pending_category="complaint",
    )
    db.add(conversation)
    db.commit()

    with patch("app.api.whatsapp_webhook.whatsapp_client", new_callable=AsyncMock) as mock_whatsapp_client:
        with patch("app.api.whatsapp_webhook.classify_message") as mock_classify:
            mock_classify.return_value = {"intent": "direct_query", "classification_confident": True, "categories": ["enquiry"], "detected_language": "english"}
            with patch("app.api.whatsapp_webhook.generate_reply") as mock_generate_reply:
                mock_generate_reply.return_value = "The metro runs from 6 AM to 10 PM."
                payload = _get_whatsapp_payload("What are the metro timings?")
                await receive_webhook(payload, db)
                assert conversation.complaint_collection_state == "collecting_contact"
                assert conversation.complaint_collection_full_name == "John Doe"
                assert "10" in mock_whatsapp_client.send_text_message.call_args.kwargs["body"]


def _get_whatsapp_payload(message_text: str) -> dict:
    """Helper to create a mock WhatsApp webhook payload."""
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": "1234567890",
                                    "id": f"wamid.test.{abs(hash(message_text))}",
                                    "text": {"body": message_text},
                                    "type": "text",
                                }
                            ],
                            "contacts": [{"profile": {"name": "Test User"}, "wa_id": "1234567890"}],
                        }
                    }
                ]
            }
        ]
    }
