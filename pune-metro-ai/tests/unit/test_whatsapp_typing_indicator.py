import pytest

from app.services.whatsapp_client import WhatsAppClient


@pytest.mark.asyncio
async def test_typing_indicator_uses_meta_read_receipt_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posted: list[dict] = []

    class Response:
        def raise_for_status(self) -> None:
            pass

    class AsyncClient:
        async def __aenter__(self) -> "AsyncClient":
            return self

        async def __aexit__(self, *_args: object) -> None:
            pass

        async def post(self, url: str, **kwargs: object) -> Response:
            posted.append({"url": url, **kwargs})
            return Response()

    monkeypatch.setattr(
        "app.services.whatsapp_client.httpx.AsyncClient",
        AsyncClient,
    )

    await WhatsAppClient().mark_as_read_and_typing("wamid.inbound.123")

    assert posted[0]["url"].endswith("/messages")
    assert posted[0]["json"] == {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": "wamid.inbound.123",
        "typing_indicator": {"type": "text"},
    }
