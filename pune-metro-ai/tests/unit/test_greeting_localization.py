"""Unit tests for localized greeting replies."""

import pytest

from app.services.llm_client import build_greeting_reply


@pytest.mark.parametrize(
    ("message_text", "language", "script", "expected_reply"),
    [
        ("hi", "english", "latin", "Hi! How can I help you today?"),
        ("hello", "english", "latin", "Hello! How can I help you today?"),
        ("hey", "english", "latin", "Hey there! How can I help you?"),
        ("good morning", "english", "latin", "Good morning! How can I help you today?"),
        ("namaste", "english", "latin", "Namaste! How can I help you?"),
        ("namaskar", "english", "latin", "Namaskar! How can I help you?"),
        ("hi", "hindi", "devanagari", "नमस्ते! मैं आपकी कैसे मदद कर सकता हूँ?"),
        ("namaste", "hindi", "devanagari", "नमस्ते! मैं आपकी कैसे मदद कर सकता हूँ?"),
        ("namaskar", "hindi", "devanagari", "नमस्कार! मैं आपकी कैसे मदद कर सकता हूँ?"),
        ("suprabhat", "hindi", "devanagari", "नमस्ते! मैं पुणे मेट्रो के बारे में आपकी क्या मदद कर सकता हूँ?"),
        ("hi", "marathi", "devanagari", "नमस्कार! मी तुमची कशी मदत करू शकतो?"),
        ("namaste", "marathi", "devanagari", "नमस्कार! मी तुमची कशी मदत करू शकतो?"),
        ("namaskar", "marathi", "devanagari", "नमस्कार! मी तुमची कशी मदत करू शकतो?"),
        ("shubh sakal", "marathi", "devanagari", "नमस्कार! मी पुणे मेट्रोबद्दल तुमची काय मदत करू शकतो?"),
        ("hola", "english", "latin", "Hello! How can I help you with Pune Metro today?"),
    ],
)
def test_build_greeting_reply(
    message_text: str, language: str, script: str, expected_reply: str
) -> None:
    """Verify that build_greeting_reply returns the correct localized greeting."""
    assert build_greeting_reply(message_text, language, script) == expected_reply