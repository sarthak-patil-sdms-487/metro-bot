"""Unit tests for the QA cache service."""

import pytest
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import QACache
from app.services.qa_cache import QACacheService, normalize_question


def test_normalize_question() -> None:
    """Verify that question normalization works as expected."""
    assert normalize_question("What is the fare from Swargate to PCMC?") == "what is the fare from swargate to pcmc"
    assert normalize_question("SNDT से गरवारे कॉलेज तक का किराया क्या है?") == "sndt से गरवारे कॉलेज तक का किराया क्या है"


def test_cache_hit(db: Session) -> None:
    """Verify that a cached answer is returned for an exact match."""
    service = QACacheService(db)
    service.store_answer("What is the fare from Swargate to PCMC?", "The fare is ₹30.", "english", "enquiry")
    
    answer = service.get_cached_answer("What is the fare from Swargate to PCMC?", "english")
    assert answer == "The fare is ₹30."


def test_cache_miss(db: Session) -> None:
    """Verify that None is returned for a question not in the cache."""
    service = QACacheService(db)
    answer = service.get_cached_answer("What is the fare from Vanaz to Ramwadi?", "english")
    assert answer is None


def test_cache_ttl(db: Session) -> None:
    """Verify that a cached answer is not returned if it has expired."""
    service = QACacheService(db)
    service.store_answer("What is the fare from Swargate to PCMC?", "The fare is ₹30.", "english", "enquiry")
    
    # Manually expire the cache entry
    cache_entry = db.scalar(select(QACache))
    cache_entry.last_used_at = datetime.now(timezone.utc) - timedelta(
        hours=settings.QA_CACHE_TTL_HOURS + 1
    )
    db.commit()

    answer = service.get_cached_answer("What is the fare from Swargate to PCMC?", "english")
    assert answer is None
