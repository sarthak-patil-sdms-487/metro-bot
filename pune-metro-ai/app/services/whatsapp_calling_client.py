"""In-process Meta WhatsApp Calling/WebRTC lifecycle adapter."""

import asyncio
import logging
from typing import Any

from sqlalchemy import select

from app.core.config import settings
from app.db.models import CallSession, Conversation, User
from app.db.session import SessionLocal


logger = logging.getLogger(__name__)
_session: Any = None
_client: Any = None
_call_tasks: set[asyncio.Task] = set()


def _access_token() -> str:
    return settings.WHATSAPP_CALLING_ACCESS_TOKEN or settings.WHATSAPP_ACCESS_TOKEN


def _phone_number_id() -> str:
    return settings.WHATSAPP_CALLING_PHONE_NUMBER_ID or settings.WHATSAPP_PHONE_NUMBER_ID


async def startup() -> None:
    """Initialize calling only when explicitly enabled and fully configured."""
    global _session, _client
    if not settings.WHATSAPP_CALLING_ENABLED:
        logger.info("WhatsApp Calling disabled; text chat remains available")
        return
    if not (_access_token() and _phone_number_id() and settings.SARVAM_API_KEY):
        logger.warning("WhatsApp Calling disabled: Meta calling or Sarvam credentials missing")
        return
    try:
        import aiohttp
        from app.services.voice_pipeline import preload_voice_pipeline_dependencies

        # Pipecat's native ONNX/Torch imports can deadlock when their first load
        # happens in a worker thread or after the WhatsApp WebRTC transport has
        # partially initialized. Load the complete audio stack first, on the
        # startup thread, then import the WhatsApp client from the warm package.
        preload_voice_pipeline_dependencies()
        from pipecat.transports.whatsapp.client import WhatsAppClient
    except Exception:
        logger.exception("WhatsApp Calling disabled: Pipecat calling dependencies unavailable")
        return
    _session = aiohttp.ClientSession()
    _client = WhatsAppClient(
        whatsapp_token=_access_token(),
        phone_number_id=_phone_number_id(),
        session=_session,
        whatsapp_secret=settings.WHATSAPP_CALLING_APP_SECRET or None,
    )
    logger.info("WhatsApp Calling client ready")


def _track_call_task(task: asyncio.Task[Any], call_session_id: int) -> None:
    """Keep call tasks alive and surface failures that happen before the runner."""
    _call_tasks.add(task)

    def task_done(completed: asyncio.Task[Any]) -> None:
        _call_tasks.discard(completed)
        if completed.cancelled():
            return
        error = completed.exception()
        if error is None:
            return
        logger.error(
            "Voice pipeline task failed for call session %s",
            call_session_id,
            exc_info=(type(error), error, error.__traceback__),
        )
        with SessionLocal() as db:
            call = db.get(CallSession, call_session_id)
            if call is not None:
                call.status = "failed"
                call.end_reason = "pipeline_startup_error"
                db.commit()

    task.add_done_callback(task_done)


async def shutdown() -> None:
    global _session, _client
    for task in tuple(_call_tasks):
        task.cancel()
    if _client is not None:
        try:
            await _client.terminate_all_calls()
        except Exception:
            logger.warning("Failed to terminate active WhatsApp calls", exc_info=True)
    if _session is not None:
        await _session.close()
    _session = None
    _client = None


def is_ready() -> bool:
    return _client is not None


async def terminate_call(call_session_id: int) -> bool:
    """Ask Meta to terminate one active call, identified by our DB session ID."""
    if _client is None:
        logger.warning("Cannot terminate call session %s: calling client is not ready", call_session_id)
        return False
    with SessionLocal() as db:
        call = db.get(CallSession, call_session_id)
        provider_call_id = call.provider_call_id if call else None
    if not provider_call_id:
        logger.warning("Cannot terminate call session %s: provider call ID missing", call_session_id)
        return False
    whatsapp_api = getattr(_client, "_whatsapp_api", None)
    if whatsapp_api is None:
        logger.warning("Cannot terminate call session %s: WhatsApp API unavailable", call_session_id)
        return False
    try:
        await whatsapp_api.terminate_call_to_whatsapp(provider_call_id)
        logger.info("Requested Meta termination for call session %s", call_session_id)
        return True
    except Exception:
        logger.exception("Failed to terminate call session %s through Meta", call_session_id)
        return False


def has_call_event(payload: dict[str, Any]) -> bool:
    return any(
        "calls" in change.get("value", {})
        for entry in payload.get("entry", [])
        for change in entry.get("changes", [])
    )


def _first_call(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            calls = value.get("calls") or []
            if calls:
                return calls[0], value
    return {}, {}


def _ensure_call_records(payload: dict[str, Any]) -> tuple[int, int, int] | None:
    call, value = _first_call(payload)
    provider_call_id = str(call.get("id") or "")
    caller = str(call.get("from") or value.get("contacts", [{}])[0].get("wa_id") or "")
    if not provider_call_id or not caller:
        return None
    with SessionLocal() as db:
        existing = db.scalar(
            select(CallSession).where(CallSession.provider_call_id == provider_call_id)
        )
        if existing:
            return existing.user_id, existing.conversation_id, existing.id
        user = db.scalar(select(User).where(User.whatsapp_number == caller))
        if user is None:
            user = User(whatsapp_number=caller)
            db.add(user)
            db.flush()
        conversation = Conversation(user_id=user.id, channel="call", status="active")
        db.add(conversation)
        db.flush()
        call_session = CallSession(
            conversation_id=conversation.id,
            user_id=user.id,
            provider_call_id=provider_call_id,
            status="connecting",
            provider_metadata={"event": call.get("event"), "direction": "inbound"},
        )
        db.add(call_session)
        db.commit()
        return user.id, conversation.id, call_session.id


async def handle_call_webhook(
    payload: dict[str, Any], raw_body: bytes = b"", signature: str | None = None
) -> None:
    """Pass Meta call signaling to Pipecat and run the voice pipeline in-process."""
    if _client is None:
        logger.warning("Call webhook received while WhatsApp Calling is not ready")
        return
    records = _ensure_call_records(payload)
    if records is None:
        logger.warning("Call webhook had no provider call ID or caller identity")
        return
    user_id, conversation_id, call_session_id = records
    from pipecat.transports.whatsapp.api import WhatsAppWebhookRequest

    async def connection_callback(connection: Any) -> None:
        from app.services.voice_pipeline import run_voice_pipeline

        task = asyncio.create_task(
            run_voice_pipeline(
                connection,
                user_id=user_id,
                conversation_id=conversation_id,
                call_session_id=call_session_id,
            )
        )
        _track_call_task(task, call_session_id)

    request = WhatsAppWebhookRequest(**payload)
    await _client.handle_webhook_request(
        request,
        connection_callback,
        raw_body=raw_body,
        sha256_signature=signature,
    )
