"""Chat-only orchestration and state ownership.

This boundary deliberately refuses call conversations so WhatsApp text can never
inherit a live call's history, complaint fields, language, or lifecycle.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Conversation, User


class ChatAgent:
    channel = "chat"

    @staticmethod
    def get_or_create_conversation(
        *, sender: str, profile_name: str | None, db: Session
    ) -> tuple[User, Conversation]:
        user = db.scalar(select(User).where(User.whatsapp_number == sender))
        if user is None:
            user = User(whatsapp_number=sender, name=profile_name)
            db.add(user)
            db.flush()
        elif profile_name and not user.name:
            user.name = profile_name
        conversation = db.scalar(
            select(Conversation).where(
                Conversation.user_id == user.id,
                Conversation.channel == ChatAgent.channel,
                Conversation.status == "active",
                Conversation.is_closed.is_(False),
            ).order_by(Conversation.id.desc())
        )
        if conversation is None:
            conversation = Conversation(user_id=user.id, channel=ChatAgent.channel)
            db.add(conversation)
            db.flush()
        return user, conversation


chat_agent = ChatAgent()
