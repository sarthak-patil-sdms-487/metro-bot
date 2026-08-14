"""One place for conservative provider-cost estimates and audit rows."""

from app.core.config import settings
from app.db.models import ResponseSourceLog


def estimated_tokens(text: str) -> int:
    # Provider usage is authoritative when exposed. This approximation is clearly
    # identified in metadata and works for existing provider adapters.
    return max(1, (len(text) + 3) // 4)


def llm_cost_inr(input_tokens: int, output_tokens: int) -> float:
    usd = (
        input_tokens * settings.LLM_INPUT_USD_PER_MILLION
        + output_tokens * settings.LLM_OUTPUT_USD_PER_MILLION
    ) / 1_000_000
    return round(usd * settings.USD_TO_INR, 6)


def tts_cost_inr(characters: int) -> float:
    return round(characters * settings.SARVAM_TTS_INR_PER_10K_CHARS / 10_000, 6)


def make_llm_log(*, conversation_id: int, channel: str, question: str, answer: str,
                 call_session_id: int | None = None, provider: str = "openrouter",
                 model: str | None = None,
                 metadata: dict | None = None) -> ResponseSourceLog:
    input_units = estimated_tokens(question)
    output_units = estimated_tokens(answer)
    cost = llm_cost_inr(input_units, output_units)
    metadata_json = {"usage_kind": "estimated"}
    if metadata:
        metadata_json.update(metadata)
    return ResponseSourceLog(
        source="llm", operation="llm", conversation_id=conversation_id,
        call_session_id=call_session_id, channel=channel, question=question,
        answer=answer, provider=provider, model=model or settings.PRIMARY_LLM_MODEL,
        input_units=input_units, output_units=output_units, actual_cost_inr=cost,
        uncached_cost_inr=cost, metadata_json=metadata_json,
    )


def make_cache_log(*, conversation_id: int, channel: str, question: str, answer: str,
                   cache_entry_id: int, call_session_id: int | None = None) -> ResponseSourceLog:
    avoided = llm_cost_inr(estimated_tokens(question), estimated_tokens(answer))
    return ResponseSourceLog(
        source="cache", operation="llm", conversation_id=conversation_id,
        call_session_id=call_session_id, channel=channel, question=question,
        answer=answer, cache_entry_id=cache_entry_id, provider="local-db",
        model=settings.PRIMARY_LLM_MODEL, actual_cost_inr=0.0,
        uncached_cost_inr=avoided,
        metadata_json={"usage_kind": "estimated", "saved_cost_inr": avoided},
    )


def make_tts_log(*, conversation_id: int, call_session_id: int, text: str,
                 source: str, cache_entry_id: int | None = None) -> ResponseSourceLog:
    avoided = tts_cost_inr(len(text))
    actual = 0.0 if source == "cache" else avoided
    return ResponseSourceLog(
        source=source, operation="tts", conversation_id=conversation_id,
        call_session_id=call_session_id, channel="call", question=text,
        cache_entry_id=cache_entry_id, provider="sarvam",
        model=settings.SARVAM_TTS_MODEL, input_units=len(text),
        actual_cost_inr=actual, uncached_cost_inr=avoided,
        metadata_json={"unit": "characters"},
    )
