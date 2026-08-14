"""The single Pune Metro reasoning entry point used by chat and voice."""

import asyncio
import json
from collections.abc import AsyncIterator

import httpx
from sqlalchemy.orm import Session
from typing import Awaitable, Callable

from app.core.config import settings
from app.db.session import SessionLocal
from app.services.brain_prompts import build_system_prompt
from app.services.brain_models import BrainAction, BrainEvent, BrainRequest, BrainResponse
from app.services.knowledge import get_knowledge
from app.services.llm_client import (
    build_route_grounding,
    classify_message,
    detect_language,
    find_station_names,
    generate_reply,
    resolve_reply_language,
)
from app.services.tools import TOOL_SCHEMAS, ToolContext, execute_tool


async def _execute_tool_isolated(
    name: str, arguments: dict, request: BrainRequest
) -> dict:
    """Run synchronous SQLAlchemy work away from the real-time audio loop."""
    def worker() -> dict:
        with SessionLocal() as tool_db:
            return execute_tool(
                name,
                arguments,
                ToolContext(
                    user_id=request.user_id,
                    conversation_id=request.conversation_id,
                    channel=request.channel,
                    session_id=request.session_id,
                    db=tool_db,
                ),
            )

    return await asyncio.to_thread(worker)


async def _tool_calling_response(
    request: BrainRequest, reference_data: str
) -> BrainResponse:
    base_url = settings.PRIMARY_LLM_BASE_URL.rstrip("/")
    url = f"{base_url}/chat/completions"
    system_prompt = build_system_prompt(request.channel)
    if reference_data:
        system_prompt += "\n\nVerified reference material:\n" + reference_data
    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        *[
            {"role": message.role, "content": message.content}
            for message in request.history
            if message.role in {"user", "assistant"}
        ],
        {"role": "user", "content": request.text},
    ]
    actions = []
    headers = {"Authorization": f"Bearer {settings.PRIMARY_LLM_API_KEY}"}
    async with httpx.AsyncClient(timeout=60) as client:
        for _ in range(5):
            response = await client.post(
                url,
                headers=headers,
                json={
                    "model": settings.PRIMARY_LLM_MODEL,
                    "messages": messages,
                    "tools": TOOL_SCHEMAS,
                    "tool_choice": "auto",
                    # Moderate variation makes spoken answers less repetitive;
                    # tools and verified reference data still constrain facts.
                    "temperature": 0.4 if request.channel == "call" else 0.3,
                },
            )
            response.raise_for_status()
            assistant = response.json()["choices"][0]["message"]
            tool_calls = assistant.get("tool_calls") or []
            if not tool_calls:
                language, _ = resolve_reply_language(request.text)
                return BrainResponse(
                    reply_text=(assistant.get("content") or "").strip(),
                    actions=actions,
                    language=request.preferred_language or language,
                )
            messages.append(assistant)
            for tool_call in tool_calls:
                name = tool_call["function"]["name"]
                arguments: dict = {}
                try:
                    arguments = json.loads(tool_call["function"].get("arguments") or "{}")
                    result = await _execute_tool_isolated(name, arguments, request)
                    status = "completed"
                except Exception as exc:
                    result = {"error": type(exc).__name__, "message": str(exc)}
                    status = "failed"
                actions.append(
                    BrainAction(tool=name, status=status, arguments=arguments, result=result)
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": json.dumps(result, ensure_ascii=False, default=str),
                    }
                )
    raise RuntimeError("Shared brain exceeded the tool-call iteration limit")


async def respond(request: BrainRequest, db: Session) -> BrainResponse:
    """Classify, ground, and generate through one channel-neutral path."""
    classification = await classify_message(request.text)
    topics = list(classification.get("reference_topics") or [])
    stations = find_station_names(request.text)
    if stations and "stations" not in topics:
        topics.append("stations")
    fare_context = None
    if len(stations) >= 2:
        fare_context = build_route_grounding(stations[0], stations[1])
    reference_data = get_knowledge(topics)
    if fare_context:
        reference_data = "\n\n".join(
            part for part in (reference_data, fare_context) if part
        )
    if settings.PRIMARY_LLM_API_KEY:
        try:
            response = await _tool_calling_response(request, reference_data)
            return BrainResponse(
                reply_text=response.reply_text,
                actions=response.actions,
                language=response.language,
                categories=list(classification.get("categories") or []),
            )
        except Exception:
            # Preserve the existing chat-grade fallback for providers/models that do
            # not expose OpenAI-compatible tool calling.
            pass
    history = [
        {"role": message.role, "content": message.content}
        for message in request.history
        if message.role in {"user", "assistant"}
    ]
    reply = await generate_reply(
        request.text,
        history,
        reference_data=reference_data,
        fare_context=fare_context,
        preferred_language=request.preferred_language,
        reference_topics=topics,
    )
    return BrainResponse(
        reply_text=reply,
        actions=[],
        language=classification.get("detected_language") or detect_language(request.text),
        categories=list(classification.get("categories") or []),
    )


async def respond_with_legacy_context(
    request: BrainRequest,
    *,
    generator: Callable[..., Awaitable[str]],
    reference_data: str,
    fare_context: str | None,
    complaint_status_context: str | None,
    reference_topics: list[str],
) -> BrainResponse:
    """Compatibility entry point while the chat state machine is retired safely.

    Both adapters now cross the shared-brain boundary. Chat keeps its established
    classification/cache/collection semantics and injectable generator so its public
    behavior and existing tests remain stable during the staged tool migration.
    """
    history = [
        {"role": message.role, "content": message.content}
        for message in request.history
        if message.role in {"user", "assistant"}
    ]
    reply = await generator(
        request.text,
        history,
        reference_data=reference_data,
        fare_context=fare_context,
        complaint_status_context=complaint_status_context,
        preferred_language=request.preferred_language,
        reference_topics=reference_topics,
    )
    language, _ = resolve_reply_language(request.text)
    return BrainResponse(
        reply_text=reply,
        actions=[],
        language=request.preferred_language or language,
    )


async def stream_response(request: BrainRequest, db: Session) -> AsyncIterator[BrainEvent]:
    """Expose a stable event interface; providers may become token-streaming internally."""
    response = await respond(request, db)
    yield BrainEvent(type="text_delta", data={"text": response.reply_text})
    yield BrainEvent(
        type="response_completed",
        data={"language": response.language, "actions": response.actions},
    )
