"""SQLAlchemy engine, session factory, and FastAPI database dependency."""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings


engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    """Provide a database session and close it after the request completes."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
