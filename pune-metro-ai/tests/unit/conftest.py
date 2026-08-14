import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base
from app.services.whatsapp_client import whatsapp_client

os.environ.setdefault("WHATSAPP_VERIFY_TOKEN", "test")
os.environ.setdefault("WHATSAPP_ACCESS_TOKEN", "test")
os.environ.setdefault("WHATSAPP_PHONE_NUMBER_ID", "test")
os.environ.setdefault("PRIMARY_LLM_API_KEY", "test")
os.environ.setdefault("FALLBACK_LLM_API_KEY", "test")
os.environ.setdefault("DATABASE_URL", "sqlite://")


@pytest.fixture(scope="function")
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        db.close()


@pytest.fixture(autouse=True)
def stub_whatsapp_typing_indicator(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent unit tests from calling Meta when webhook typing is not under test."""

    async def mark_as_read_and_typing(_message_id: str) -> None:
        pass

    monkeypatch.setattr(
        whatsapp_client,
        "mark_as_read_and_typing",
        mark_as_read_and_typing,
    )