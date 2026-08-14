"""Public, unauthenticated API endpoints for high-level dashboard stats."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import ComplaintTracking
from app.db.session import get_db
from app.services.llm_client import MATRIX_STATIONS

router = APIRouter(prefix="/api/v1/public")


@router.get("/stats")
def get_public_stats(db: Session = Depends(get_db)) -> dict[str, object]:
    """Return high-level, non-sensitive, anonymized data for a public dashboard."""
    now = datetime.now(timezone.utc)
    start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    start_of_week = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=7)

    total_tickets_resolved_this_month = (
        db.scalar(
            select(func.count(ComplaintTracking.id)).where(
                ComplaintTracking.status == "resolved",
                ComplaintTracking.created_at >= start_of_month,
            )
        )
        or 0
    )

    resolved_ticket_rows = (
        db.execute(
            select(ComplaintTracking.created_at)
            .where(
                ComplaintTracking.status == "resolved",
                ComplaintTracking.created_at >= start_of_week,
            )
            .order_by(ComplaintTracking.created_at.asc())
        )
        .scalars()
        .all()
    )
    daily_resolved_counts: dict[str, int] = {}
    for created_at in resolved_ticket_rows:
        if not created_at:
            continue
        key = created_at.astimezone(timezone.utc).strftime("%Y-%m-%d")
        daily_resolved_counts[key] = daily_resolved_counts.get(key, 0) + 1
    tickets_resolved_per_day = [
        {"date": date_key, "count": count}
        for date_key, count in sorted(daily_resolved_counts.items())
    ]

    return {
        "total_tickets_resolved_this_month": total_tickets_resolved_this_month,
        "supported_languages": ["English", "Hindi", "Marathi"],
        "station_count": len(MATRIX_STATIONS),
        "tickets_resolved_per_day": tickets_resolved_per_day,
    }