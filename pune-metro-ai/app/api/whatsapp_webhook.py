"""Meta WhatsApp Cloud API webhook endpoints."""

import logging
import re

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import CategoryLog, Conversation, Message, TicketDetails, User, ResponseSourceLog
from app.db.session import get_db
from app.services.admin_dashboard import notify_admin_dashboard
from app.services.brain import respond_with_legacy_context
from app.services.brain_models import BrainMessage, BrainRequest
from app.services.complaint_tracking import (
    complaint_confirmation_reply,
    complaint_contact_redirect_reply,
    create_complaint_tracking,
    get_latest_complaint_tracking,
    is_contact_request,
    is_complaint_contact_followup,
    suggestion_confirmation_reply,
)
from app.services.collection_flow import (
    advance_collection,
    parse_contact_number,
    start_collection,
)
from app.services.guardrails import apply_guardrails
from app.services.llm_client import (
    build_greeting_reply,
    build_route_grounding,
    classify_message,
    check_complaint_status,
    detect_script,
    detect_language_switch_request,
    generate_category_prompt,
    generate_collection_prompt,
    generate_closing_reply,
    generate_language_switch_confirmation,
    generate_out_of_scope_reply,
    generate_reply,
    generate_unsupported_station_reply,
    find_unsupported_station_names,
    find_station_names,
    generate_unrecognized_station_reply,
    is_station_or_route_question,
    reply_has_only_canonical_station_names,
    is_confusion_message,
    load_reference_data,
    normalize_category,
    reply_variant_key,
    resolve_reply_language,
    resolve_station_alias,
    short_message_intent,
    _sanitize_outbound_text,
)
from app.services.qa_cache import QACacheService
from app.services.chat_agent import chat_agent
from app.services.cost_tracking import make_cache_log, make_llm_log
from app.services.whatsapp_client import whatsapp_client
from app.services import whatsapp_calling_client


router = APIRouter()
logger = logging.getLogger(__name__)

_COLLECTION_NON_ANSWERS = frozenset(
    {
        "hi", "hii", "hiii", "hello", "hey", "heyy", "thanks", "thank you",
        "thank u", "thx", "ok", "okay", "yes", "no", "nope", "wtf", "cool",
        "sure", "got it", "alright",
    }
)


def _is_collection_non_answer(value: str) -> bool:
    normalized = " ".join(value.casefold().strip("!?.,;:").split())
    return len(normalized) < 2 or normalized in _COLLECTION_NON_ANSWERS


_DESCRIPTION_META_PATTERNS = (
    r"\b(?:you|u) (?:already )?(?:have|got|know) (?:it|this)\b",
    r"\b(?:described|said|mentioned|told) (?:it|that|this|before|above)\b",
    r"\b(?:same as|as above)\b",
)


def _is_valid_description(value: str) -> bool:
    """Accept actual issue/idea detail, never acknowledgements about the flow."""
    normalized = " ".join(value.casefold().strip("!?.,;:").split())
    if _is_collection_non_answer(normalized) or len(normalized) < 18:
        return False
    return not any(re.search(pattern, normalized) for pattern in _DESCRIPTION_META_PATTERNS)


def _has_initial_description(value: str, category: str) -> bool:
    """Do not treat a bare category request as its own report description."""
    normalized = " ".join(value.casefold().split())
    bare_request = re.fullmatch(
        r"(?:i (?:have|want to make|want to give)|i want|need to)?\s*(?:a )?"
        rf"{category}(?: please)?", normalized
    )
    return not bare_request and _is_valid_description(value)


def _correction_station_and_question(
    message_text: str, conversation: Conversation, db: Session
) -> tuple[str, str] | None:
    """Apply an explicit station correction to the immediately preceding question."""
    patterns = (
        r"(?:it'?s|it is)\s+not\s+(.+?)\s+(?:it'?s|it is)\s+(.+)$",
        r"(?:no,?\s*)?i meant\s+(.+)$",
        r"^\*?(.+?)\s+not\s+(.+?)\*?$",
    )
    corrected = None
    for pattern in patterns:
        match = re.match(pattern, message_text.strip(), re.IGNORECASE)
        if not match:
            continue
        corrected = match.group(match.lastindex).strip(" *.,!?")
        break
    if not corrected or resolve_station_alias(corrected) is None:
        return None
    previous = db.scalar(
        select(Message)
        .where(Message.conversation_id == conversation.id, Message.role == "user")
        .order_by(Message.created_at.desc(), Message.id.desc())
        .offset(1)
        .limit(1)
    )
    if previous is None or not is_station_or_route_question(previous.content):
        return None
    # Replace any known station alias in the earlier question; if it contained an
    # invented name, append the confirmed station so generation is still grounded.
    question = previous.content
    known = find_station_names(question)
    if known:
        for station in known:
            question = re.sub(re.escape(station), resolve_station_alias(corrected) or corrected, question, flags=re.I)
    else:
        question = f"{question} (The correct station is {resolve_station_alias(corrected)}.)"
    return resolve_station_alias(corrected) or corrected, question


def _valid_contact_number(value: str) -> str | None:
    """Return a normalized plausible phone number, or None for invalid input."""
    return parse_contact_number(value)


def _collection_station(value: str) -> str | None:
    """Resolve a complaint station through the same aliases used for route replies."""
    return resolve_station_alias(value)


def _collection_confirmation_prompt(conversation: Conversation) -> str:
    """Build the deterministic collected-data summary before registration."""
    category = getattr(conversation, "pending_category", None) or "complaint"
    summary = f"Here's what I have:\nName: {getattr(conversation, 'complaint_collection_full_name', '')}\n"
    summary += (
        f"Contact: {getattr(conversation, 'complaint_collection_contact_number', '')}\n"
        f"Station: {getattr(conversation, 'complaint_collection_station', '')}\n"
    )
    summary += f"Description: {getattr(conversation, 'complaint_collection_description', '')}\n\n"
    return f"{summary}Do you want me to register this {category}? (yes/no)"

