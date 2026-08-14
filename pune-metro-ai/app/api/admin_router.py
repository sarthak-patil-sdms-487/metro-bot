import asyncio
import logging
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import AdminUser, CallSession, CategoryLog, ComplaintTracking, Conversation, Message, User, ResponseSourceLog, TTSAudioCache
from app.db.session import get_db
from app.core.config import settings
from app.security.auth import authenticate_admin, create_access_token, get_current_admin_user
from app.services.llm_client import reply_variant_key, resolve_reply_language
from app.services.whatsapp_client import whatsapp_client

router = APIRouter(prefix="/api/v1/admin")
logger = logging.getLogger(__name__)

TICKET_APPROVED_REPLIES = {
    "english": "Your ticket ({token}) has been approved and is being worked on.",
    "hindi": "आपका टिकट ({token}) स्वीकृत हो गया है और इस पर काम किया जा रहा है।",
    "hindi_romanized": "Aapka ticket ({token}) manzoor ho gaya hai aur is par kaam kiya ja raha hai.",
    "marathi": "तुमचे तिकीट ({token}) मंजूर झाले आहे आणि त्यावर काम सुरू आहे.",
    "marathi_romanized": "Tumche ticket ({token}) manjur jhale aahe aani tyavar kaam suru aahe.",
}
TICKET_RESOLVED_REPLIES = {
    "english": "Good news! Your complaint (ticket {token}) has been resolved. Thank you for your patience.",
    "hindi": "खुशखबरी! आपकी शिकायत (टिकट {token}) का समाधान हो गया है। आपके धैर्य के लिए धन्यवाद।",
    "hindi_romanized": "Khushkhabri! Aapki shikayat (ticket {token}) ka samadhan ho gaya hai. Aapke dhairya ke liye dhanyawad.",
    "marathi": "चांगली बातमी! तुमच्या तक्रारीचे (तिकीट {token}) निराकरण झाले आहे. तुमच्या संयमाबद्दल धन्यवाद.",
    "marathi_romanized": "Changli batmi! Tumchya takrariche (ticket {token}) nirakaran jhale aahe. Tumchya saiyambaddal dhanyawad.",
}


@router.post("/auth/login")
def login(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Session = Depends(get_db),
) -> dict[str, str]:
    admin_user = authenticate_admin(form_data.username, form_data.password, db)
    if not admin_user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect username or password")
    access_token = create_access_token(admin_user.username)
    return {"access_token": access_token, "token_type": "bearer"}


@router.get("/stats/overview")
def stats_overview(
    db: Session = Depends(get_db),
    _admin_user: AdminUser = Depends(get_current_admin_user),
) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_week = start_of_today - timedelta(days=7)

    total_users = db.scalar(select(func.count(User.id))) or 0
    total_conversations = db.scalar(select(func.count(Conversation.id))) or 0
    conversations_by_channel = {
        channel: count
        for channel, count in db.execute(
            select(Conversation.channel, func.count(Conversation.id)).group_by(Conversation.channel)
        ).all()
    }
    messages_today = db.scalar(select(func.count(Message.id)).where(Message.created_at >= start_of_today)) or 0
    messages_this_week = db.scalar(select(func.count(Message.id)).where(Message.created_at >= start_of_week)) or 0

    tickets_by_status = {
        status: count
        for status, count in db.execute(
            select(ComplaintTracking.status, func.count(ComplaintTracking.id)).group_by(ComplaintTracking.status)
        ).all()
    }

    category_rows = db.execute(select(CategoryLog.categories, CategoryLog.id)).all()
    categories_by_type: dict[str, int] = {}
    for categories, _ in category_rows:
        for category in categories or []:
            categories_by_type[category] = categories_by_type.get(category, 0) + 1

    message_rows = db.execute(
        select(Message.created_at).where(Message.created_at >= start_of_week).order_by(Message.created_at.asc())
    ).scalars().all()
    daily_counts: dict[str, int] = {}
    for created_at in message_rows:
        if not created_at:
            continue
        key = created_at.astimezone(timezone.utc).strftime("%Y-%m-%d")
        daily_counts[key] = daily_counts.get(key, 0) + 1
    messages_per_day = [
        {"date": date_key, "count": count}
        for date_key, count in sorted(daily_counts.items())
    ]

    resolved_tickets = db.scalar(
        select(func.count(ComplaintTracking.id)).where(ComplaintTracking.status == "resolved")
    ) or 0
    if resolved_tickets == 0:
        avg_tickets_resolved_per_day = 0.0
    else:
        earliest_tracking = db.scalar(select(func.min(ComplaintTracking.created_at)))
        earliest_category_log = db.scalar(select(func.min(CategoryLog.created_at)))
        earliest_history = min(
            timestamp for timestamp in [earliest_tracking, earliest_category_log] if timestamp is not None
        ) if any(
            timestamp is not None for timestamp in [earliest_tracking, earliest_category_log]
        ) else None

        # Use the available history window, capped at seven days, so the average reflects
        # the actual data span instead of always diluting the result with five nonexistent days.
        if earliest_history is None:
            avg_tickets_resolved_per_day = 0.0
        else:
            days_since_earliest_record = max(1, math.ceil((now - earliest_history).total_seconds() / 86400))
            denominator = min(7, days_since_earliest_record)
            avg_tickets_resolved_per_day = round(resolved_tickets / denominator, 2)

    return {
        "total_users": total_users,
        "total_conversations": total_conversations,
        "conversations_by_channel": conversations_by_channel,
        "messages_today": messages_today,
        "messages_this_week": messages_this_week,
        "tickets_by_status": tickets_by_status,
        "categories_by_type": categories_by_type,
        "messages_per_day": messages_per_day,
        "avg_tickets_resolved_per_day": avg_tickets_resolved_per_day,
    }


