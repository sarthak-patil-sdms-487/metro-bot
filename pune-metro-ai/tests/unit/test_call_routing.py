from app.services.whatsapp_calling_client import has_call_event


def test_call_event_detection_is_separate_from_chat() -> None:
    call_payload = {
        "entry": [{"changes": [{"value": {"calls": [{"id": "call-1"}]}}]}]
    }
    chat_payload = {
        "entry": [{"changes": [{"value": {"messages": [{"id": "message-1"}]}}]}]
    }
    assert has_call_event(call_payload) is True
    assert has_call_event(chat_payload) is False