# ``other_help`` is an interaction id, not a stored category. It intentionally maps
# to the canonical ``enquiry`` category after selection.
CATEGORY_MENU_TEXT = {
    "english": {
        "header": "How can we help?",
        "body": "Choose the topic that best matches your message.",
        "button": "View topics",
        "section": "Categories",
        "description": "{title} about Pune Metro",
    },
    "hindi": {
        "header": "हम आपकी कैसे मदद कर सकते हैं?",
        "body": "आपके संदेश से सबसे मेल खाता विषय चुनें.",
        "button": "विषय देखें",
        "section": "श्रेणियाँ",
        "description": "{title} के बारे में",
    },
    "hindi_romanized": {
        "header": "Hum aapki kaise madad karein?",
        "body": "Apne message se sabse zyada milta-julta topic chunein.",
        "button": "Topics dekhein",
        "section": "Categories",
        "description": "{title} ke baare mein",
    },
    "marathi": {
        "header": "आम्ही तुम्हाला कशी मदत करू शकतो?",
        "body": "तुमच्या संदेशाशी सर्वात जुळणारा विषय निवडा.",
        "button": "विषय पहा",
        "section": "श्रेणी",
        "description": "{title} बद्दल",
    },
    "marathi_romanized": {
        "header": "Aamhi tumhala kashi madat karu?",
        "body": "Tumchya message shi saglyat jultanara vishay nivda.",
        "button": "Vishay paha",
        "section": "Categories",
        "description": "{title} baddal",
    },
}
CATEGORY_MENU_OPTIONS_TRANSLATED = {
    "english": [
        ("complaint", "Complaint"),
        ("suggestion", "Suggestion"),
        ("appreciation", "Appreciation"),
        ("enquiry", "Enquiry"),
        ("other_help", "Others"),
    ],
    "hindi": [
        ("complaint", "शिकायत"),
        ("suggestion", "सुझाव"),
        ("appreciation", "प्रशंसा"),
        ("enquiry", "पूछताछ"),
        ("other_help", "अन्य"),
    ],
    "hindi_romanized": [
        ("complaint", "Shikayat"),
        ("suggestion", "Suggestion"),
        ("appreciation", "Tareef"),
        ("enquiry", "Sawal"),
        ("other_help", "Anya"),
    ],
    "marathi": [
        ("complaint", "तक्रार"),
        ("suggestion", "सूचना"),
        ("appreciation", "कौतुक"),
        ("enquiry", "चौकशी"),
        ("other_help", "इतर"),
    ],
    "marathi_romanized": [
        ("complaint", "Takrar"),
        ("suggestion", "Suchana"),
        ("appreciation", "Kautuk"),
        ("enquiry", "Chaukashi"),
        ("other_help", "Itar"),
    ],
}
# Retained as the canonical English option set for existing callers and tests.
CATEGORY_MENU_OPTIONS = CATEGORY_MENU_OPTIONS_TRANSLATED["english"]

_GREETING_MENU_BODIES = {
    "english": {
        "hi": "Pick a topic below, or just type your Pune Metro question.",
        "hello": "Choose a topic below, or type your question directly.",
        "hey": "Pick a topic below — or send your question straight away.",
        "good morning": "Select a topic below, or write your question directly.",
        "namaste": "Choose a topic below, or simply type your question.",
        "namaskar": "Pick a topic below, or type your question directly.",
        "default": "Pick a topic below, or type your Pune Metro question.",
    },
    "hindi": {
        "hi": "नीचे एक विषय चुनें या अपना पुणे मेट्रो का सवाल सीधे लिखें।",
        "hello": "नीचे एक विषय चुनें या अपना सवाल सीधे लिखें।",
        "hey": "नीचे एक विषय चुनें या अपना सवाल सीधे भेजें।",
        "good morning": "नीचे एक विषय चुनें या अपना सवाल लिखें।",
        "namaste": "नीचे एक विषय चुनें या अपना सवाल सीधे लिखें।",
        "namaskar": "नीचे एक विषय चुनें या अपना सवाल सीधे लिखें।",
        "default": "नीचे एक विषय चुनें या अपना पुणे मेट्रो का सवाल लिखें।",
    },
    "hindi_romanized": {
        "hi": "Neeche ek topic chunein ya apna Pune Metro sawal seedha likhein.",
        "hello": "Neeche ek topic chunein ya apna sawal seedha likhein.",
        "hey": "Neeche ek topic chunein ya apna sawal seedha bhejein.",
        "good morning": "Neeche ek topic chunein ya apna sawal likhein.",
        "namaste": "Neeche ek topic chunein ya apna sawal seedha likhein.",
        "namaskar": "Neeche ek topic chunein ya apna sawal seedha likhein.",
        "default": "Neeche ek topic chunein ya apna Pune Metro sawal likhein.",
    },
    "marathi": {
        "hi": "खालील विषय निवडा किंवा तुमचा पुणे मेट्रोचा प्रश्न थेट लिहा.",
        "hello": "खालील विषय निवडा किंवा तुमचा प्रश्न थेट लिहा.",
        "hey": "खालील विषय निवडा किंवा तुमचा प्रश्न थेट पाठवा.",
        "good morning": "खालील विषय निवडा किंवा तुमचा प्रश्न लिहा.",
        "namaste": "खालील विषय निवडा किंवा तुमचा प्रश्न थेट लिहा.",
        "namaskar": "खालील विषय निवडा किंवा तुमचा प्रश्न थेट लिहा.",
        "default": "खालील विषय निवडा किंवा तुमचा पुणे मेट्रोचा प्रश्न लिहा.",
    },
    "marathi_romanized": {
        "hi": "Khalil vishay nivda kinva tumcha Pune Metro prashna thet liha.",
        "hello": "Khalil vishay nivda kinva tumcha prashna thet liha.",
        "hey": "Khalil vishay nivda kinva tumcha prashna thet pathva.",
        "good morning": "Khalil vishay nivda kinva tumcha prashna liha.",
        "namaste": "Khalil vishay nivda kinva tumcha prashna thet liha.",
        "namaskar": "Khalil vishay nivda kinva tumcha prashna thet liha.",
        "default": "Khalil vishay nivda kinva tumcha Pune Metro prashna liha.",
    },
}


def _greeting_menu_body(message_text: str, language: str, script: str) -> str:
    """Return a varied, instruction-only continuation for a greeting menu."""
    normalized = " ".join(message_text.casefold().strip("!?.,;:").split())
    greeting_key = next(
        (
            key
            for key in ("hi", "hello", "hey", "good morning", "namaste", "namaskar")
            if key in normalized
        ),
        "default",
    )
    bodies = _GREETING_MENU_BODIES.get(
        reply_variant_key(language, script), _GREETING_MENU_BODIES["english"]
    )
    return bodies[greeting_key]


