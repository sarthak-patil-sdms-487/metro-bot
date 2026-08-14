import os

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import AdminUser
from app.db.session import SessionLocal
from app.security.auth import get_password_hash


def seed_admin_user() -> None:
    engine = create_engine(settings.DATABASE_URL)
    SessionLocal.configure(bind=engine)
    with SessionLocal() as session:
        username = os.getenv("ADMIN_SEED_USERNAME", "admin")
        password = os.getenv("ADMIN_SEED_PASSWORD")
        if not password:
            raise RuntimeError("ADMIN_SEED_PASSWORD is required")
        existing = session.query(AdminUser).filter(AdminUser.username == username).first()
        if existing:
            existing.hashed_password = get_password_hash(password)
            session.commit()
            print(f"Updated admin user {username}")
            return
        session.add(AdminUser(username=username, hashed_password=get_password_hash(password)))
        session.commit()
        print(f"Created admin user {username}")


if __name__ == "__main__":
    seed_admin_user()
