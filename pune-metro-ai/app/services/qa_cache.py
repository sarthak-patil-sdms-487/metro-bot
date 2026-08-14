"""Service for caching question and answer pairs."""

import logging
import re
import unicodedata
from difflib import SequenceMatcher
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import QACache

logger = logging.getLogger(__name__)


def _cache_clock(last_used: datetime) -> tuple[datetime, datetime]:
    """Return comparable UTC timestamps even when a DB driver drops tzinfo."""
    now = datetime.now(timezone.utc)
    comparable_last_used = (
        last_used.replace(tzinfo=timezone.utc)
        if last_used.tzinfo is None
        else last_used.astimezone(timezone.utc)
    )
    return now, comparable_last_used


def is_cacheable_question(text: str) -> bool:
    """Allow answer reuse only for turns that look like information requests.

    This is intentionally channel-neutral. Free-form incidents, appreciation,
    complaints, and suggestions must reach intent classification/state handling
    instead of fuzzy-matching an unrelated FAQ.
    """
    normalized = text.casefold()
    feedback_signal = re.search(
        r"\b(?:complaint|suggestion|appreciation|feedback|problem|issue|trouble|broken|"
        r"not working|stopped working|fight|fighting)\b|"
        r"तक्रार|त्रास|सूचना|कौतुक|अडचण|बिघड|मारामारी|"
        r"शिकायत|परेशानी|सुझाव|प्रशंसा|खराब|काम नहीं",
        normalized,
    )
    if feedback_signal:
        return False
    return bool(re.search(
        r"\?|\b(?:what|when|where|which|who|why|how|fare|route|timing|hours?|"
        r"schedule|price|cost|station|card|ticket)\b|"
        r"काय|कधी|कुठे|कोणते|कसे|किती|भाडे|वेळ|मार्ग|स्थानक|कार्ड|तिकीट|"
        r"क्या|कब|कहाँ|कौन|कैसे|कितना|किराया|समय|रूट|स्टेशन|कार्ड|टिकट",
        normalized,
    ))


def normalize_question(text: str) -> str:
    """Normalize a question for caching by lowercasing, stripping punctuation, and collapsing whitespace."""
    normalized = text.casefold()
    # Unicode combining marks carry Devanagari vowels; ``[^\w]`` silently
    # removed them and could make unrelated Hindi/Marathi questions collide.
    normalized = "".join(
        char for char in normalized
        if char.isspace() or unicodedata.category(char)[0] in {"L", "M", "N"}
    )
    return " ".join(normalized.split())


class QACacheService:
    def __init__(self, db: Session):
        self.db = db

    def get_cached_entry(self, question: str, language: str) -> QACache | None:
        """Get a cached answer for a given question and language."""
        if not is_cacheable_question(question):
            return None
        try:
            normalized_question = normalize_question(question)
            
            # Exact match
            cache_entry = self.db.scalar(
                select(QACache).where(
                    QACache.normalized_question == normalized_question,
                    QACache.language == language,
                )
            )
            if cache_entry:
                now, last_used = _cache_clock(cache_entry.last_used_at)
                if now - last_used > timedelta(hours=settings.QA_CACHE_TTL_HOURS):
                    self.db.delete(cache_entry)
                    self.db.commit()
                    return None
                cache_entry.hit_count += 1
                cache_entry.last_used_at = now
                self.db.commit()
                return cache_entry

            # Fuzzy match
            all_questions = self.db.scalars(select(QACache).where(QACache.language == language)).all()
            for entry in all_questions:
                similarity = SequenceMatcher(None, normalized_question, entry.normalized_question).ratio()
                if similarity >= settings.QA_CACHE_FUZZY_THRESHOLD:
                    now, last_used = _cache_clock(entry.last_used_at)
                    if now - last_used > timedelta(hours=settings.QA_CACHE_TTL_HOURS):
                        self.db.delete(entry)
                        self.db.commit()
                        continue
                    entry.hit_count += 1
                    entry.last_used_at = now
                    self.db.commit()
                    return entry
        except Exception as e:
            logger.error(f"QA cache lookup failed: {e}")
            return None

        return None

    def get_cached_answer(self, question: str, language: str) -> str | None:
        entry = self.get_cached_entry(question, language)
        return entry.answer if entry else None

    def store_answer(self, question: str, answer: str, language: str, category: str) -> None:
        """Store a new question-answer pair in the cache."""
        try:
            normalized_question = normalize_question(question)
            cache_entry = self.db.scalar(select(QACache).where(
                QACache.normalized_question == normalized_question,
                QACache.language == language,
            ))
            if cache_entry:
                cache_entry.answer = answer
                cache_entry.category = category
                cache_entry.last_used_at = datetime.now(timezone.utc)
            else:
                self.db.add(QACache(normalized_question=normalized_question, answer=answer,
                                    language=language, category=category))
            self.db.commit()
        except Exception as e:
            logger.error(f"QA cache store failed: {e}")
