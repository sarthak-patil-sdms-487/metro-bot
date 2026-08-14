"""Unit tests for the complaint and suggestion collection flow."""

import pytest
from unittest.mock import AsyncMock, patch
from sqlalchemy.orm import Session

from app.db.models import Conversation, User
from app.api.whatsapp_webhook import receive_webhook


@pytest.mark.asyncio
async def test_complaint_collection_happy_path(db: Session) -> None:
    """Verify the full complaint collection flow from start to finish."""
    user = User(whatsapp_number="1234567890")
    conversation = Conversation(user_id=user.id, pending_category="complaint")
    db.add_all([user, conversation])
    db.commit()

    with patch("app.api.whatsapp_webhook.whatsapp_client", new_callable=AsyncMock) as mock_whatsapp_client:
        with patch("app.api.whatsapp_webhook.classify_message") as mock_classify:
            mock_classify.return_value = {"intent": "direct_query", "classification_confident": True, "categories": ["complaint"], "detected_language": "english"}
            
            # 1. Initial message to trigger the flow
            payload = _get_whatsapp_payload("I have a complaint")
            await receive_webhook(payload, db)
            mock_whatsapp_client.send_text_message.assert_called_with(
                to="1234567890",
                body="To register your complaint, I need a few details. First, what is your full name?",
            )

            # 2. User provides name
            conversation.complaint_collection_state = "collecting_name"
            db.commit()
            payload = _get_whatsapp_payload("John Doe")
            await receive_webhook(payload, db)
            mock_whatsapp_client.send_text_message.assert_called_with(
                to="1234567890", body="Got it. What is your contact number?"
            )

            # 3. User provides contact number
            payload = _get_whatsapp_payload("0987654321")
            await receive_webhook(payload, db)
            mock_whatsapp_client.send_text_message.assert_called_with(
                to="1234567890", body="Thanks. Which station or location does this relate to?"
            )

            # 4. User provides station
            payload = _get_whatsapp_payload("PCMC")
            await receive_webhook(payload, db)
            mock_whatsapp_client.send_text_message.assert_called_with(
                to="1234567890", body="Thank you. Please describe what happened."
            )

            # 5. User provides description
            payload = _get_whatsapp_payload("The escalator was not working.")
            await receive_webhook(payload, db)
            mock_whatsapp_client.send_text_message.assert_called_with(
                to="1234567890",
                body="Here's what I have:\nName: John Doe\nContact: 0987654321\nStation: PCMC\nDescription: The escalator was not working.\n\nDo you want me to register this complaint? (yes/no)",
            )

            # 6. User confirms
            with patch("app.api.whatsapp_webhook.create_complaint_tracking") as mock_create_complaint:
                mock_create_complaint.return_value.token = "test-token"
                payload = _get_whatsapp_payload("yes")
                await receive_webhook(payload, db)
                mock_whatsapp_client.send_text_message.assert_called()
                assert "Your complaint has been registered" in mock_whatsapp_client.send_text_message.call_args[1]["body"]


@pytest.mark.asyncio
async def test_complaint_confirmation_no_reply(db: Session) -> None:
    """Verify that a 'no' reply cancels the complaint flow."""
    user = User(whatsapp_number="1234567890")
    conversation = Conversation(
        user_id=user.id,
        complaint_collection_state="confirming",
        complaint_collection_full_name="John Doe",
        complaint_collection_contact_number="0987654321",
        complaint_collection_station="PCMC",
        complaint_collection_description="The escalator was not working.",
        pending_category="complaint",
    )
    db.add_all([user, conversation])
    db.commit()

    with patch("app.api.whatsapp_webhook.whatsapp_client", new_callable=AsyncMock) as mock_whatsapp_client:
        payload = _get_whatsapp_payload("no")
        await receive_webhook(payload, db)
        mock_whatsapp_client.send_text_message.assert_called_with(
            to="1234567890", body="Okay, I've cancelled the process. How else can I help you?"
        )
        assert conversation.complaint_collection_state is None


@pytest.mark.asyncio
async def test_complaint_collection_abandonment(db: Session) -> None:
    """Verify that an unrelated question exits the complaint flow."""
    user = User(whatsapp_number="1234567890")
    conversation = Conversation(
        user_id=user.id,
        complaint_collection_state="collecting_contact",
        complaint_collection_full_name="John Doe",
        pending_category="complaint",
    )
    db.add_all([user, conversation])
    db.commit()

    with patch("app.api.whatsapp_webhook.whatsapp_client", new_callable=AsyncMock) as mock_whatsapp_client:
        with patch("app.api.whatsapp_webhook.classify_message") as mock_classify:
            mock_classify.return_value = {"intent": "direct_query", "classification_confident": True, "categories": ["enquiry"], "detected_language": "english"}
            with patch("app.api.whatsapp_webhook.generate_reply") as mock_generate_reply:
                mock_generate_reply.return_value = "The metro runs from 6 AM to 10 PM."
                payload = _get_whatsapp_payload("What are the metro timings?")
                await receive_webhook(payload, db)
                assert conversation.complaint_collection_state is None
                # Assert that a normal reply is sent, not a complaint collection prompt
                assert "timings" in mock_whatsapp_client.send_text_message.call_args[1]["body"].lower()


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
                                    "id": "wamid.test",
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