import os
from collections.abc import Generator
from typing import Any

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

os.environ.setdefault("WHATSAPP_VERIFY_TOKEN", "test")
os.environ.setdefault("WHATSAPP_ACCESS_TOKEN", "test")
os.environ.setdefault("WHATSAPP_PHONE_NUMBER_ID", "test")
os.environ.setdefault("PRIMARY_LLM_API_KEY", "test")
os.environ.setdefault("FALLBACK_LLM_API_KEY", "test")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
os.environ.setdefault("JWT_EXPIRE_MINUTES", "30")
os.environ.setdefault("ADMIN_DASHBOARD_ORIGIN", "http://localhost:5173")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://pune_metro:pune_metro@localhost:5433/pune_metro_test",
)

from app.db.models import Base
from app.db.session import SessionLocal, get_db
from app.main import app


def _reset_database(engine: Any) -> None:
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(text(f"DROP TABLE IF EXISTS {table.name} CASCADE"))
        Base.metadata.create_all(bind=conn)


@pytest.fixture(scope="session")
def integration_engine() -> Any:
    db_name = os.getenv("POSTGRES_TEST_DB", "pune_metro_test")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5433")
    user = os.getenv("POSTGRES_USER", "pune_metro")
    password = os.getenv("POSTGRES_PASSWORD", "pune_metro")
    base_url = os.getenv(
        "POSTGRES_TEST_URL",
        f"postgresql+psycopg://{user}:{password}@{host}:{port}/postgres",
    )
    db_url = f"postgresql+psycopg://{user}:{password}@{host}:{port}/{db_name}"
    os.environ["DATABASE_URL"] = db_url

    with psycopg.connect(base_url.replace("postgresql+psycopg://", "postgresql://"), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
            exists = cur.fetchone()
            if exists:
                cur.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
            cur.execute(f'CREATE DATABASE "{db_name}"')

    test_engine = create_engine(db_url, pool_pre_ping=True)
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(alembic_cfg, "head")
    return test_engine


@pytest.fixture(autouse=True)
def db_session(integration_engine: Any) -> Generator[Session, None, None]:
    SessionLocal.configure(bind=integration_engine)
    _reset_database(integration_engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def client(db_session: Session, integration_engine: Any) -> Generator[Any, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("DATABASE_URL", str(integration_engine.url))
        from fastapi.testclient import TestClient

        with TestClient(app) as test_client:
            yield test_client
    app.dependency_overrides.clear()
