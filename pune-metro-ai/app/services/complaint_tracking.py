"""Complaint-token creation and complaint-specific reply helpers."""

import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import CategoryLog, ComplaintTracking
from app.services.llm_client import (
    detect_script,
    localized_reply,
    resolve_reply_language,
)


def generate_complaint_token() -> str:
    """Return a tracking token in the user-facing PMC-###### format."""
    return f"PMC-{secrets.randbelow(1_000_000):06d}"


def create_complaint_tracking(
    *,
    category_log: CategoryLog,
    user_id: int,
    conversation_id: int,
    db: Session,
    category: str = "complaint",
) -> ComplaintTracking:
    """Create a collision-free pending token for a complaint or suggestion."""
    for _ in range(20):
        token = generate_complaint_token()
        if db.scalar(select(ComplaintTracking).where(ComplaintTracking.token == token)):
            continue
        tracking = ComplaintTracking(
            category_log_id=category_log.id,
            user_id=user_id,
            conversation_id=conversation_id,
            token=token,
            category=category,
            status="pending",
        )
        db.add(tracking)
        db.flush()
        return tracking
    raise RuntimeError("Unable to generate a unique complaint tracking token")


def get_latest_complaint_tracking(user_id: int, db: Session) -> ComplaintTracking | None:
    """Return the user's most recent complaint token."""
    return db.scalar(
        select(ComplaintTracking)
        .where(ComplaintTracking.user_id == user_id)
        .order_by(ComplaintTracking.created_at.desc(), ComplaintTracking.id.desc())
        .limit(1)
    )


def is_contact_request(message: str) -> bool:
    """Return whether a message asks for a phone or other contact channel."""
    normalized = message.casefold()
    contact_terms = ("phone", "number", "helpline", "call", "contact", "फोन", "नंबर", "क्रमांक")
    return any(term in normalized for term in contact_terms)


def is_complaint_contact_followup(
    message: str, *, has_current_complaint_context: bool = False
) -> bool:
    """Identify an explicit or in-flow request for complaint contact details."""
    normalized = message.casefold()
    complaint_terms = ("complaint", "issue", "report", "ticket", "tracking", "token", "तक्रार", "शिकायत")
    return is_contact_request(message) and (
        has_current_complaint_context or any(term in normalized for term in complaint_terms)
    )


def complaint_confirmation_reply(token: str, user_message: str, language: str) -> str:
    """Build a token-only complaint acknowledgment in the classified language."""
    script = detect_script(user_message)
    replies = {
        "english": (
            f"Thank you for reporting this. Your complaint has been logged with tracking ID {token}. "
            "Our team will review it and follow up with you once processed."
        ),
        "hindi": (
            f"इसकी सूचना देने के लिए धन्यवाद। आपकी शिकायत ट्रैकिंग आईडी {token} के साथ दर्ज कर ली गई है। "
            "हमारी टीम इसकी समीक्षा करेगी और प्रक्रिया पूरी होने पर आपको यहीं अपडेट देगी।"
        ),
        "hindi_romanized": (
            f"Batane ke liye dhanyavaad. Aapki shikayat tracking ID {token} ke saath "
            "darj ho gayi hai. Hamari team ise dekhegi aur process hone ke baad yahin "
            "update degi."
        ),
        "marathi": (
            f"याची नोंद दिल्याबद्दल धन्यवाद. तुमची तक्रार ट्रॅकिंग आयडी {token} सह नोंदवली आहे. "
            "आमची टीम तिचे परीक्षण करून प्रक्रिया पूर्ण झाल्यावर तुम्हाला इथेच अपडेट देईल."
        ),
        "marathi_romanized": (
            f"Yachi mahiti dilyabaddal dhanyavaad. Tumchi takrar tracking ID {token} "
            "sobat nondavli aahe. Aamchi team ti tapasel ani process zhalyavar tumhala "
            "ithech update deil."
        ),
    }
    return localized_reply(replies, language, script)


def suggestion_confirmation_reply(token: str, user_message: str, language: str) -> str:
    """Build a suggestion acknowledgment in the classified language."""
    script = detect_script(user_message)
    replies = {
        "english": (
            f"Thanks for the suggestion! We've noted it with reference ID {token}. "
            "Our team reviews suggestions periodically as we plan improvements."
        ),
        "hindi": (
            f"सुझाव के लिए धन्यवाद! हमने इसे संदर्भ आईडी {token} के साथ दर्ज कर लिया है। "
            "सुधारों की योजना बनाते समय हमारी टीम समय-समय पर सुझावों की समीक्षा करती है।"
        ),
        "hindi_romanized": (
            f"Suggestion ke liye dhanyavaad! Humne ise reference ID {token} ke saath "
            "note kar liya hai. Sudhar ki planning ke dauran hamari team suggestions "
            "ko samay-samay par review karti hai."
        ),
        "marathi": (
            f"सूचनेबद्दल धन्यवाद! आम्ही ती संदर्भ आयडी {token} सह नोंदवली आहे. "
            "सुधारणांचे नियोजन करताना आमची टीम वेळोवेळी सूचनांचा आढावा घेते."
        ),
        "marathi_romanized": (
            f"Suchanebaddal dhanyavaad! Aamhi ti reference ID {token} sobat nondavli "
            "aahe. Sudharancha plan kartana aamchi team veloveli suchancha aadhava ghete."
        ),
    }
    return localized_reply(replies, language, script)


def complaint_contact_redirect_reply(token: str, user_message: str) -> str:
    """Redirect complaint-specific contact requests to the tracking token."""
    language, script = resolve_reply_language(user_message)
    replies = {
        "english": (
            f"Your complaint {token} is being reviewed by our team; you'll receive an update "
            "directly here once it's processed."
        ),
        "hindi": (
            f"आपकी शिकायत {token} हमारी टीम की समीक्षा में है। प्रक्रिया पूरी होने पर आपको यहीं सीधे अपडेट मिलेगा।"
        ),
        "hindi_romanized": (
            f"Aapki shikayat {token} hamari team review kar rahi hai. Process poora "
            "hone par aapko yahin seedha update milega."
        ),
        "marathi": (
            f"तुमची तक्रार {token} आमची टीम तपासत आहे. प्रक्रिया पूर्ण झाल्यावर तुम्हाला इथेच थेट अपडेट मिळेल."
        ),
        "marathi_romanized": (
            f"Tumchi takrar {token} aamchi team tapasat aahe. Process purna zhalyavar "
            "tumhala ithech thet update milel."
        ),
    }
    return localized_reply(replies, language, script)