@router.get("/conversations")
def list_conversations(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    channel: str | None = Query(None, pattern="^(chat|call)$"),
    db: Session = Depends(get_db),
    _admin_user: AdminUser = Depends(get_current_admin_user),
) -> dict[str, object]:
    query = db.query(Conversation)
    if channel:
        query = query.where(Conversation.channel == channel)
    total = query.count()
    conversations = (
        query
        .order_by(Conversation.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    items = []
    for conversation in conversations:
        user = db.get(User, conversation.user_id)
        category_logs = db.scalars(
            select(CategoryLog)
            .where(CategoryLog.conversation_id == conversation.id)
            .order_by(CategoryLog.created_at.asc())
        ).all()
        tracking_rows = db.scalars(
            select(ComplaintTracking)
            .where(ComplaintTracking.conversation_id == conversation.id)
            .order_by(ComplaintTracking.created_at.asc())
        ).all()
        message_count = db.scalar(
            select(func.count(Message.id)).where(Message.conversation_id == conversation.id)
        ) or 0
        call = db.scalar(
            select(CallSession).where(CallSession.conversation_id == conversation.id)
        )
        inferred_language = (
            resolve_reply_language(category_logs[-1].message)[0]
            if category_logs else None
        )
        items.append(
            {
                "id": conversation.id,
                "user_id": conversation.user_id,
                "status": conversation.status,
                "channel": conversation.channel,
                "created_at": conversation.created_at,
                "updated_at": conversation.updated_at,
                "is_closed": conversation.is_closed,
                "feedback_rating": conversation.feedback_rating,
                "feedback_comment": conversation.feedback_comment,
                "preferred_language": (
                    conversation.preferred_language or inferred_language
                ),
                "message_count": message_count,
                "categories": sorted(
                    {category for log in category_logs for category in (log.categories or [])}
                ),
                "category_log_ids": [log.id for log in category_logs],
                "tracking_ids": [item.token for item in tracking_rows],
                "detected_languages": call.detected_languages if call else [],
                "user": {
                    "id": user.id if user else None,
                    "whatsapp_number": user.whatsapp_number if user else None,
                    "name": user.name if user else None,
                    "created_at": user.created_at if user else None,
                    "total_conversations": 0,
                },
            }
        )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/conversations/{conversation_id}/messages", response_model=None)
def list_conversation_messages(
    conversation_id: int,
    db: Session = Depends(get_db),
    _admin_user: AdminUser = Depends(get_current_admin_user),
) -> list[Message]:
    return (
        db.query(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
        .all()
    )


@router.get("/category-logs")
def list_category_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    _admin_user: AdminUser = Depends(get_current_admin_user),
) -> dict[str, object]:
    total = db.scalar(select(func.count(CategoryLog.id))) or 0
    logs = (
        db.query(CategoryLog)
        .order_by(CategoryLog.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    items = []
    for log in logs:
        user = db.get(User, log.user_id)
        conversation = db.get(Conversation, log.conversation_id)
        tracking = db.scalar(
            select(ComplaintTracking).where(ComplaintTracking.category_log_id == log.id)
        )
        items.append(
            {
                "id": log.id,
                "conversation_id": log.conversation_id,
                "user": {
                    "id": user.id if user else None,
                    "whatsapp_number": user.whatsapp_number if user else None,
                    "name": user.name if user else None,
                    "created_at": user.created_at if user else None,
                    "total_conversations": 0,
                },
                "categories": log.categories,
                "subcategory": log.subcategory,
                "message": log.message,
                "status": log.status,
                "workflow_status": (
                    tracking.status
                    if tracking
                    else "answered"
                    if "enquiry" in (log.categories or [])
                    else "recorded"
                    if "appreciation" in (log.categories or [])
                    else "classified"
                ),
                "channel": conversation.channel if conversation else "chat",
                "language": (
                    conversation.preferred_language
                    if conversation and conversation.preferred_language
                    else resolve_reply_language(log.message)[0]
                ),
                "tracking_id": tracking.token if tracking else None,
                "created_at": log.created_at,
            }
        )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/users")
def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    _admin_user: AdminUser = Depends(get_current_admin_user),
) -> dict[str, object]:
    total = db.scalar(select(func.count(User.id))) or 0
    users = (
        db.query(User)
        .order_by(User.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    result = []
    for user in users:
        total_conversations = db.scalar(
            select(func.count(Conversation.id)).where(Conversation.user_id == user.id)
        ) or 0
        result.append(
            {
                "id": user.id,
                "whatsapp_number": user.whatsapp_number,
                "name": user.name,
                "created_at": user.created_at,
                "total_conversations": total_conversations,
            }
        )
    return {"items": result, "total": total, "page": page, "page_size": page_size}


@router.get("/tickets")
def list_tickets(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    channel: str | None = Query(None, pattern="^(chat|call)$"),
    db: Session = Depends(get_db),
    _admin_user: AdminUser = Depends(get_current_admin_user),
) -> dict[str, object]:
    query = db.query(ComplaintTracking).join(
        Conversation, Conversation.id == ComplaintTracking.conversation_id
    )
    if channel:
        query = query.where(Conversation.channel == channel)
    total = query.count()
    tickets = (
        query
        .order_by(ComplaintTracking.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    items = []
    for ticket in tickets:
        category_log = db.get(CategoryLog, ticket.category_log_id)
        user = db.get(User, ticket.user_id)
        conversation = db.get(Conversation, ticket.conversation_id)
        
        message_details = {}
        if category_log and category_log.message:
            for line in category_log.message.split('\n'):
                if ':' in line:
                    key, value = line.split(':', 1)
                    message_details[key.strip().lower()] = value.strip()

        items.append(
            {
                "id": ticket.id,
                "tracking_id": ticket.token,
                "category_log_id": ticket.category_log_id,
                "conversation_id": ticket.conversation_id,
                "user": {
                    "id": user.id if user else None,
                    "whatsapp_number": user.whatsapp_number if user else None,
                    "name": user.name if user else None,
                    "created_at": user.created_at if user else None,
                    "total_conversations": 0,
                },
                "categories": category_log.categories if category_log else [],
                "subcategory": category_log.subcategory if category_log else None,
                "message": message_details,
                "status": ticket.status,
                "channel": conversation.channel if conversation else "chat",
                "language": conversation.preferred_language if conversation else None,
                "created_at": ticket.created_at,
            }
        )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/call-sessions")
def list_call_sessions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: str | None = Query(None, alias="status"),
    db: Session = Depends(get_db),
    _admin_user: AdminUser = Depends(get_current_admin_user),
) -> dict[str, object]:
    query = db.query(CallSession)
    if status_filter:
        query = query.where(CallSession.status == status_filter)
    total = query.count()
    sessions = (
        query.order_by(CallSession.started_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "items": [
            {
                "id": item.id,
                "conversation_id": item.conversation_id,
                "user_id": item.user_id,
                "provider_call_id": item.provider_call_id,
                "status": item.status,
                "direction": item.direction,
                "started_at": item.started_at,
                "answered_at": item.answered_at,
                "ended_at": item.ended_at,
                "end_reason": item.end_reason,
                "detected_languages": item.detected_languages,
                "user": {
                    "id": user.id if (user := db.get(User, item.user_id)) else None,
                    "whatsapp_number": user.whatsapp_number if user else None,
                    "name": user.name if user else None,
                },
                "transcript_count": db.scalar(
                    select(func.count(Message.id)).where(
                        Message.conversation_id == item.conversation_id
                    )
                ) or 0,
                "duration_seconds": (
                    int((item.ended_at - item.answered_at).total_seconds())
                    if item.answered_at and item.ended_at else None
                ),
                "recording_available": bool(
                    (item.provider_metadata or {}).get("recording_file")
                ),
                "recording_url": (
                    f"/call-sessions/{item.id}/recording"
                    if (item.provider_metadata or {}).get("recording_file") else None
                ),
            }
            for item in sessions
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/call-sessions/{call_session_id}/recording")
def get_call_recording(
    call_session_id: int,
    db: Session = Depends(get_db),
    _admin_user: AdminUser = Depends(get_current_admin_user),
) -> FileResponse:
    call = db.get(CallSession, call_session_id)
    if not call:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Call not found")
    filename = (call.provider_metadata or {}).get("recording_file")
    if not filename or Path(filename).name != filename:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recording not available")
    path = Path(settings.CALL_RECORDINGS_DIR) / filename
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recording file not found")
    return FileResponse(path, media_type="audio/wav", filename=filename)


@router.patch("/tickets/{ticket_id}")
async def update_ticket_status(
    ticket_id: int,
    payload: dict[str, str],
    db: Session = Depends(get_db),
    _admin_user: AdminUser = Depends(get_current_admin_user),
) -> dict[str, object]:
    ticket = db.get(ComplaintTracking, ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    
    old_status = ticket.status
    new_status = payload.get("status")
    if new_status not in {"pending", "approved", "resolved", "rejected"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid status")
    
    if old_status != new_status:
        ticket.status = new_status
        db.commit()
        db.refresh(ticket)

        user = db.get(User, ticket.user_id)
        conversation = db.get(Conversation, ticket.conversation_id)
        if user and conversation:
            language = conversation.preferred_language or "english"
            script = "latin"  # Assuming Latin script for notifications
            reply_key = reply_variant_key(language, script)
            
            message_templates = None
            if new_status == "approved":
                message_templates = TICKET_APPROVED_REPLIES
            elif new_status == "resolved":
                message_templates = TICKET_RESOLVED_REPLIES

            if message_templates:
                message_text = message_templates.get(reply_key, message_templates["english"]).format(token=ticket.token)
                try:
                    await whatsapp_client.send_text_message(to=user.whatsapp_number, body=message_text)
                except Exception as e:
                    logger.error(f"Failed to send WhatsApp notification for ticket {ticket.id}: {e}")

    category_log = db.get(CategoryLog, ticket.category_log_id)
    user = db.get(User, ticket.user_id)
    conversation = db.get(Conversation, ticket.conversation_id)
    message_details = {}
    if category_log and category_log.message:
        for line in category_log.message.split("\n"):
            if ":" in line:
                key, value = line.split(":", 1)
                message_details[key.strip().lower()] = value.strip()
    return {
        "id": ticket.id,
        "tracking_id": ticket.token,
        "category_log_id": ticket.category_log_id,
        "conversation_id": ticket.conversation_id,
        "user": {
            "id": user.id if user else None,
            "whatsapp_number": user.whatsapp_number if user else None,
            "name": user.name if user else None,
            "created_at": user.created_at if user else None,
            "total_conversations": 0,
        },
        "categories": category_log.categories if category_log else [],
        "subcategory": category_log.subcategory if category_log else None,
        "message": message_details,
        "channel": conversation.channel if conversation else "chat",
        "language": conversation.preferred_language if conversation else None,
        "status": ticket.status,
        "created_at": ticket.created_at,
    }


@router.get("/response-source-stats")
def get_response_source_stats(
    db: Session = Depends(get_db),
    _admin_user: AdminUser = Depends(get_current_admin_user),
) -> dict[str, object]:
    """Return statistics on response sources (cache vs. LLM)."""
    now = datetime.now(timezone.utc)
    start_of_week = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=7)

    total_cache = db.scalar(select(func.count(ResponseSourceLog.id)).where(ResponseSourceLog.source == "cache")) or 0
    total_llm = db.scalar(select(func.count(ResponseSourceLog.id)).where(ResponseSourceLog.source == "llm")) or 0

    daily_rows = db.execute(
        select(ResponseSourceLog.created_at, ResponseSourceLog.source)
        .where(ResponseSourceLog.created_at >= start_of_week)
        .order_by(ResponseSourceLog.created_at.asc())
    ).all()
    
    daily_counts: dict[str, dict[str, int]] = {}
    for created_at, source in daily_rows:
        if not created_at:
            continue
        key = created_at.astimezone(timezone.utc).strftime("%Y-%m-%d")
        if key not in daily_counts:
            daily_counts[key] = {"cache": 0, "llm": 0}
        daily_counts[key][source] += 1
        
    daily_stats = [
        {"date": date_key, **counts}
        for date_key, counts in sorted(daily_counts.items())
    ]

    return {
        "total_cache": total_cache,
        "total_llm": total_llm,
        "daily_stats": daily_stats,
    }


@router.get("/cost-audit")
def get_cost_audit(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    channel: str | None = Query(None, pattern="^(chat|call)$"),
    source: str | None = Query(None),
    conversation_id: int | None = Query(None, ge=1),
    db: Session = Depends(get_db),
    _admin_user: AdminUser = Depends(get_current_admin_user),
) -> dict[str, object]:
    """Return one expandable audit record per complete conversation."""
    query = db.query(ResponseSourceLog.conversation_id).where(
        ResponseSourceLog.conversation_id.is_not(None)
    )
    if channel:
        query = query.where(ResponseSourceLog.channel == channel)
    if source:
        query = query.where(ResponseSourceLog.source == source)
    if conversation_id:
        query = query.where(ResponseSourceLog.conversation_id == conversation_id)
    conversation_ids = [item[0] for item in query.group_by(
        ResponseSourceLog.conversation_id
    ).order_by(func.max(ResponseSourceLog.created_at).desc()).all()]
    total = len(conversation_ids)
    page_ids = conversation_ids[(page - 1) * page_size:page * page_size]
    all_cost_rows = db.query(ResponseSourceLog)
    if channel:
        all_cost_rows = all_cost_rows.where(ResponseSourceLog.channel == channel)
    summary_rows = all_cost_rows.all()
    actual = sum(row.actual_cost_inr or 0 for row in summary_rows)
    uncached = sum(row.uncached_cost_inr or 0 for row in summary_rows)

    # STT is continuous call audio, so account for it once per call rather than
    # pretending each finalized transcript has an exact provider duration.
    calls = db.scalars(select(CallSession)).all() if channel != "chat" else []
    stt_cost = 0.0
    for call in calls:
        end = call.ended_at or (datetime.now(timezone.utc) if call.answered_at else None)
        if call.answered_at and end:
            stt_cost += max(0, (end - call.answered_at).total_seconds()) / 3600 * settings.SARVAM_STT_INR_PER_HOUR
    actual += stt_cost
    uncached += stt_cost

    # Keep these lifetime TTS figures separate from the conversation filters.
    # They describe the reusable audio library itself, while the cards above
    # describe the currently filtered cost audit rows.
    tts_cache_entries = db.scalar(select(func.count(TTSAudioCache.id))) or 0
    tts_cache_reuses = db.scalar(select(func.coalesce(func.sum(TTSAudioCache.hit_count), 0))) or 0
    tts_saved_cost = sum(
        (row.uncached_cost_inr or 0) - (row.actual_cost_inr or 0)
        for row in db.scalars(
            select(ResponseSourceLog).where(
                ResponseSourceLog.operation == "tts",
                ResponseSourceLog.source == "cache",
            )
        ).all()
    )
    most_reused_tts = db.scalar(
        select(TTSAudioCache)
        .where(TTSAudioCache.hit_count > 0)
        .order_by(TTSAudioCache.hit_count.desc(), TTSAudioCache.last_used_at.desc())
        .limit(1)
    )

    items = []
    for item_id in page_ids:
        conversation = db.get(Conversation, item_id)
        if conversation is None:
            continue
        user = db.get(User, conversation.user_id)
        call = db.scalar(select(CallSession).where(CallSession.conversation_id == item_id))
        events = db.scalars(select(ResponseSourceLog).where(
            ResponseSourceLog.conversation_id == item_id
        ).order_by(ResponseSourceLog.created_at.asc(), ResponseSourceLog.id.asc())).all()
        messages = db.scalars(select(Message).where(
            Message.conversation_id == item_id
        ).order_by(Message.created_at.asc(), Message.id.asc())).all()
        event_actual = sum(event.actual_cost_inr or 0 for event in events)
        event_uncached = sum(event.uncached_cost_inr or 0 for event in events)
        call_stt = 0.0
        if call and call.answered_at:
            end = call.ended_at or datetime.now(timezone.utc)
            call_stt = max(0, (end - call.answered_at).total_seconds()) / 3600 * settings.SARVAM_STT_INR_PER_HOUR
        event_items = [{
            "id": event.id, "operation": event.operation, "source": event.source,
            "question": event.question, "answer": event.answer,
            "provider": event.provider, "model": event.model,
            "input_units": event.input_units, "output_units": event.output_units,
            "actual_cost_inr": event.actual_cost_inr,
            "uncached_cost_inr": event.uncached_cost_inr,
            "saved_cost_inr": round((event.uncached_cost_inr or 0) - (event.actual_cost_inr or 0), 6),
            "created_at": event.created_at,
        } for event in events]
        if call:
            event_items.insert(0, {
                "id": -call.id, "operation": "stt", "source": "stt",
                "question": "Complete call transcription", "answer": None,
                "provider": "sarvam", "model": settings.SARVAM_STT_MODEL,
                "input_units": round(max(0, ((call.ended_at or datetime.now(timezone.utc)) - call.answered_at).total_seconds())) if call.answered_at else 0,
                "output_units": 0, "actual_cost_inr": round(call_stt, 6),
                "uncached_cost_inr": round(call_stt, 6), "saved_cost_inr": 0,
                "created_at": call.started_at,
            })
        items.append({
            "conversation_id": item_id, "call_session_id": call.id if call else None,
            "channel": conversation.channel, "status": call.status if call else conversation.status,
            "user_name": user.name if user else None,
            "user_number": user.whatsapp_number if user else None,
            "created_at": conversation.created_at,
            "ended_at": call.ended_at if call else conversation.updated_at,
            "actual_cost_inr": round(event_actual + call_stt, 6),
            "uncached_cost_inr": round(event_uncached + call_stt, 6),
            "saved_cost_inr": round(event_uncached - event_actual, 6),
            "cache_hits": sum(1 for event in events if event.source == "cache"),
            "llm_calls": sum(1 for event in events if event.source == "llm"),
            "fresh_tts": sum(1 for event in events if event.operation == "tts" and event.source == "tts"),
            "cached_tts": sum(1 for event in events if event.operation == "tts" and event.source == "cache"),
            "messages": [{"id": message.id, "role": message.role, "content": message.content,
                          "created_at": message.created_at} for message in messages],
            "events": event_items,
        })

    return {
        "items": items,
        "total": total, "page": page, "page_size": page_size,
        "summary": {
            "actual_cost_inr": round(actual, 4),
            "uncached_cost_inr": round(uncached, 4),
            "saved_cost_inr": round(uncached - actual, 4),
            "stt_cost_inr": round(stt_cost, 4),
            "cache_hits": sum(1 for row in summary_rows if row.source == "cache"),
            "llm_calls": sum(1 for row in summary_rows if row.source == "llm"),
        },
        "tts_cache": {
            "stored_entries": int(tts_cache_entries),
            "total_reuses": int(tts_cache_reuses),
            "saved_cost_inr": round(tts_saved_cost, 4),
            "most_reused": ({
                "text": most_reused_tts.text,
                "language": most_reused_tts.language,
                "reuse_count": most_reused_tts.hit_count,
            } if most_reused_tts else None),
        },
        "pricing": {
            "llm_input_usd_per_million": settings.LLM_INPUT_USD_PER_MILLION,
            "llm_output_usd_per_million": settings.LLM_OUTPUT_USD_PER_MILLION,
            "tts_inr_per_10k_chars": settings.SARVAM_TTS_INR_PER_10K_CHARS,
            "stt_inr_per_hour": settings.SARVAM_STT_INR_PER_HOUR,
        },
    }