FIRST_REPLY_GREETINGS = {
    "english": "Namaskar!",
    "hindi": "नमस्ते!",
    "hindi_romanized": "Namaste!",
    "marathi": "नमस्कार!",
    "marathi_romanized": "Namaskar!",
}
SUBCATEGORIES = [
    "Passenger Amenities",
    "Staff Complaints",
    "Refund",
    "AFC & Ticketing",
    "Train Operation & Services",
    "Feeder Services",
    "Others",
]
CATEGORY_SUBCATEGORIES = {
    "complaint": SUBCATEGORIES,
    "suggestion": SUBCATEGORIES,
    "appreciation": [
        "Passenger Amenities",
        "Staff Complaints",
        "Train Operation & Services",
        "Feeder Services",
        "Others",
    ],
    "enquiry": SUBCATEGORIES,
}
CATEGORY_INPUT_PROMPTS = {
    "english": {
        "complaint": (
            "Sorry to hear you've run into an issue! Please describe what happened, "
            "including the station and time if possible, so we can help."
        ),
        "suggestion": (
            "We love ideas that improve Pune Metro! Please describe your suggestion "
            "and how it would make journeys better for passengers."
        ),
        "appreciation": (
            "That means a lot to us. Please tell us what made your Pune Metro "
            "experience special."
        ),
        "enquiry": (
            "Happy to help! Please share your Pune Metro question with as much detail "
            "as you can."
        ),
    },
    "hindi": {
        "complaint": (
            "आपको हुई परेशानी के लिए हमें खेद है। कृपया बताएं कि क्या हुआ और संभव हो "
            "तो स्टेशन तथा समय की जानकारी भी दें।"
        ),
        "suggestion": (
            "पुणे मेट्रो को बेहतर बनाने के लिए आपके विचार महत्वपूर्ण हैं। कृपया अपना "
            "सुझाव और यात्रियों को इससे होने वाला लाभ बताएं।"
        ),
        "appreciation": (
            "आपकी प्रशंसा हमारे लिए बहुत मायने रखती है। कृपया बताएं कि पुणे मेट्रो "
            "का आपका अनुभव किस वजह से खास रहा।"
        ),
        "enquiry": (
            "हमें मदद करके खुशी होगी। कृपया पुणे मेट्रो से जुड़ा अपना प्रश्न विस्तार "
            "से साझा करें।"
        ),
    },
    "hindi_romanized": {
        "complaint": (
            "Aapko hui pareshani ke liye humein khed hai. Kripya batayein kya hua, "
            "aur ho sake to station aur samay bhi batayein."
        ),
        "suggestion": (
            "Pune Metro ko behtar banane ke liye aapke ideas zaroori hain. Apna "
            "suggestion aur usse yatriyon ko kya fayda hoga, batayein."
        ),
        "appreciation": (
            "Aapki tareef hamare liye bahut maayne rakhti hai. Batayein ki Pune Metro "
            "ka aapka experience kis wajah se khaas raha."
        ),
        "enquiry": (
            "Madad karke humein khushi hogi. Pune Metro se juda apna sawal detail mein batayein."
        ),
    },
    "marathi": {
        "complaint": (
            "तुम्हाला झालेल्या त्रासाबद्दल आम्हाला खेद आहे. कृपया काय घडले ते सांगा "
            "आणि शक्य असल्यास स्थानक व वेळही नमूद करा."
        ),
        "suggestion": (
            "पुणे मेट्रो अधिक चांगली करण्यासाठी तुमच्या कल्पना महत्त्वाच्या आहेत. "
            "कृपया तुमची सूचना आणि तिचा प्रवाशांना कसा फायदा होईल ते सांगा."
        ),
        "appreciation": (
            "तुमचे कौतुक आमच्यासाठी खूप महत्त्वाचे आहे. पुणे मेट्रोमधील तुमचा अनुभव "
            "कशामुळे खास ठरला ते कृपया सांगा."
        ),
        "enquiry": (
            "मदत करायला आम्हाला आनंद होईल. कृपया पुणे मेट्रोविषयीचा तुमचा प्रश्न "
            "सविस्तर सांगा."
        ),
    },
    "marathi_romanized": {
        "complaint": (
            "Tumhala zhalelya trasabaddal aamhal khed aahe. Krupaya kay ghadla te sanga "
            "ani shaky asel tar station ani velahi sanga."
        ),
        "suggestion": (
            "Pune Metro ajun changli karayla tumchya kalpana mahatvachya aahet. Tumchi "
            "suchana ani ticha pravashanna kasa fayda hoil te sanga."
        ),
        "appreciation": (
            "Tumcha kautuk aamchyasathi khup mahatvacha aahe. Pune Metro madhla tumcha "
            "anubhav kashamule khas zhala te sanga."
        ),
        "enquiry": (
            "Madat karayla aamhal anand hoil. Pune Metro baddalcha tumcha prashna savistar sanga."
        ),
    },
}
COMPLAINT_STATUS_KEYWORDS = (
    "complaint",
    "register",
    "status",
    "ticket",
    "तक्रार",
    "स्थिती",
    "स्टेटस",
    "तिकीट",
    "नोंद",
)
UNSUPPORTED_MESSAGE_REPLIES = {
    "english": "Sorry, I can only understand text messages right now. Please type your question.",
    "hindi": "क्षमा करें, मैं अभी केवल टेक्स्ट संदेश समझ सकता हूं। कृपया अपना प्रश्न टाइप करें।",
    "hindi_romanized": "Kshama karein, main abhi keval text sandesh samajh sakta hoon. Kripya apna prashna type karein.",
    "marathi": "क्षमस्व, मी सध्या फक्त मजकूर संदेश समजू शकतो. कृपया तुमचा प्रश्न टाइप करा.",
    "marathi_romanized": "Kshamawa, mi sadhya fakt majkur sandesh samju shakto. Krupaya tumcha prashna type kara.",
}
CONFUSION_HANDOFF_REPLIES = {
    "english": (
        "It seems I haven't been able to clarify this. Would you like me to connect "
        "you with our support team?"
    ),
    "hindi": (
        "लगता है मैं आपकी बात स्पष्ट नहीं कर पाया। क्या आप हमारी सहायता टीम से जुड़ना चाहेंगे?"
    ),
    "hindi_romanized": (
        "Lagta hai main aapki baat samajh nahi paaya. Kya aap hamari support team se judna chahenge?"
    ),
    "marathi": (
        "तुमची अडचण मला स्पष्ट करता आली नाही असे दिसते. तुम्हाला आमच्या सहाय्य टीमशी जोडू का?"
    ),
    "marathi_romanized": (
        "Tumchi adchan mala spashta karta aali nahi asa distay. Tumhala aamchya support team shi jodu ka?"
    ),
}
CONFUSION_HANDOFF_REPLY = CONFUSION_HANDOFF_REPLIES["english"]
_EMOJI_ONLY_CHARACTERS = re.compile(
    r"^[\U0001F1E6-\U0001FAFF\u2600-\u27BF\uFE0E\uFE0F\u200D]+$"
)
_EMOJI_CHARACTER = re.compile(r"[\U0001F1E6-\U0001FAFF\u2600-\u27BF]")


def _sections(title: str, rows: list[dict[str, str]]) -> list[dict[str, object]]:
    """Build a Meta interactive-list section."""
    return [{"title": title, "rows": rows}]


def _is_first_assistant_reply(conversation_id: int, db: Session) -> bool:
    """Return whether this conversation has no persisted assistant response yet."""
    return (
        db.scalar(
            select(Message.id)
            .where(
                Message.conversation_id == conversation_id,
                Message.role == "assistant",
            )
            .limit(1)
        )
        is None
    )


def _with_first_reply_greeting(
    reply: str, language: str, script: str = "devanagari"
) -> str:
    """Prefix a direct answer with its one-time language-matched greeting."""
    key = reply_variant_key(language, script)
    greeting = FIRST_REPLY_GREETINGS.get(key, FIRST_REPLY_GREETINGS["english"])
    return f"{greeting} {reply}"


