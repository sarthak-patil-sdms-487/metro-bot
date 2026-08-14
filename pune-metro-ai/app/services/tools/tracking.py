"""Complaint and suggestion tracking lookup tool."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ComplaintTracking


def check_tracking(tracking_id: str, db: Session) -> dict[str, Any]:
    normalized = tracking_id.strip().upper()
    tracking = db.scalar(
        select(ComplaintTracking).where(ComplaintTracking.token == normalized)
    )
    if tracking is None:
        return {"found": False, "tracking_id": normalized}
    return {
        "found": True,
        "tracking_id": tracking.token,
        "category": tracking.category,
        "status": tracking.status,
    }
