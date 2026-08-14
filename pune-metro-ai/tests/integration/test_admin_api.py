import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.models import (
    AdminUser,
    CategoryLog,
    ComplaintTracking,
    Conversation,
    Message,
    User,
)
from app.db.session import SessionLocal
from app.security.auth import get_password_hash


@pytest.fixture()
def db_session() -> Session:
    return SessionLocal()


def seed_admin_user(db: Session, username: str = "admin", password: str = "secret123") -> AdminUser:
    existing = db.query(AdminUser).filter(AdminUser.username == username).first()
    if existing is not None:
        return existing

    user = AdminUser(username=username, hashed_password=get_password_hash(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def seed_dashboard_data(
    db: Session,
    *,
    resolved_count: int = 1,
    pending_count: int = 1,
    history_days: int = 7,
) -> None:
    for table in ["complaint_tracking", "category_logs", "messages", "conversations", "users", "admin_users"]:
        db.execute(text(f'DELETE FROM {table}'))
    db.commit()

    now = datetime.now(timezone.utc)
    user_one = User(whatsapp_number="+111", name="Ada", created_at=now)
    user_two = User(whatsapp_number="+222", name="Grace", created_at=now)
    db.add_all([user_one, user_two])
    db.flush()

    conv_one = Conversation(user_id=user_one.id, status="active", created_at=now - timedelta(hours=1), updated_at=now)
    conv_two = Conversation(user_id=user_two.id, status="active", created_at=now - timedelta(days=2), updated_at=now - timedelta(days=2))
    db.add_all([conv_one, conv_two])
    db.flush()

    db.add_all(
        [
            Message(conversation_id=conv_one.id, role="user", content="hello", created_at=now),
            Message(conversation_id=conv_one.id, role="assistant", content="hi", created_at=now - timedelta(days=1)),
            Message(conversation_id=conv_two.id, role="user", content="later", created_at=now - timedelta(days=2)),
        ]
    )
    db.flush()

    category_log_one = CategoryLog(
        user_id=user_one.id,
        conversation_id=conv_one.id,
        categories=["complaint", "suggestion"],
        subcategory="Refund",
        message="refund issue",
        status="open",
        created_at=now - timedelta(days=history_days),
    )
    category_log_two = CategoryLog(
        user_id=user_two.id,
        conversation_id=conv_two.id,
        categories=["enquiry"],
        subcategory="Train Operation",
        message="schedule question",
        status="resolved",
        created_at=now - timedelta(days=history_days),
    )
    db.add_all([category_log_one, category_log_two])
    db.flush()

    complaint_rows = []
    for index in range(pending_count):
        complaint_rows.append(
            ComplaintTracking(
                category_log_id=category_log_one.id,
                user_id=user_one.id,
                conversation_id=conv_one.id,
                token=f"T{index + 1}",
                category="complaint",
                status="pending",
                created_at=now,
            )
        )

    resolved_specs = [
        (category_log_two.id, "suggestion"),
        (category_log_one.id, "suggestion"),
        (category_log_two.id, "complaint"),
        (category_log_one.id, "complaint"),
    ]
    for index in range(resolved_count):
        category_log_id, category = resolved_specs[index]
        complaint_rows.append(
            ComplaintTracking(
                category_log_id=category_log_id,
                user_id=user_two.id if index % 2 else user_one.id,
                conversation_id=conv_two.id if index % 2 else conv_one.id,
                token=f"R{index + 1}",
                category=category,
                status="resolved",
                created_at=now - timedelta(days=history_days),
            )
        )

    db.add_all(complaint_rows)
    db.commit()


def test_login_succeeds_and_fails(client: TestClient, db_session: Session) -> None:
    seed_admin_user(db_session, username="admin", password="secret123")

    response = client.post(
        "/api/v1/admin/auth/login",
        data={"username": "admin", "password": "secret123"},
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 200
    assert response.json()["token_type"] == "bearer"
    assert response.json()["access_token"]

    failed = client.post(
        "/api/v1/admin/auth/login",
        data={"username": "admin", "password": "wrong"},
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert failed.status_code == 401


def test_stats_overview_returns_expected_aggregates(client: TestClient, db_session: Session) -> None:
    seed_dashboard_data(db_session, history_days=7)
    seed_admin_user(db_session)

    token = client.post(
        "/api/v1/admin/auth/login",
        data={"username": "admin", "password": "secret123"},
        headers={"content-type": "application/x-www-form-urlencoded"},
    ).json()["access_token"]

    response = client.get(
        "/api/v1/admin/stats/overview",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_users"] == 2
    assert payload["total_conversations"] == 2
    assert payload["messages_today"] >= 1
    assert payload["messages_this_week"] >= 2
    assert payload["tickets_by_status"]["pending"] == 1
    assert payload["tickets_by_status"]["resolved"] == 1
    assert payload["categories_by_type"]["complaint"] == 1
    assert payload["categories_by_type"]["suggestion"] == 1
    assert payload["categories_by_type"]["enquiry"] == 1
    assert payload["avg_tickets_resolved_per_day"] == pytest.approx(0.14)


def test_stats_overview_uses_shorter_history_window_for_average(client: TestClient, db_session: Session) -> None:
    seed_dashboard_data(db_session, resolved_count=1, pending_count=1, history_days=2)
    seed_admin_user(db_session)

    token = client.post(
        "/api/v1/admin/auth/login",
        data={"username": "admin", "password": "secret123"},
        headers={"content-type": "application/x-www-form-urlencoded"},
    ).json()["access_token"]

    response = client.get(
        "/api/v1/admin/stats/overview",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["avg_tickets_resolved_per_day"] == pytest.approx(0.33)


def test_stats_overview_returns_zero_average_for_empty_dataset(client: TestClient, db_session: Session) -> None:
    for table in ["complaint_tracking", "category_logs", "messages", "conversations", "users", "admin_users"]:
        db_session.execute(text(f'DELETE FROM {table}'))
    db_session.commit()
    seed_admin_user(db_session)

    token = client.post(
        "/api/v1/admin/auth/login",
        data={"username": "admin", "password": "secret123"},
        headers={"content-type": "application/x-www-form-urlencoded"},
    ).json()["access_token"]

    response = client.get(
        "/api/v1/admin/stats/overview",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["avg_tickets_resolved_per_day"] == 0.0


def test_tickets_endpoint_returns_native_status_values(client: TestClient, db_session: Session) -> None:
    seed_dashboard_data(db_session)
    seed_admin_user(db_session)

    token = client.post(
        "/api/v1/admin/auth/login",
        data={"username": "admin", "password": "secret123"},
        headers={"content-type": "application/x-www-form-urlencoded"},
    ).json()["access_token"]

    response = client.get(
        "/api/v1/admin/tickets?page=1&page_size=10",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    payload = response.json()
    items = payload["items"]
    assert any(item["status"] == "pending" for item in items)
    assert any(item["status"] == "resolved" for item in items)


def test_admin_routes_require_authentication(client: TestClient) -> None:
    routes = [
        "/api/v1/admin/stats/overview",
        "/api/v1/admin/conversations?page=1&page_size=10",
        "/api/v1/admin/users?page=1&page_size=10",
        "/api/v1/admin/category-logs?page=1&page_size=10",
        "/api/v1/admin/tickets?page=1&page_size=10",
    ]
    for route in routes:
        response = client.get(route)
        assert response.status_code == 401
        assert response.json()["detail"]


@patch("app.api.admin_router.whatsapp_client", new_callable=AsyncMock)
def test_ticket_status_change_notifies_user(mock_whatsapp_client: AsyncMock, client: TestClient, db_session: Session) -> None:
    """Verify that changing a ticket's status sends a WhatsApp notification."""
    seed_dashboard_data(db_session)
    seed_admin_user(db_session)
    token = client.post(
        "/api/v1/admin/auth/login",
        data={"username": "admin", "password": "secret123"},
        headers={"content-type": "application/x-www-form-urlencoded"},
    ).json()["access_token"]
    
    ticket = db_session.query(ComplaintTracking).filter(ComplaintTracking.status == "pending").first()
    
    # Test approved
    response = client.patch(
        f"/api/v1/admin/tickets/{ticket.id}",
        json={"status": "approved"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    mock_whatsapp_client.send_text_message.assert_called_once()
    
    # Test resolved
    mock_whatsapp_client.reset_mock()
    response = client.patch(
        f"/api/v1/admin/tickets/{ticket.id}",
        json={"status": "resolved"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    mock_whatsapp_client.send_text_message.assert_called_once()

    # Test no duplicate notification
    mock_whatsapp_client.reset_mock()
    response = client.patch(
        f"/api/v1/admin/tickets/{ticket.id}",
        json={"status": "resolved"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    mock_whatsapp_client.send_text_message.assert_not_called()


@patch("app.api.admin_router.whatsapp_client", new_callable=AsyncMock)
def test_whatsapp_send_failure_does_not_fail_request(mock_whatsapp_client: AsyncMock, client: TestClient, db_session: Session) -> None:
    """Verify that a failed WhatsApp notification does not fail the API request."""
    mock_whatsapp_client.send_text_message.side_effect = Exception("WhatsApp send failed")
    seed_dashboard_data(db_session)
    seed_admin_user(db_session)
    token = client.post(
        "/api/v1/admin/auth/login",
        data={"username": "admin", "password": "secret123"},
        headers={"content-type": "application/x-www-form-urlencoded"},
    ).json()["access_token"]
    
    ticket = db_session.query(ComplaintTracking).filter(ComplaintTracking.status == "pending").first()
    
    response = client.patch(
        f"/api/v1/admin/tickets/{ticket.id}",
        json={"status": "approved"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "approved"