def is_emoji_only(text: str) -> bool:
    """Return whether text contains emoji characters but no words or other text."""
    stripped_text = text.strip()
    return bool(
        stripped_text
        and _EMOJI_CHARACTER.search(stripped_text)
        and _EMOJI_ONLY_CHARACTERS.fullmatch(stripped_text)
    )


def _get_or_create_active_conversation(
    *, sender: str, profile_name: str | None, db: Session
) -> tuple[User, Conversation]:
    """Find the WhatsApp user and their active conversation, creating either as needed."""
    return chat_agent.get_or_create_conversation(
        sender=sender, profile_name=profile_name, db=db
    )


async def _reply_unsupported_message(
    *,
    sender: str,
    conversation: Conversation,
    profile_name: str | None,
    db: Session,
    whatsapp_message_id: str | None,
    placeholder_content: str,
) -> None:
    """Log an unsupported inbound message and ask for a text-based question."""
    db.add(
        Message(
            conversation_id=conversation.id,
            whatsapp_message_id=whatsapp_message_id,
            role="user",
            content=placeholder_content,
        )
    )
    db.commit()

    language, script = resolve_reply_language(profile_name or "")
    if getattr(conversation, "preferred_language", None):
        language = conversation.preferred_language
    
    reply_key = reply_variant_key(language, script)
    reply = UNSUPPORTED_MESSAGE_REPLIES.get(reply_key, UNSUPPORTED_MESSAGE_REPLIES["english"])

    try:
        await whatsapp_client.send_text_message(
            to=sender, body=_sanitize_outbound_text(reply)
        )
    except Exception:
        logger.exception("Failed to send unsupported-message reply to %s", sender)


async def _send_category_list(
    to: str,
    language: str,
    script: str = "devanagari",
    body: str | None = None,
    header: str | None = None,
) -> None:
    """Present the shared five-option top-level category menu."""
    resolved_language = reply_variant_key(language, script)
    if resolved_language not in CATEGORY_MENU_TEXT:
        resolved_language = "english"
    menu_text = CATEGORY_MENU_TEXT[resolved_language]
    rows = [
        {
            "id": f"category:{selection}",
            "title": title,
            "description": menu_text["description"].format(title=title),
        }
        for selection, title in CATEGORY_MENU_OPTIONS_TRANSLATED[resolved_language]
    ]
    await whatsapp_client.send_interactive_list(
        to=to,
        header=header or menu_text["header"],
        body=body or menu_text["body"],
        button_text=menu_text["button"],
        sections=_sections(menu_text["section"], rows),
    )


async def _handle_interactive_reply(
    *,
    reply_id: str,
    interactive_message_id: str | None,
    sender: str,
    conversation: Conversation,
    db: Session,
) -> None:
    """Store a selected category and ask the user for free-text detail."""
    if reply_id.startswith("feedback:"):
        await _handle_feedback_reply(
            reply_id=reply_id,
            sender=sender,
            conversation=conversation,
            db=db,
        )
        return

    if not reply_id.startswith("category:"):
        return

    selection = reply_id.removeprefix("category:")
    # "Others" is a live open-ended help route, implemented as an enquiry so it
    # receives the existing question prompt and persists only canonical categories.
    category = "enquiry" if selection == "other_help" else normalize_category(selection)
    if category not in CATEGORY_SUBCATEGORIES:
        return

    conversation.pending_category = category
    conversation.confusion_handoff_shown = False
    conversation.unclear_streak_count = 0
    db.commit()

    previous_user_message = db.scalar(
        select(Message)
        .where(
            Message.conversation_id == conversation.id,
            Message.role == "user",
            Message.whatsapp_message_id != interactive_message_id,
        )
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(1)
    )
    last_user_message = previous_user_message.content if previous_user_message else ""
    resolved_language, prompt_script = resolve_reply_language(last_user_message)
    prompt_language = (
        getattr(conversation, "preferred_language", None) or resolved_language
    )
    prompt_key = reply_variant_key(prompt_language, prompt_script)
    if prompt_key not in CATEGORY_INPUT_PROMPTS:
        prompt_key = "english"
        prompt_language = "english"
        prompt_script = "latin"

    try:
        prompt = await generate_category_prompt(
            category, last_user_message, prompt_language, prompt_script
        )
    except Exception:
        logger.exception("Failed to generate category prompt for %s", category)
        prompt = "Please share a little more detail so I can help."
    if guardrail_reply := apply_guardrails(last_user_message, prompt):
        prompt = guardrail_reply

    try:
        await whatsapp_client.send_text_message(
            to=sender,
            body=_sanitize_outbound_text(prompt),
        )
    except Exception:
        logger.exception("Failed to request category details from %s", sender)


async def _handle_confusion_message(
    *, sender: str, message_text: str, conversation: Conversation, db: Session
) -> None:
    """Run the unclear-message state machine using the shared greeting menu.

    unclear -> menu -> unclear -> handoff -> [reset] -> unclear -> menu.
    The persisted handoff flag makes the first unclear message after a handoff start
    a fresh cycle, rather than immediately sending another handoff.
    """
    language, script = resolve_reply_language(message_text)
    conversation.pending_category = None
    if getattr(conversation, "confusion_handoff_shown", False):
        conversation.confusion_handoff_shown = False
        conversation.unclear_streak_count = 1
        db.commit()
        try:
            await _send_category_list(sender, language, script)
        except Exception:
            logger.exception("Failed to send post-handoff category list to %s", sender)
        return

    conversation.unclear_streak_count = getattr(conversation, "unclear_streak_count", 0) + 1
    should_handoff = conversation.unclear_streak_count >= 2
    if should_handoff:
        conversation.confusion_handoff_shown = True
        conversation.unclear_streak_count = 0
    db.commit()
    try:
        if should_handoff:
            handoff_key = reply_variant_key(language, script)
            await whatsapp_client.send_text_message(
                to=sender,
                body=_sanitize_outbound_text(
                    CONFUSION_HANDOFF_REPLIES.get(
                        handoff_key, CONFUSION_HANDOFF_REPLIES["english"]
                    )
                ),
            )
        else:
            # Deliberately reuse the exact greeting-menu sender; do not create a
            # context-specific confusion submenu.
            await _send_category_list(sender, language, script)
    except Exception:
        logger.exception("Failed to send confusion recovery response to %s", sender)


