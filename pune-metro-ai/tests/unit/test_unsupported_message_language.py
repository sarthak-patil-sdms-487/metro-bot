"""Unit tests for localized replies to unsupported message types."""

import pytest
from unittest.mock import AsyncMock, patch
from sqlalchemy.orm import Session

from app.db.models import Conversation, User
from app.api.whatsapp_webhook import receive_webhook


@pytest.mark.asyncio
async def test_unsupported_message_with_preferred_language(db: Session) -> None:
    """Verify that a user with a preferred language gets the correct unsupported message reply."""
    user = User(whatsapp_number="1234567890", name="Test User")
    conversation = Conversation(user_id=user.id, preferred_language="hindi")
    db.add_all([user, conversation])
    db.commit()

    with patch("app.api.whatsapp_webhook.whatsapp_client", new_callable=AsyncMock) as mock_whatsapp_client:
        payload = _get_whatsapp_payload("audio")
        await receive_webhook(payload, db)
        mock_whatsapp_client.send_text_message.assert_called_once()
        sent_text = mock_whatsapp_client.send_text_message.call_args[1]["body"]
        assert "क्षमा करें" in sent_text
        assert "Sorry" not in sent_text


@pytest.mark.asyncio
async def test_unsupported_message_no_preferred_language(db: Session) -> None:
    """Verify that a user without a preferred language gets the English unsupported message reply."""
    user = User(whatsapp_number="1234567890", name="Test User")
    conversation = Conversation(user_id=user.id)
    db.add_all([user, conversation])
    db.commit()

    with patch("app.api.whatsapp_webhook.whatsapp_client", new_callable=AsyncMock) as mock_whatsapp_client:
        payload = _get_whatsapp_payload("video")
        await receive_webhook(payload, db)
        mock_whatsapp_client.send_text_message.assert_called_once()
        sent_text = mock_whatsapp_client.send_text_message.call_args[1]["body"]
        assert "Sorry" in sent_text
        assert "क्षमा करें" not in sent_text


@pytest.mark.asyncio
async def test_unsupported_message_no_concatenated_reply(db: Session) -> None:
    """Verify that the old concatenated reply is never sent."""
    user = User(whatsapp_number="1234567890", name="Test User")
    conversation = Conversation(user_id=user.id)
    db.add_all([user, conversation])
    db.commit()

    with patch("app.api.whatsapp_webhook.whatsapp_client", new_callable=AsyncMock) as mock_whatsapp_client:
        payload = _get_whatsapp_payload("sticker")
        await receive_webhook(payload, db)
        mock_whatsapp_client.send_text_message.assert_called_once()
        sent_text = mock_whatsapp_client.send_text_message.call_args[1]["body"]
        assert "Sorry, I can only understand text messages right now. Please type your question." not in sent_text
        assert "क्षमस्व, मी सध्या फक्त मजकूर संदेश समजू शकतो. कृपया तुमचा प्रश्न टाइप करा." not in sent_text
        assert "क्षमा करें, मैं अभी केवल टेक्स्ट संदेश समझ सकता हूं। कृपया अपना प्रश्न टाइप करें।" not in sent_text


def _get_whatsapp_payload(message_type: str) -> dict:
    """Helper to create a mock WhatsApp webhook payload for unsupported types."""
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
                                    "type": message_type,
                                }
                            ],
                            "contacts": [{"profile": {"name": "Test User"}, "wa_id": "1234567890"}],
                        }
                    }
                ]
            }
        ]
    }