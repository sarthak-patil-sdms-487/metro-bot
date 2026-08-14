"""Complaint/suggestion write tools backed by the existing tracking service."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import CategoryLog, ComplaintTracking, TicketDetails
from app.services.complaint_tracking import create_complaint_tracking
from app.services.llm_client import resolve_station_alias


def _existing_action(
    *, conversation_id: int, category: str, message: str, db: Session
) -> ComplaintTracking | None:
    return db.scalar(
        select(ComplaintTracking)
        .join(CategoryLog, CategoryLog.id == ComplaintTracking.category_log_id)
        .where(
            ComplaintTracking.conversation_id == conversation_id,
            ComplaintTracking.category == category,
            CategoryLog.message == message,
        )
        .order_by(ComplaintTracking.id.desc())
        .limit(1)
    )


def log_complaint(
    *,
    user_id: int,
    conversation_id: int,
    full_name: str,
    contact_number: str,
    station: str,
    description: str,
    db: Session,
    subcategory: str | None = None,
) -> dict[str, Any]:
    contact = contact_number.strip()
    if not contact.isdigit() or not 7 <= len(contact) <= 15:
        return {"created": False, "error": "invalid_contact_number"}
    canonical_station = resolve_station_alias(station)
    if canonical_station is None:
        return {"created": False, "error": "station_not_found", "station": station}
    if len(description.strip()) < 18:
        return {"created": False, "error": "description_too_short"}

    message = (
        f"Name: {full_name.strip()}\nContact: {contact}\nStation: {canonical_station}\n"
        f"Description: {description.strip()}"
    )
    if existing := _existing_action(
        conversation_id=conversation_id, category="complaint", message=message, db=db
    ):
        return {"created": False, "idempotent": True, "tracking_id": existing.token}

    category_log = CategoryLog(
        user_id=user_id,
        conversation_id=conversation_id,
        categories=["complaint"],
        subcategory=subcategory,
        message=message,
    )
    db.add(category_log)
    db.flush()
    db.add(
        TicketDetails(
            category_log_id=category_log.id,
            metro_station=canonical_station,
            passenger_name=full_name.strip(),
        )
    )
    tracking = create_complaint_tracking(
        category_log=category_log,
        user_id=user_id,
        conversation_id=conversation_id,
        db=db,
        category="complaint",
    )
    db.commit()
    return {"created": True, "tracking_id": tracking.token, "station": canonical_station}


def log_suggestion(
    *,
    user_id: int,
    conversation_id: int,
    full_name: str,
    description: str,
    db: Session,
    station: str | None = None,
    subcategory: str | None = None,
) -> dict[str, Any]:
    if len(description.strip()) < 18:
        return {"created": False, "error": "description_too_short"}
    canonical_station = resolve_station_alias(station) if station else None
    if station and canonical_station is None:
        return {"created": False, "error": "station_not_found", "station": station}
    message = f"Name: {full_name.strip()}\nDescription: {description.strip()}"
    if canonical_station:
        message += f"\nStation: {canonical_station}"
    if existing := _existing_action(
        conversation_id=conversation_id, category="suggestion", message=message, db=db
    ):
        return {"created": False, "idempotent": True, "tracking_id": existing.token}
    category_log = CategoryLog(
        user_id=user_id,
        conversation_id=conversation_id,
        categories=["suggestion"],
        subcategory=subcategory,
        message=message,
    )
    db.add(category_log)
    db.flush()
    tracking = create_complaint_tracking(
        category_log=category_log,
        user_id=user_id,
        conversation_id=conversation_id,
        db=db,
        category="suggestion",
    )
    db.commit()
    return {"created": True, "tracking_id": tracking.token, "station": canonical_station}