async def _handle_greeting_message(
    *, sender: str, message_text: str, conversation: Conversation, db: Session
) -> None:
    """Reset nested flow state and send the single shared greeting/category menu."""
    conversation.pending_category = None
    conversation.confusion_handoff_shown = False
    conversation.unclear_streak_count = 0
    db.commit()
    language, script = resolve_reply_language(message_text)
    # A list is one WhatsApp message, so keep the personalized greeting in its
    # header and reserve its body for the practical menu instruction.
    header = build_greeting_reply(message_text, language, script)
    body = _greeting_menu_body(message_text, language, script)
    if guardrail_reply := apply_guardrails(message_text, header):
        header = guardrail_reply
    if guardrail_reply := apply_guardrails(message_text, body):
        body = guardrail_reply
    try:
        await _send_category_list(
            sender,
            language,
            script,
            header=_sanitize_outbound_text(header),
            body=_sanitize_outbound_text(body),
        )
    except Exception:
        logger.exception("Failed to send category list to %s", sender)
        return
    db.add(
        Message(
            conversation_id=conversation.id,
            role="assistant",
            content=f"{header}\n{body}",
        )
    )
    db.commit()


async def _handle_acknowledgment_message(
    *, sender: str, message_text: str, conversation: Conversation, db: Session
) -> None:
    """Close naturally without reopening feedback or a completed complaint flow."""
    language, _ = resolve_reply_language(message_text)
    try:
        reply_text = await generate_closing_reply(
            message_text, getattr(conversation, "preferred_language", None) or language
        )
    except Exception:
        logger.exception("Failed to generate acknowledgment reply")
        reply_text = "You're welcome! Feel free to reach out if you need anything else."
    if guardrail_reply := apply_guardrails(message_text, reply_text):
        reply_text = guardrail_reply
    try:
        await whatsapp_client.send_text_message(to=sender, body=_sanitize_outbound_text(reply_text))
    except Exception:
        logger.exception("Failed to send acknowledgment reply to %s", sender)
        return
    db.add(Message(conversation_id=conversation.id, role="assistant", content=reply_text))
    db.commit()


@router.get("/webhook")
async def verify_webhook(
    mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    verify_token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
) -> Response:
    """Complete Meta's webhook verification handshake."""
    del mode
    if verify_token == settings.WHATSAPP_VERIFY_TOKEN and challenge is not None:
        return Response(content=challenge, media_type="text/plain")
    return Response(status_code=status.HTTP_403_FORBIDDEN)


