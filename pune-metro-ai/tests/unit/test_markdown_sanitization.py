import os

os.environ.setdefault("WHATSAPP_VERIFY_TOKEN", "test")
os.environ.setdefault("WHATSAPP_ACCESS_TOKEN", "test")
os.environ.setdefault("WHATSAPP_PHONE_NUMBER_ID", "test")
os.environ.setdefault("PRIMARY_LLM_API_KEY", "test")
os.environ.setdefault("FALLBACK_LLM_API_KEY", "test")
os.environ.setdefault("DATABASE_URL", "sqlite://")

from app.services.llm_client import (
    PROJECT_ROOT,
    _sanitize_outbound_text,
    _strip_markdown,
    load_reference_data,
)


def test_timetable_reference_markdown_is_converted_to_plain_prompt_text() -> None:
    timetable_markdown = (PROJECT_ROOT / "data" / "timetable.md").read_text(encoding="utf-8")

    plain_text = _strip_markdown(timetable_markdown)
    loaded_text = load_reference_data(["timetable"])

    for text in (plain_text, loaded_text):
        assert "#" not in text
        assert "**" not in text
        assert "• Service hours:" in text
        assert "- Service hours:" not in text


def test_outbound_sanitizer_removes_heading_bold_italic_and_bullet_markers() -> None:
    reply = "## Timetable\n\n- **Service hours:** *06:00 AM – 11:00 PM*"

    sanitized = _sanitize_outbound_text(reply)

    assert sanitized == "Timetable\n\n• Service hours: 06:00 AM – 11:00 PM"
    assert "#" not in sanitized
    assert "**" not in sanitized
    assert "*" not in sanitized
