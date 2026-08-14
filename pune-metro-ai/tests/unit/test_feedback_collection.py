"""Unit tests for the feedback collection flow."""

import pytest
from unittest.mock import AsyncMock, patch
from sqlalchemy.orm import Session

from app.db.models import Conversation, User
from app.api.whatsapp_webhook import receive_webhook


@pytest.mark.asyncio
async def test_feedback_after_ticket_confirmation(db: Session) -> None:
    """Verify that feedback is requested after a ticket is confirmed."""
    user = User(whatsapp_number="1234567890")
    conversation = Conversation(user_id=user.id, complaint_collection_state="confirming")
    db.add_all([user, conversation])
    db.commit()

    with patch("app.api.whatsapp_webhook.whatsapp_client", new_callable=AsyncMock) as mock_whatsapp_client:
        with patch("app.api.whatsapp_webhook.create_complaint_tracking") as mock_create_complaint:
            mock_create_complaint.return_value.token = "test-token"
            payload = _get_whatsapp_payload("yes")
            await receive_webhook(payload, db)
            # The last call should be the feedback request
            mock_whatsapp_client.send_interactive_list.assert_called_once()


@pytest.mark.asyncio
async def test_feedback_after_enquiry(db: Session) -> None:
    """Verify that feedback is requested after an enquiry is answered and the user says thanks."""
    user = User(whatsapp_number="1234567890")
    conversation = Conversation(user_id=user.id)
    db.add_all([user, conversation])
    db.commit()

    with patch("app.api.whatsapp_webhook.whatsapp_client", new_callable=AsyncMock) as mock_whatsapp_client:
        # Simulate an enquiry and then a thank you
        with patch("app.services.llm_client.classify_message") as mock_classify:
            mock_classify.return_value = {"intent": "direct_query", "classification_confident": True, "categories": ["enquiry"], "detected_language": "english"}
            payload = _get_whatsapp_payload("What time is the last metro?")
            await receive_webhook(payload, db)

            mock_classify.return_value = {"intent": "acknowledgment", "classification_confident": True}
            payload = _get_whatsapp_payload("Thanks!")
            await receive_webhook(payload, db)
            mock_whatsapp_client.send_interactive_list.assert_called_once()


@pytest.mark.asyncio
async def test_no_duplicate_feedback_prompts(db: Session) -> None:
    """Verify that feedback is only requested once per conversation."""
    user = User(whatsapp_number="1234567890")
    conversation = Conversation(user_id=user.id, is_closed=True)
    db.add_all([user, conversation])
    db.commit()

    with patch("app.api.whatsapp_webhook.whatsapp_client", new_callable=AsyncMock) as mock_whatsapp_client:
        with patch("app.services.llm_client.classify_message") as mock_classify:
            mock_classify.return_value = {"intent": "acknowledgment", "classification_confident": True}
            payload = _get_whatsapp_payload("Thanks!")
            await receive_webhook(payload, db)
            mock_whatsapp_client.send_interactive_list.assert_not_called()


@pytest.mark.asyncio
async def test_feedback_rating_persists(db: Session) -> None:
    """Verify that the feedback rating is persisted to the database."""
    user = User(whatsapp_number="1234567890")
    conversation = Conversation(user_id=user.id)
    db.add_all([user, conversation])
    db.commit()

    with patch("app.api.whatsapp_webhook.whatsapp_client", new_callable=AsyncMock):
        payload = _get_whatsapp_payload("feedback:5")
        payload["entry"][0]["changes"][0]["value"]["messages"][0]["type"] = "interactive"
        payload["entry"][0]["changes"][0]["value"]["messages"][0]["interactive"] = {"list_reply": {"id": "feedback:5", "title": "5 ★"}}
        await receive_webhook(payload, db)
        
        db.refresh(conversation)
        assert conversation.feedback_rating == 5


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