@router.post("/webhook")
async def webhook_endpoint(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    """Dispatch Meta events to chat or calling without a second service."""
    raw_body = await request.body()
    payload = await request.json()
    if whatsapp_calling_client.has_call_event(payload):
        await whatsapp_calling_client.handle_call_webhook(
            payload,
            raw_body=raw_body,
            signature=request.headers.get("X-Hub-Signature-256"),
        )
        return {"status": "received", "channel": "call"}
    return await receive_webhook(payload, db)


async def receive_webhook(
    payload: dict[str, Any],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    """Process an inbound WhatsApp text event through the chat adapter."""
    try:
        value = payload["entry"][0]["changes"][0]["value"]
        messages = value.get("messages", [])
    except (IndexError, KeyError, TypeError):
        return {}

    if not messages:
        return {}

    inbound_message = messages[0]
    whatsapp_message_id = inbound_message.get("id")
    if not whatsapp_message_id:
        logger.warning("Ignoring inbound WhatsApp message without a Meta message id")
        return {}
    if whatsapp_message_id and db.scalar(
        select(Message).where(Message.whatsapp_message_id == whatsapp_message_id)
    ):
        logger.info("Ignoring duplicate inbound WhatsApp message %s", whatsapp_message_id)
        return {}
    try:
        await whatsapp_client.mark_as_read_and_typing(whatsapp_message_id)
    except Exception:
        logger.exception(
            "Failed to mark WhatsApp message %s as read and typing",
            whatsapp_message_id,
        )

    sender = inbound_message.get("from")
    contacts = value.get("contacts") or []
    profile_name = None
    if contacts and isinstance(contacts[0], dict):
        profile = contacts[0].get("profile")
        if isinstance(profile, dict):
            profile_name = profile.get("name")

    message_type = inbound_message.get("type")

    if message_type not in {"text", "interactive"}:
        if not sender:
            return {}

        _, conversation = _get_or_create_active_conversation(
            sender=sender, profile_name=profile_name, db=db
        )
        await _reply_unsupported_message(
            sender=sender,
            conversation=conversation,
            profile_name=profile_name,
            db=db,
            whatsapp_message_id=whatsapp_message_id,
            placeholder_content=f"[Unsupported message type: {message_type}]",
        )
        return {}

    interactive_reply_id = None
    if message_type == "interactive":
        list_reply = (inbound_message.get("interactive") or {}).get("list_reply") or {}
        interactive_reply_id = list_reply.get("id")
        message_text = list_reply.get("title") or interactive_reply_id
    else:
        message_text = (inbound_message.get("text") or {}).get("body")
    if not sender or not message_text:
        return {}

    user, conversation = _get_or_create_active_conversation(
        sender=sender, profile_name=profile_name, db=db
    )

    if message_type == "text" and is_emoji_only(message_text):
        await _reply_unsupported_message(
            sender=sender,
            conversation=conversation,
            profile_name=profile_name,
            db=db,
            whatsapp_message_id=whatsapp_message_id,
            placeholder_content="[Unsupported message: emoji-only]",
        )
        return {}

    db.add(
        Message(
            conversation_id=conversation.id,
            whatsapp_message_id=whatsapp_message_id,
            role="user",
            content=message_text,
        )
    )
    db.commit()

    if (guardrail_reply := apply_guardrails(message_text, "")):
        try:
            await whatsapp_client.send_text_message(
                to=sender, body=_sanitize_outbound_text(guardrail_reply)
            )
        except Exception:
            logger.exception("Failed to send guardrail reply to %s", sender)
        return {}

    if getattr(conversation, "complaint_collection_state", None):
        await _handle_complaint_collection(
            sender=sender,
            message_text=message_text,
            conversation=conversation,
            db=db,
        )
        return {}

    # A request for a contact number about an already-filed complaint is handled
    # before classification and independently of the current nested flow. General
    # customer-care lookups without a current or explicit complaint context continue
    # normally.
    if is_contact_request(message_text):
        tracking = get_latest_complaint_tracking(user.id, db)
        has_current_complaint_context = (
            tracking is not None
            and getattr(tracking, "conversation_id", None) == conversation.id
        )
        if tracking is not None and is_complaint_contact_followup(
            message_text,
            has_current_complaint_context=has_current_complaint_context,
        ):
            redirect_reply = complaint_contact_redirect_reply(tracking.token, message_text)
            try:
                await whatsapp_client.send_text_message(
                    to=sender, body=_sanitize_outbound_text(redirect_reply)
                )
            except Exception:
                logger.exception("Failed to send complaint tracking redirect to %s", sender)
                return {}
            db.add(
                Message(
                    conversation_id=conversation.id,
                    role="assistant",
                    content=redirect_reply,
                )
            )
            db.commit()
            return {}

    if message_type == "interactive":
        await _handle_interactive_reply(
            reply_id=interactive_reply_id or "",
            interactive_message_id=whatsapp_message_id,
            sender=sender,
            conversation=conversation,
            db=db,
        )
        return {}

    # An explicit language request is a control message, not a Metro question.
    # Persist it immediately and avoid sending it through classification or scope logic.
    requested_language = detect_language_switch_request(message_text)
    if requested_language:
        conversation.preferred_language = requested_language
        language_reply = generate_language_switch_confirmation(
            requested_language, detect_script(message_text)
        )
        db.commit()
        try:
            await whatsapp_client.send_text_message(
                to=sender, body=_sanitize_outbound_text(language_reply)
            )
        except Exception:
            logger.exception("Failed to confirm language preference to %s", sender)
            return {}
        db.add(
            Message(
                conversation_id=conversation.id,
                role="assistant",
                content=language_reply,
            )
        )
        db.commit()
        return {}

    # Exact casual greetings are resolved first. This makes their route mutually
    # exclusive with confusion recovery and resets a previously nested flow.
    if short_message_intent(message_text) == "greeting":
        logger.info("Routing WhatsApp message %s as deterministic greeting", whatsapp_message_id)
        await _handle_greeting_message(
            sender=sender,
            message_text=message_text,
            conversation=conversation,
            db=db,
        )
        return {}

    if short_message_intent(message_text) == "acknowledgment":
        logger.info("Routing WhatsApp message %s as deterministic acknowledgment", whatsapp_message_id)
        await _handle_acknowledgment_message(
            sender=sender,
            message_text=message_text,
            conversation=conversation,
            db=db,
        )
        return {}

    # A correction is a continuation, even if the message itself resembles a
    # short acknowledgement or would otherwise be classified as ambiguous.
    if correction := _correction_station_and_question(message_text, conversation, db):
        corrected_station, corrected_question = correction
        logger.info("Re-running station question with corrected station %s", corrected_station)
        message_text = corrected_question

    # Text is handled independently of any outstanding interactive list, so a
    # free-text confusion signal cannot be mistaken for a list-item selection.
    if is_confusion_message(message_text):
        logger.info("Routing WhatsApp message %s as confusion recovery", whatsapp_message_id)
        await _handle_confusion_message(
            sender=sender,
            message_text=message_text,
            conversation=conversation,
            db=db,
        )
        return {}

    # Planned Line 3 stations have deterministic handling. Do this before LLM
    # classification so an otherwise clear route request cannot fall into the
    # ambiguity menu or receive a hallucinated route.
    unsupported_stations = find_unsupported_station_names(message_text)
    if unsupported_stations:
        logger.info(
            "Routing WhatsApp message %s as an upcoming-station request: %s",
            whatsapp_message_id,
            unsupported_stations,
        )
        reply_text = generate_unsupported_station_reply(
            message_text, getattr(conversation, "preferred_language", None)
        )
        try:
            await whatsapp_client.send_text_message(
                to=sender, body=_sanitize_outbound_text(reply_text)
            )
        except Exception:
            logger.exception("Failed to send upcoming-station reply to %s", sender)
            return {}
        db.add(
            Message(
                conversation_id=conversation.id,
                role="assistant",
                content=reply_text,
            )
        )
        db.commit()
        return {}

    # Do not hand a station-shaped request with no verified station to the LLM.
    # It is safer to ask for a valid name than to let it autocomplete one.
    if is_station_or_route_question(message_text) and not find_station_names(message_text):
        reply_text = generate_unrecognized_station_reply(
            message_text, getattr(conversation, "preferred_language", None)
        )
        try:
            await whatsapp_client.send_text_message(
                to=sender, body=_sanitize_outbound_text(reply_text)
            )
        except Exception:
            logger.exception("Failed to send unrecognised-station reply to %s", sender)
            return {}
        db.add(Message(conversation_id=conversation.id, role="assistant", content=reply_text))
        db.commit()
        return {}

    pending_category = getattr(conversation, "pending_category", None)
    # This service is also used after an LLM answer is generated.  Initialising
    # it only inside the cache-read branch caused an UnboundLocalError whenever
    # that branch was skipped (for example, during an active collection).
    qa_cache_service = QACacheService(db)
    # Existing FAQ entries are known-safe enquiry answers. Check them before the
    # remote classifier so a true hit avoids every LLM request for this turn.
    if pending_category is None and not getattr(conversation, "complaint_collection_state", None):
        cache_language = (
            getattr(conversation, "preferred_language", None)
            or resolve_reply_language(message_text)[0]
        )
        cached_entry = qa_cache_service.get_cached_entry(message_text, cache_language)
        if cached_entry and reply_has_only_canonical_station_names(cached_entry.answer):
            cached_answer = cached_entry.answer
            db.add(make_cache_log(conversation_id=conversation.id, channel="chat",
                                  question=message_text, answer=cached_answer,
                                  cache_entry_id=cached_entry.id))
            db.add(Message(conversation_id=conversation.id, role="assistant", content=cached_answer))
            db.commit()
            try:
                await whatsapp_client.send_text_message(
                    to=sender, body=_sanitize_outbound_text(cached_answer)
                )
            except Exception:
                logger.exception("Failed to send cached WhatsApp reply to %s", sender)
            return {}
    classification = await classify_message(message_text, pending_category)
    logger.info("Full classification for %r: %s", message_text, classification)
    intent = classification["intent"]
    
    if intent == "ambiguous" or not classification["classification_confident"]:
        logger.info("Routing WhatsApp message %s as LLM-unclear", whatsapp_message_id)
        await _handle_confusion_message(
            sender=sender,
            message_text=message_text,
            conversation=conversation,
            db=db,
        )
        return {}

    if intent == "out_of_scope":
        # A clear out-of-scope turn also breaks a prior unclear-message cycle.
        if getattr(conversation, "confusion_handoff_shown", False):
            conversation.confusion_handoff_shown = False
            conversation.unclear_streak_count = 0
            db.commit()
        try:
            await whatsapp_client.send_text_message(
                to=sender,
                body=_sanitize_outbound_text(
                    generate_out_of_scope_reply(
                        message_text,
                        classification["detected_language"],
                        detect_script(message_text),
                    )
                ),
            )
        except Exception:
            logger.exception("Failed to send out-of-scope reply to %s", sender)
        return {}

    # A successful non-ambiguous turn breaks any prior post-handoff recovery cycle.
    if getattr(conversation, "confusion_handoff_shown", False):
        conversation.confusion_handoff_shown = False
        conversation.unclear_streak_count = 0
        db.commit()
    if pending_category is None and intent == "greeting":
        await _handle_greeting_message(
            sender=sender,
            message_text=message_text,
            conversation=conversation,
            db=db,
        )
        return {}

    if pending_category is None and intent == "acknowledgment":
        await _handle_acknowledgment_message(
            sender=sender,
            message_text=message_text,
            conversation=conversation,
            db=db,
        )
        return {}

    is_first_assistant_reply = _is_first_assistant_reply(conversation.id, db)
    extracted_details = classification["extracted_details"]
    detected_language = classification["detected_language"]
    passenger_name = extracted_details["passenger_name"] or profile_name
    categories = [
        canonical
        for item in classification["categories"]
        if (canonical := normalize_category(item))
    ]
    if pending_category and pending_category not in categories:
        categories = [pending_category, *categories]
    
    if any(category in categories for category in ("complaint", "suggestion", "appreciation")):
        await _initiate_complaint_collection(
            sender=sender,
            category=next(
                category for category in ("complaint", "suggestion", "appreciation")
                if category in categories
            ),
            initial_message=message_text,
            conversation=conversation,
            db=db,
        )
        return {}

    category_log = CategoryLog(
        user_id=user.id,
        conversation_id=conversation.id,
        categories=categories,
        subcategory=",".join(classification["subcategories"]) or None,
        message=message_text,
    )
    db.add(category_log)
    db.flush()

    ticket_details = {
        "metro_station": extracted_details["metro_station"],
        "ticket_number": extracted_details["ticket_number"],
        "payment_method": extracted_details["payment_method"],
        "passenger_name": passenger_name,
    }
    if any(ticket_details.values()):
        db.add(TicketDetails(category_log_id=category_log.id, **ticket_details))
    if pending_category:
        conversation.pending_category = None
    db.commit()

    recent_messages = list(
        db.scalars(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(20)
        )
    )
    conversation_history = [
        {"role": message.role, "content": message.content}
        for message in reversed(recent_messages)
    ]
    reference_topics = classification["reference_topics"]
    if is_station_or_route_question(message_text) and "stations" not in reference_topics:
        reference_topics = [*reference_topics, "stations"]
    reference_data = load_reference_data(reference_topics)
    station_names = find_station_names(message_text)
    if len(station_names) >= 2:
        route_grounding = build_route_grounding(station_names[0], station_names[1])
        if route_grounding:
            # Route topology and fares come from deterministic local data, not
            # the model's general knowledge or a free-form interpretation.
            reference_data = "\n\n".join(filter(None, [reference_data, route_grounding]))
    # The official, code-labelled matrix in fares.md is supplied directly to the
    # model. Do not override it with the older JSON fare cache.
    fare_estimate = None
    asks_about_complaint_status = (
        classification["asking_about_complaint_status"]
        or any(keyword in message_text.casefold() for keyword in COMPLAINT_STATUS_KEYWORDS)
    )
    complaint_status_context = None
    if asks_about_complaint_status:
        complaint_status_context = check_complaint_status(user.id, db)
        if complaint_status_context is None:
            complaint_status_context = "No complaint is currently on record for this user."
    
    brain_response = await respond_with_legacy_context(
        BrainRequest(
            user_id=user.id,
            user_identity=sender,
            channel="chat",
            text=message_text,
            conversation_id=conversation.id,
            preferred_language=detected_language,
            history=[
                BrainMessage(role=item["role"], content=item["content"])
                for item in conversation_history
                if item["role"] in {"user", "assistant"}
            ],
        ),
        generator=generate_reply,
        reference_data=reference_data,
        fare_context=fare_estimate,
        complaint_status_context=complaint_status_context,
        reference_topics=reference_topics,
    )
    reply_text = brain_response.reply_text
    if is_first_assistant_reply:
        reply_text = _with_first_reply_greeting(
            reply_text, detected_language, detect_script(message_text)
        )

    if not reply_has_only_canonical_station_names(reply_text):
        logger.warning("Blocked reply containing an ungrounded station name: %r", reply_text)
        reply_text = generate_unrecognized_station_reply(
            message_text, getattr(conversation, "preferred_language", None)
        )

    if (guardrail_reply := apply_guardrails(message_text, reply_text)):
        reply_text = guardrail_reply

    db.add(make_llm_log(conversation_id=conversation.id, channel="chat",
                        question=message_text, answer=reply_text))

    if "enquiry" in categories:
        qa_cache_service.store_answer(message_text, reply_text, detected_language, "enquiry")

    try:
        await whatsapp_client.send_text_message(
            to=sender, body=_sanitize_outbound_text(reply_text)
        )
    except Exception:
        logger.exception("Failed to send WhatsApp reply to %s", sender)
        return {}

    db.add(
        Message(
            conversation_id=conversation.id,
            role="assistant",
            content=reply_text,
        )
    )
    db.commit()

    return {}


async def _initiate_complaint_collection(
    *, sender: str, category: str, initial_message: str, conversation: Conversation, db: Session
) -> None:
    """Start the complaint/suggestion collection flow."""
    language, script = resolve_reply_language(initial_message)
    prompt = start_collection(
        conversation,
        category,
        initial_message,
        language=language,
    )
    db.commit()
    if guardrail_reply := apply_guardrails(initial_message, prompt):
        prompt = guardrail_reply
    try:
        await whatsapp_client.send_text_message(to=sender, body=_sanitize_outbound_text(prompt))
    except Exception:
        logger.exception("Failed to send complaint collection prompt to %s", sender)


async def _handle_complaint_collection(
    *, sender: str, message_text: str, conversation: Conversation, db: Session
) -> None:
    """Handle the step-by-step collection of complaint/suggestion details."""
    state = getattr(conversation, "complaint_collection_state", None)

    category = getattr(conversation, "pending_category", None) or "complaint"
    language, script = resolve_reply_language(message_text)
    if not getattr(conversation, "preferred_language", None):
        conversation.preferred_language = language
    prompt, completed = advance_collection(conversation, message_text, db)
    db.commit()
    if guardrail_reply := apply_guardrails(message_text, prompt):
        prompt = guardrail_reply
    try:
        await whatsapp_client.send_text_message(
            to=sender, body=_sanitize_outbound_text(prompt)
        )
    except Exception:
        logger.exception("Failed to send complaint collection prompt to %s", sender)
    db.add(Message(conversation_id=conversation.id, role="assistant", content=prompt))
    db.commit()
    if completed and re.search(r"PMC-\d{6}", prompt):
        await _request_feedback(sender, conversation, db)
    return

    if state == "collecting_name":
        if _is_collection_non_answer(message_text):
            prompt = "Please share your full name so I can continue."
            if guardrail_reply := apply_guardrails(message_text, prompt):
                prompt = guardrail_reply
            db.commit()
            await whatsapp_client.send_text_message(to=sender, body=_sanitize_outbound_text(prompt))
            return
        conversation.complaint_collection_full_name = message_text
        conversation.complaint_collection_state = "collecting_contact"
        next_field = "contact number"
        if state == "collecting_name" and conversation.complaint_collection_state != "confirming":
            try:
                prompt = await generate_collection_prompt(category, next_field, language, script)
            except Exception:
                prompt = f"Please share your {next_field}."
    elif state == "collecting_contact":
        contact_number = _valid_contact_number(message_text)
        if contact_number is None:
            prompt = "Please enter a valid contact number using 7 to 15 digits."
            if guardrail_reply := apply_guardrails(message_text, prompt):
                prompt = guardrail_reply
            db.commit()
            await whatsapp_client.send_text_message(to=sender, body=_sanitize_outbound_text(prompt))
            return
        conversation.complaint_collection_contact_number = contact_number
        conversation.complaint_collection_state = "collecting_station"
        try:
            prompt = await generate_collection_prompt(category, "station or location", language, script)
        except Exception:
            prompt = "Please share the station or location."
    elif state == "collecting_station":
        station = _collection_station(message_text)
        if station is None:
            prompt = (
                "I couldn't match that station. Please send the Pune Metro station name, "
                "for example PCMC, District Court/Civil Court, Vanaz, or Swargate."
            )
            if guardrail_reply := apply_guardrails(message_text, prompt):
                prompt = guardrail_reply
            db.commit()
            await whatsapp_client.send_text_message(to=sender, body=_sanitize_outbound_text(prompt))
            return
        conversation.complaint_collection_station = station
        if getattr(conversation, "complaint_collection_description", None):
            conversation.complaint_collection_state = "confirming"
            prompt = _collection_confirmation_prompt(conversation)
        else:
            conversation.complaint_collection_state = "collecting_description"
            try:
                prompt = await generate_collection_prompt(category, "what happened", language, script)
            except Exception:
                prompt = "Please describe what happened."
    elif state == "collecting_description":
        if not _is_valid_description(message_text):
            original = getattr(conversation, "complaint_collection_description", None)
            if original:
                conversation.complaint_collection_state = "confirming"
                prompt = _collection_confirmation_prompt(conversation)
            else:
                prompt = "Please describe the issue or suggestion with a little more detail."
            if guardrail_reply := apply_guardrails(message_text, prompt):
                prompt = guardrail_reply
            db.commit()
            await whatsapp_client.send_text_message(to=sender, body=_sanitize_outbound_text(prompt))
            return
        conversation.complaint_collection_description = message_text
        conversation.complaint_collection_state = "confirming"
        prompt = _collection_confirmation_prompt(conversation)
    elif state == "confirming":
        if message_text.casefold().strip(" .!?।") in {"yes", "yeah", "yep", "हो", "हां", "हाँ", "जी हाँ", "करा", "नोंदवा"}:
            await _create_complaint_from_collection(sender, conversation, db)
            return
        _clear_complaint_collection(conversation)
        db.commit()
        prompt = "Okay, I've cancelled the process. How else can I help you?"
    else:
        # Fallback for unexpected state
        conversation.complaint_collection_state = None
        prompt = "Sorry, something went wrong. Let's start over. How can I help?"

    db.commit()
    if guardrail_reply := apply_guardrails(message_text, prompt):
        prompt = guardrail_reply
    try:
        await whatsapp_client.send_text_message(to=sender, body=_sanitize_outbound_text(prompt))
    except Exception:
        logger.exception("Failed to send complaint collection prompt to %s", sender)


def _clear_complaint_collection(conversation: Conversation) -> None:
    """Clear every persisted field belonging to the complaint collection flow."""
    conversation.complaint_collection_state = None
    conversation.complaint_collection_full_name = None
    conversation.complaint_collection_contact_number = None
    conversation.complaint_collection_station = None
    conversation.complaint_collection_description = None
    conversation.pending_category = None


async def _create_complaint_from_collection(
    sender: str, conversation: Conversation, db: Session
) -> None:
    """Create a complaint or suggestion from the collected details."""
    category = getattr(conversation, "pending_category", None)
    message = (
        f"Name: {getattr(conversation, 'complaint_collection_full_name', '')}\n"
        f"Contact: {getattr(conversation, 'complaint_collection_contact_number', '')}\n"
        f"Station: {getattr(conversation, 'complaint_collection_station', '')}\n"
        f"Description: {getattr(conversation, 'complaint_collection_description', '')}"
    )
    category_log = CategoryLog(
        user_id=conversation.user_id,
        conversation_id=conversation.id,
        categories=[category],
        message=message,
    )
    db.add(category_log)
    db.flush()
    db.add(TicketDetails(
        category_log_id=category_log.id,
        metro_station=getattr(conversation, "complaint_collection_station", None),
        passenger_name=getattr(conversation, "complaint_collection_full_name", None),
    ))

    tracking = None
    if category in {"complaint", "suggestion"}:
        tracking = create_complaint_tracking(
            category_log=category_log,
            user_id=conversation.user_id,
            conversation_id=conversation.id,
            db=db,
            category=category,
        )
    # Ticket creation is a terminal collection state. Clear it before any later
    # feedback interaction can receive another user message.
    _clear_complaint_collection(conversation)
    db.commit()

    if category == "complaint" and tracking is not None:
        reply_text = complaint_confirmation_reply(tracking.token, message, "english")
    elif category == "suggestion" and tracking is not None:
        reply_text = suggestion_confirmation_reply(tracking.token, message, "english")
    else:
        reply_text = "Your appreciation has been recorded. Thank you for sharing it with us."

    try:
        await whatsapp_client.send_text_message(to=sender, body=reply_text)
    except Exception:
        logger.exception("Failed to send complaint confirmation to %s", sender)
    
    db.add(
        Message(
            conversation_id=conversation.id,
            role="assistant",
            content=reply_text,
        )
    )
    db.commit()
    await _request_feedback(sender, conversation, db)


async def _request_feedback(sender: str, conversation: Conversation, db: Session) -> None:
    """Ask the user for feedback on the conversation."""
    if getattr(conversation, "is_closed", False):
        return

    conversation.is_closed = True
    db.commit()

    rows = [
        {"id": f"feedback:5", "title": "5 ★"},
        {"id": f"feedback:4", "title": "4 ★"},
        {"id": f"feedback:3", "title": "3 ★"},
        {"id": f"feedback:2", "title": "2 ★"},
        {"id": f"feedback:1", "title": "1 ★"},
    ]
    await whatsapp_client.send_interactive_list(
        to=sender,
        header="Rate your experience",
        body="How would you rate your experience with the Pune Metro assistant?",
        button_text="Rate",
        sections=_sections("Rating", rows),
    )


async def _handle_feedback_reply(
    *,
    reply_id: str,
    sender: str,
    conversation: Conversation,
    db: Session,
) -> None:
    """Handle the user's feedback rating."""
    rating = int(reply_id.removeprefix("feedback:"))
    conversation.feedback_rating = rating
    # Feedback is another terminal exit. The collection state is explicitly
    # cleared here as well to protect older or partially completed conversations.
    _clear_complaint_collection(conversation)
    db.commit()

    await whatsapp_client.send_text_message(
        to=sender,
        body="Thank you for your feedback! If you have any more comments, please type them now.",
    )
