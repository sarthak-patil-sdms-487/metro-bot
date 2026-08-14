"""Client for the Meta WhatsApp Cloud API."""

import httpx

from app.core.config import settings

WHATSAPP_LIST_TITLE_MAX_LENGTH = 24
WHATSAPP_LIST_HEADER_MAX_LENGTH = 60


def whatsapp_list_title(title: str) -> str:
    """Return a non-empty WhatsApp list-row title within Meta's 24-character limit."""
    normalized = " ".join(title.split())
    if len(normalized) <= WHATSAPP_LIST_TITLE_MAX_LENGTH:
        return normalized
    return f"{normalized[: WHATSAPP_LIST_TITLE_MAX_LENGTH - 3]}..."


def whatsapp_list_header(header: str) -> str:
    """Keep an interactive-list header within Meta's 60-character limit."""
    normalized = " ".join(header.split())
    if len(normalized) <= WHATSAPP_LIST_HEADER_MAX_LENGTH:
        return normalized

    shortened = normalized[: WHATSAPP_LIST_HEADER_MAX_LENGTH - 3].rsplit(" ", 1)[0]
    return f"{shortened or normalized[: WHATSAPP_LIST_HEADER_MAX_LENGTH - 3]}..."


class WhatsAppClient:
    """Send WhatsApp messages through the Meta Cloud API."""

    async def mark_as_read_and_typing(self, message_id: str) -> None:
        """Mark an inbound message read and show Meta's text typing indicator."""
        url = (
            "https://graph.facebook.com/v19.0/"
            f"{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
        )
        headers = {"Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}"}
        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
            "typing_indicator": {"type": "text"},
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()

    async def send_text_message(self, to: str, body: str) -> None:
        """Send a text message to a WhatsApp phone number."""
        url = (
            "https://graph.facebook.com/v19.0/"
            f"{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
        )
        headers = {"Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}"}
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": body},
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()

    async def send_interactive_list(
        self,
        to: str,
        header: str,
        body: str,
        button_text: str,
        sections: list[dict],
    ) -> None:
        """Send a Meta Cloud API interactive list message."""
        url = (
            "https://graph.facebook.com/v19.0/"
            f"{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
        )
        headers = {"Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}"}
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "header": {"type": "text", "text": whatsapp_list_header(header)},
                "body": {"text": body},
                "action": {"button": button_text, "sections": sections},
            },
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()


whatsapp_client = WhatsAppClient()
