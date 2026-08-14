"""Streaming Pipecat pipeline that adapts WhatsApp calls to the shared brain."""

import asyncio
import hashlib
import logging
import re
import time
import types
import wave
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.core.config import settings
from app.db.models import CallSession, Conversation, Message, User, ResponseSourceLog, TTSAudioCache
from app.db.session import SessionLocal
from app.services.brain import respond
from app.services.brain_models import BrainMessage, BrainRequest
from app.services.qa_cache import QACacheService
from app.services.cost_tracking import make_cache_log, make_llm_log, make_tts_log
from app.services.voice_agent import voice_agent
from app.services.collection_flow import (
    advance_collection,
    collection_resume_reply,
    compact_voice_confirmation,
    is_additional_collection_detail,
    start_collection,
)
from app.services.whatsapp_client import whatsapp_client
from app.services.llm_client import (
    FARE_MATRIX,
    OPERATIONAL_LINES,
    build_greeting_reply,
    classify_message,
    find_station_names,
    find_unsupported_station_names,
    generate_unsupported_station_reply,
    reply_has_only_canonical_station_names,
    resolve_reply_language,
    short_message_intent,
)


logger = logging.getLogger(__name__)
END_MARKER = "[END_CALL]"
GREETING_TEXT = (
    "नमस्कार! सेवा गुणवत्ता आणि नोंदीसाठी ही कॉल रेकॉर्ड केली जाऊ शकते. "
    "मी पुणे मेट्रोची सहाय्यक बोलते. मी मराठी, हिंदी आणि इंग्रजी समजू शकते. "
    "मी तुम्हाला कशी मदत करू?"
)
MIN_TRANSCRIPTION_CONFIDENCE = 0.60
# Collection fields are often names, station names, and spoken digits, which STT
# scores lower than full sentences. Keep them usable while state-specific
# validation prevents unrelated speech from mutating confirmed data.
COLLECTION_TRANSCRIPTION_CONFIDENCE = 0.35
# Keep this short: VAD handles natural pauses before STT finalizes an utterance.
# This debounce only joins back-to-back final STT frames and must not make the
# caller wait before every response.
TURN_AGGREGATION_DELAY_SECONDS = 0.65
VAD_STOP_SECS = 0.8
SUPPORTED_STT_LANGUAGES = frozenset({"en-IN", "hi-IN", "mr-IN"})
TTS_LANGUAGE_TAG = "\u2063"


def _build_vad_analyzer() -> Any:
    """Build the configured analyzer in both preload and live-call paths."""
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.audio.vad.vad_analyzer import VADParams

    return SileroVADAnalyzer(params=VADParams(stop_secs=VAD_STOP_SECS))


def preload_voice_pipeline_dependencies() -> None:
    """Load the slow audio stack before the service reports calling ready.

    Importing Torch/Silero on the first live call can take minutes on a small
    host.  Doing that work during application startup keeps the WebRTC callback
    fast enough for the caller to hear the greeting.
    """
    started_at = time.monotonic()
    from pipecat.pipeline.pipeline import Pipeline  # noqa: F401
    from pipecat.pipeline.runner import PipelineRunner  # noqa: F401
    from pipecat.pipeline.task import PipelineTask  # noqa: F401
    from pipecat.processors.audio.vad_processor import VADProcessor  # noqa: F401
    from pipecat.services.sarvam.stt import SarvamSTTService  # noqa: F401
    from pipecat.services.sarvam.tts import SarvamTTSService  # noqa: F401
    from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport  # noqa: F401

    # Construct once so the model file is loaded and validated while starting.
    _build_vad_analyzer()
    logger.info(
        "Voice pipeline dependencies preloaded in %.2fs",
        time.monotonic() - started_at,
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _tts_language(text: str) -> str:
    devanagari = bool(re.search(r"[\u0900-\u097F]", text))
    if not devanagari:
        return "english"
    marathi_markers = (
        "आहे",
        "आहेत",
        "किती",
        "कुठे",
        "तुम्ही",
        "तुम्हाला",
        "मला",
        "मी ",
        "कशी",
        "कसा",
        "जायचं",
        "जाऊ",
        "समजलं",
        "मराठी",
        "तिथे",
        "तुमची",
        "सध्याची",
        "लाईनवरून",
        "उतरून",
        "म्हणा",
        "स्थानकावर",
    )
    hindi_markers = (
        "वहाँ",
        "आपको",
        "आपकी",
        "जाइए",
        "बताइए",
        "चढ़ें",
        "उतरकर",
        "लाइन से",
        "लाइन पर",
        "शिकायत",
        "जारी रखें",
    )
    marathi_score = sum(marker in text for marker in marathi_markers)
    hindi_score = sum(marker in text for marker in hindi_markers)
    return "marathi" if marathi_score > hindi_score else "hindi"


def _transcription_language_code(frame: Any) -> str | None:
    """Read Sarvam's language code without depending on one Pipecat version."""
    result = getattr(frame, "result", None)
    data = result.get("data") if isinstance(result, dict) else None
    if isinstance(data, dict):
        value = data.get("language_code")
        return str(value) if value else None
    value = getattr(data, "language_code", None)
    return str(value) if value else None


def _reply_language_from_stt(code: str | None, text: str) -> str:
    mapping = {"en-IN": "english", "hi-IN": "hindi", "mr-IN": "marathi"}
    return mapping.get(code, resolve_reply_language(text)[0])


def _tag_tts_text(text: str, language: str) -> str:
    safe_language = language if language in {"english", "hindi", "marathi"} else "english"
    return f"{TTS_LANGUAGE_TAG}{safe_language}{TTS_LANGUAGE_TAG}{text}"


def _parse_tts_text(text: str) -> tuple[str, str]:
    if text.startswith(TTS_LANGUAGE_TAG):
        _, language, clean = text.split(TTS_LANGUAGE_TAG, 2)
        if language in {"english", "hindi", "marathi"}:
            return clean, language
    return text, _tts_language(text)


def _spoken_reply_language(text: str, fallback: str) -> str:
    """Configure TTS from generated reply text, not a station-only STT label."""
    if re.search(r"[\u0900-\u097F]", text):
        return _tts_language(text)
    return fallback if fallback in {"english", "hindi", "marathi"} else "english"


def _sanitize_voice_reply(text: str) -> str:
    """Make provider output sound like a call even when it emits Markdown."""
    clean = re.sub(r"(?m)^\s*[-*#]+\s*", "", text)
    clean = re.sub(r"[*_`#]", "", clean)
    clean = re.sub(r"\s*\n+\s*", " ", clean)
    return re.sub(r"\s{2,}", " ", clean).strip()


def _sanitize_reply_with_end_marker(text: str) -> str:
    """Sanitize spoken text while preserving the internal call-control marker."""
    should_end_call = END_MARKER in text
    clean = _sanitize_voice_reply(text.replace(END_MARKER, ""))
    return f"{clean} {END_MARKER}" if should_end_call else clean


def _set_call_state(call_session_id: int, status: str, **values: Any) -> None:
    with SessionLocal() as db:
        call = db.get(CallSession, call_session_id)
        if call is None:
            return
        call.status = status
        for key, value in values.items():
            setattr(call, key, value)
        db.commit()


def _transcription_confidence(frame: Any) -> float | None:
    result = getattr(frame, "result", None)
    if not isinstance(result, dict):
        return None
    data = result.get("data")
    if not isinstance(data, dict):
        return None
    probability = data.get("language_probability")
    try:
        return float(probability) if probability is not None else None
    except (TypeError, ValueError):
        return None


def _mentions_planned_line(text: str) -> bool:
    """Return whether a recent turn establishes the planned Line 3 topic."""
    normalized = text.casefold()
    return bool(
        re.search(
            r"hinj[ae]wadi|hinjavadi|shivaji\s*nagar.{0,25}(?:line|corridor)|"
            r"line\s*3|planned station|still under construction|"
            r"(हिंजवडी|हिंजेवाडी|हिंजवाडी|शिवाजीनगर).{0,30}"
            r"(मेट्रो|लाईन|कॉरिडॉर|मार्ग)|"
            r"बांधकामाधीन|निर्माणाधीन",
            normalized,
        )
    )


def _planned_line_followup_reply(
    text: str, *, context_active: bool
) -> tuple[str, str] | None:
    """Resolve pronoun-based Line 3 follow-ups without asking route fields."""
    normalized = text.casefold()
    language, _script = resolve_reply_language(text)
    unsupported = find_unsupported_station_names(text)
    asks_about_baner_stop = "Baner" in unsupported and bool(
        re.search(
            r"\b(stop|station|halt)\b|(थांब|स्टेशन|स्थानक|रुक|रुके)",
            normalized,
        )
    )
    if asks_about_baner_stop:
        replies = {
            "english": (
                "Yes. Baner is a planned station on the Hinjewadi–Shivajinagar "
                "corridor. The line is still under construction, so Baner station "
                "is not open yet."
            ),
            "hindi": (
                "हाँ। बाणेर हिंजवडी–शिवाजीनगर मार्ग का एक प्रस्तावित स्टेशन है। "
                "यह लाइन अभी निर्माणाधीन है, इसलिए बाणेर स्टेशन अभी खुला नहीं है।"
            ),
            "marathi": (
                "हो. बाणेर हे हिंजवडी–शिवाजीनगर मार्गावरील नियोजित स्थानक आहे. "
                "ही लाईन सध्या बांधकामाधीन असल्यामुळे बाणेर स्थानक अजून सुरू झालेले नाही."
            ),
        }
        return replies.get(language, replies["english"]), language

    asks_about_travel_now = bool(
        re.search(
            r"\b(today|now|currently)\b|(आज|अभी|सध्या|आता)", normalized
        )
        and re.search(
            r"\b(travel|ride|use|go)\b|(प्रवास|जाऊ|जाऊँ|यात्रा|सफर)",
            normalized,
        )
    )
    refers_to_current_line = bool(
        re.search(
            r"\b(it|this line|that line|the line|these lines|this metro|that metro)\b|"
            r"(ही लाईन|या लाईन|ह्या लाईन|इस लाइन|उस लाइन)",
            normalized,
        )
    )
    if context_active and asks_about_travel_now and refers_to_current_line:
        replies = {
            "english": (
                "No, not yet. The Hinjewadi–Shivajinagar Line 3 is still under "
                "construction and is not open for passenger travel today."
            ),
            "hindi": (
                "नहीं, अभी नहीं। हिंजवडी–शिवाजीनगर लाइन 3 अभी निर्माणाधीन है और "
                "आज यात्री सेवा के लिए खुली नहीं है।"
            ),
            "marathi": (
                "नाही, अजून नाही. हिंजवडी–शिवाजीनगर लाईन 3 सध्या बांधकामाधीन आहे "
                "आणि आज प्रवासी सेवेसाठी सुरू नाही."
            ),
        }
        return replies.get(language, replies["english"]), language
    return None


def _fast_voice_reply(
    text: str, *, planned_line_context: bool = False
) -> tuple[str, str] | None:
    """Answer simple greetings/closings without two remote LLM round trips."""
    planned_followup = _planned_line_followup_reply(
        text, context_active=planned_line_context
    )
    if planned_followup is not None:
        return planned_followup
    normalized = " ".join(text.casefold().strip("!?.,;:।").split())
    language, _script = resolve_reply_language(text)
    if re.search(
        r"\b(?:are you there|can you hear me|hello are you there)\b|"
        r"(?:आहेत का तुम्ही|ऐकू येतंय का|तुम्ही ऐकताय का)|"
        r"(?:सुन रही हैं|सुन रहे हैं|आवाज़ आ रही है)",
        normalized,
    ):
        replies = {
            "english": "Yes, I’m here and listening. How can I help with Pune Metro?",
            "hindi": "जी, मैं सुन रही हूँ। पुणे मेट्रो के बारे में मैं आपकी कैसे मदद करूँ?",
            "marathi": "हो, मी ऐकतेय. पुणे मेट्रोबद्दल मी तुम्हाला कशी मदत करू?",
        }
        return replies.get(language, replies["english"]), language
    if re.search(
        r"\b(?:what does destination mean|meaning of destination)\b|"
        r"(?:गंतव्य|दंतव्य).{0,12}(?:म्हणजे|क्या|मतलब)",
        normalized,
    ):
        replies = {
            "english": "Destination means the station you want to travel to.",
            "hindi": "गंतव्य का मतलब वह स्टेशन है जहाँ आप जाना चाहते हैं।",
            "marathi": "गंतव्य म्हणजे ज्या स्थानकावर तुम्हाला जायचं आहे ते स्थानक.",
        }
        return replies.get(language, replies["english"]), language
    if common_reply := voice_agent.common_information_reply(text):
        return common_reply
    if platform_reply := voice_agent.platform_guidance_reply(text):
        return platform_reply
    if find_unsupported_station_names(text):
        language, _script = resolve_reply_language(text)
        return generate_unsupported_station_reply(text, language), language
    route_clarification = _route_clarification(text)
    if route_clarification is not None:
        return route_clarification
    stations = find_station_names(text)
    if len(stations) >= 2:
        language, _script = resolve_reply_language(text)
        return _voice_route_reply(stations[0], stations[1], language), language
    intent = short_message_intent(text)
    if intent is None and len(text.split()) <= 10 and re.match(
        r"^(hello|hi|hey|नमस्कार|नमस्ते|हेलो|हॅलो)\b", text, re.I
    ):
        intent = "greeting"
    if intent is None:
        return None
    language, script = resolve_reply_language(text)
    if intent == "greeting":
        return build_greeting_reply(text, language, script), language
    closings = {
        "english": "You're welcome. Is there anything else about Pune Metro I can help with?",
        "hindi": "आपका स्वागत है। क्या मैं पुणे मेट्रो के बारे में आपकी और मदद कर सकती हूँ?",
        "marathi": "तुमचं स्वागत आहे. पुणे मेट्रोबद्दल आणखी काही मदत करू का?",
    }
    return closings.get(language, closings["english"]), language


def _is_explicit_thank_you(text: str) -> bool:
    normalized = " ".join(text.casefold().strip("!?.,;:").split())
    if re.search(r"\b(?:thanks|thank\s*(?:you|u)|thankyou)\b", normalized):
        return True
    return any(
        phrase in normalized
        for phrase in (
            "धन्यवाद",
            "शुक्रिया",
            "थँक्यू",
            "थैंक यू",
            "थैंक्यू",
            "थॅंक्यू",
        )
    )


def _is_no_more_enquiry(text: str, *, allow_bare_negative: bool = False) -> bool:
    """Detect when the caller clearly says they need no further assistance."""
    normalized = " ".join(text.casefold().strip("!?.,;:।").split())
    explicit_closings = (
        "that's all", "that is all", "nothing else", "no more", "i'm done", "im done",
        "बस इतना ही", "और कुछ नहीं", "कोई और सवाल नहीं", "बस हो गया",
        "अभी कुछ नहीं", "फिलहाल कुछ नहीं",
        "एवढंच", "इतकंच", "आणखी काही नाही", "सध्या काही नाही",
        "सध्या तर काही नाही", "आत्ता काही नाही", "आता काही नाही",
        "माझं झालं", "बस झालं",
    )
    bare_negatives = {"no", "nope", "नहीं", "नही", "नाही", "नको"}
    return (
        _is_explicit_thank_you(text)
        or any(phrase in normalized for phrase in explicit_closings)
        or (allow_bare_negative and normalized in bare_negatives)
    )


def _offered_more_help(text: str) -> bool:
    normalized = text.casefold()
    return any(
        phrase in normalized
        for phrase in (
            "anything else", "any other pune metro", "what else can i help",
            "before we finish", "i can help plan", "और मदद", "और कोई", "और किस बात",
            "कॉल खत्म करने से पहले", "आणखी काही", "अजून काही", "आणखी कशात",
            "कॉल संपवण्यापूर्वी", "मार्ग नियोजित करण्यात मी मदत",
            "मार्ग तय करने में मदद",
            "still under construction", "अभी निर्माणाधीन", "सध्या बांधकामाधीन",
        )
    )


def _offer_more_help(reply: str, language: str) -> str:
    """End completed answers with a natural invitation for another enquiry."""
    normalized = reply.casefold()
    asks_caller = any(
        phrase in normalized
        for phrase in (
            "please tell", "could you tell", "what is", "which station",
            "बताइए", "बताएँ", "कौन सा", "कौन-सा", "क्या है",
            "सांगा", "सांगाल", "कोणतं", "कोणता", "काय आहे",
        )
    )
    if "?" in reply or asks_caller or _offered_more_help(reply):
        return reply
    offers = {
        "english": (
            "Is there anything else about Pune Metro I can help you with?",
            "What else can I help you with today?",
            "Before we finish, is there anything else you'd like to ask?",
        ),
        "hindi": (
            "क्या पुणे मेट्रो के बारे में मैं आपकी और कोई मदद कर सकती हूँ?",
            "आज मैं आपकी और किस बात में मदद कर सकती हूँ?",
            "कॉल खत्म करने से पहले, क्या आप कुछ और पूछना चाहेंगे?",
        ),
        "marathi": (
            "पुणे मेट्रोबद्दल मी तुम्हाला आणखी काही मदत करू का?",
            "आज मी तुम्हाला आणखी कशात मदत करू शकते?",
            "कॉल संपवण्यापूर्वी तुम्हाला आणखी काही विचारायचं आहे का?",
        ),
    }
    choices = offers.get(language, offers["english"])
    index = int(hashlib.sha256(reply.encode("utf-8")).hexdigest()[:8], 16) % len(choices)
    return f"{reply} {choices[index]}"


def _continue_call_after_collection(reply: str, language: str) -> str:
    """Invite another independent task after one collection is safely saved."""
    continuations = {
        "english": (
            "You can now share another complaint, suggestion, appreciation, or "
            "ask a Pune Metro question in this same call."
        ),
        "hindi": (
            "अब आप इसी कॉल में दूसरी शिकायत, सुझाव, प्रशंसा या पुणे मेट्रो से जुड़ी "
            "पूछताछ बता सकते हैं।"
        ),
        "marathi": (
            "आता तुम्ही याच कॉलमध्ये दुसरी तक्रार, सूचना, कौतुक किंवा पुणे मेट्रोबद्दल "
            "चौकशी सांगू शकता."
        ),
    }
    return f"{reply} {continuations.get(language, continuations['english'])}"


def _call_closing_reply(
    text: str, language: str, *, allow_bare_negative: bool = False
) -> str | None:
    """Return a polite farewell only when the caller indicates they are done."""
    if not _is_no_more_enquiry(text, allow_bare_negative=allow_bare_negative):
        return None
    farewells = {
        "english": "You're welcome. Thanks for calling Pune Metro. Goodbye.",
        "hindi": "आपका स्वागत है। पुणे मेट्रो को कॉल करने के लिए धन्यवाद। नमस्कार।",
        "marathi": "तुमचं स्वागत आहे. पुणे मेट्रोला कॉल केल्याबद्दल धन्यवाद. नमस्कार.",
    }
    return f"{farewells.get(language, farewells['english'])} {END_MARKER}"


def _voice_route_reply(origin: str, destination: str, language: str) -> str:
    origin_line = next(line for line, items in OPERATIONAL_LINES.items() if origin in items)
    destination_line = next(
        line for line, items in OPERATIONAL_LINES.items() if destination in items
    )
    fare = FARE_MATRIX.get(origin, {}).get(destination)
    direct = origin_line == destination_line
    if language == "marathi":
        if direct:
            answer = f"{origin} येथून {origin_line} ने थेट {destination} येथे जा."
        else:
            answer = (
                f"{origin} येथून {origin_line} ने डिस्ट्रिक्ट कोर्टला जा. तिथे "
                f"{destination_line} बदला आणि {destination} पर्यंत जा."
            )
        return f"{answer} तिकीट {fare} रुपये आहे." if isinstance(fare, int) else answer
    if language == "hindi":
        if direct:
            answer = f"{origin} से {origin_line} लेकर सीधे {destination} जाइए।"
        else:
            answer = (
                f"{origin} से {origin_line} लेकर डिस्ट्रिक्ट कोर्ट जाइए। वहाँ "
                f"{destination_line} बदलकर {destination} तक जाइए।"
            )
        return f"{answer} टिकट {fare} रुपये है।" if isinstance(fare, int) else answer
    if direct:
        answer = f"Take the {origin_line} directly from {origin} to {destination}."
    else:
        answer = (
            f"Take the {origin_line} from {origin} to District Court. Change to the "
            f"{destination_line} there and continue to {destination}."
        )
    return f"{answer} The fare is ₹{fare}." if isinstance(fare, int) else answer


def _is_incomplete_voice_fragment(text: str) -> bool:
    normalized = " ".join(text.casefold().strip("!?.,;:").split())
    if normalized in {
        "तर मला", "मला", "तो मुझे", "मुझे", "so i", "i want to", "from", "to",
        "ते", "पासून", "वरून", "से", "तक",
    }:
        return True
    # Sarvam can finalize a long utterance at a hesitation. A dangling connector
    # is not a complete request and otherwise causes an unnecessary LLM turn.
    return bool(
        re.search(
            r"(?:\b(?:and|but|so|because|from|to)|(?:तर|पण|मला|मुझे|लेकिन|और|ते|पासून|वरून|से|तक))$",
            normalized,
        )
    )


def _is_actionable_barge_in(text: str, *, active_collection: bool) -> bool:
    """Keep deliberate interruptions while rejecting ambient room conversation."""
    normalized = " ".join(text.casefold().strip("!?.,;:।").split())
    if _is_no_more_enquiry(text) or voice_agent.is_resume_collection(text):
        return True
    if re.search(
        r"\b(?:cancel|stop|resume|continue)\b|रद्द|थांबा|जारी रखें|रोकिए",
        normalized,
    ):
        return True
    if voice_agent.collection_category(text) or voice_agent.is_explicit_enquiry(text):
        return True
    if active_collection and is_additional_collection_detail(text):
        return True
    # A short canonical station is a common answer to a route clarification.
    if find_station_names(text) and len(normalized.split()) <= 8:
        return True
    return False


def _route_clarification(text: str) -> tuple[str, str] | None:
    """Ask for missing operational stations instead of guessing a route."""
    normalized = text.casefold()
    # A line opening/status question can contain "A to B" but is not a route
    # planning request. Let the knowledge path answer it instead of asking for
    # origin and destination again.
    status_cues = re.search(
        r"\b(when|open|opening|start|starting|operational|status|construction)\b|"
        r"(कधी|केव्हा|चालू होणार|सुरू होणार|कब|कब शुरू|कब चालू|निर्माणाधीन)",
        normalized,
    )
    if status_cues is not None:
        return None
    route_cues = re.search(
        r"\b(from|to|route|travel|go|journey)\b|"
        r"(ते|पासून|वरून|जायच|जाऊ|मार्ग|से|तक|जाना|जाऊँ)",
        normalized,
    )
    if route_cues is None:
        return None
    stations = find_station_names(text)
    if len(stations) >= 2:
        return None
    language, _script = resolve_reply_language(text)
    if stations and ("pimpri" in normalized or "पिंपरी" in normalized):
        replies = {
            "english": (
                "I understood Shivaji Nagar. For Pimpri, do you mean PCMC station "
                "or Sant Tukaram Nagar station?"
            ),
            "hindi": (
                "शिवाजी नगर समझ गया। पिंपरी के लिए आपका मतलब पीसीएमसी स्टेशन है "
                "या संत तुकाराम नगर स्टेशन?"
            ),
            "marathi": (
                "शिवाजी नगर समजलं. पिंपरीसाठी तुम्हाला पीसीएमसी स्टेशन म्हणायचं आहे "
                "की संत तुकाराम नगर स्टेशन?"
            ),
        }
        return replies.get(language, replies["english"]), language
    if len(stations) == 1:
        station = stations[0]
        route_role = _single_station_route_role(text)
        if route_role and route_role[1] == "destination":
            replies = {
                "english": (
                    f"I understood {station} as your destination. Tell me your current "
                    "or nearest Pune Metro station so I can plan the route."
                ),
                "hindi": (
                    f"मैंने {station} को आपका गंतव्य समझा। मार्ग बताने के लिए अपना "
                    "वर्तमान या सबसे नज़दीकी पुणे मेट्रो स्टेशन बताइए।"
                ),
                "marathi": (
                    f"{station} हे तुमचं गंतव्य समजलं. मार्ग सांगण्यासाठी तुमचं "
                    "सध्याचं किंवा जवळचं पुणे मेट्रो स्थानक सांगा."
                ),
            }
            return replies.get(language, replies["english"]), language
        replies = {
            "english": f"I understood {station}. What is the other station for your journey?",
            "hindi": f"{station} समझ गया। आपकी यात्रा का दूसरा स्टेशन कौन-सा है?",
            "marathi": f"{station} समजलं. तुमच्या प्रवासातील दुसरं स्टेशन कोणतं आहे?",
        }
        return replies.get(language, replies["english"]), language
    replies = {
        "english": "Please tell me your starting station and destination station.",
        "hindi": "कृपया अपना शुरुआती स्टेशन और गंतव्य स्टेशन बताइए।",
        "marathi": "कृपया सुरुवातीचं स्टेशन आणि गंतव्य स्टेशन सांगा.",
    }
    return replies.get(language, replies["english"]), language


def _single_station_route_role(text: str) -> tuple[str, str] | None:
    """Return a locally safe origin/destination slot from a one-station turn."""
    stations = find_station_names(text)
    if len(stations) != 1:
        return None
    normalized = text.casefold()
    origin_cue = re.search(
        r"\bfrom\b|(?:पासून|वरून|वरनं|येथून|से)", normalized
    )
    destination_cue = re.search(
        r"\b(?:to|destination)\b|(?:जायच|जायचे|जाना|पोहोच|तक|मध्ये जाय|ला जाय)",
        normalized,
    )
    if destination_cue is not None and origin_cue is None:
        return stations[0], "destination"
    if origin_cue is not None and destination_cue is None:
        return stations[0], "origin"
    return None


async def run_voice_pipeline(
    webrtc_connection: Any,
    *,
    user_id: int,
    conversation_id: int,
    call_session_id: int,
) -> None:
    """Run one isolated, interruptible voice session inside this FastAPI process."""
    from pipecat.frames.frames import (
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
        InputAudioRawFrame,
        TTSAudioRawFrame,
        TTSStoppedFrame,
        TTSSpeakFrame,
        TextFrame,
        TranscriptionFrame,
        UserStartedSpeakingFrame,
        UserStoppedSpeakingFrame,
    )
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.runner import PipelineRunner
    from pipecat.pipeline.task import PipelineTask
    from pipecat.processors.audio.vad_processor import VADProcessor
    from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
    from pipecat.services.sarvam.stt import SarvamSTTService
    from pipecat.services.sarvam.tts import SarvamTTSService
    from pipecat.transcriptions.language import Language
    from pipecat.transports.base_transport import TransportParams
    from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

    class TurnState:
        processing = False
        user_speaking = False
        closing_requested = False
        pending_transcriptions: list[Any] = []
        flush_task: asyncio.Task[Any] | None = None
        carried_fragments: list[str] = []
        pending_route_origin: str | None = None
        pending_route_destination: str | None = None

    turn_state = TurnState()

    class BusyTurnGate(FrameProcessor):
        """Buffer finalized utterances captured while the previous turn is processing."""

        async def _flush_pending(self, direction: Any) -> None:
            await asyncio.sleep(TURN_AGGREGATION_DELAY_SECONDS)
            while turn_state.processing or turn_state.user_speaking:
                await asyncio.sleep(0.05)
            frames = list(turn_state.pending_transcriptions)
            turn_state.pending_transcriptions.clear()
            turn_state.flush_task = None
            if not frames:
                return
            last = frames[-1]
            # A short low-confidence filler can arrive after a good transcript.
            # Preserve metadata from the strongest frame so the complete caller
            # turn is not discarded based only on its final word.
            metadata_frame = max(
                frames,
                key=lambda item: _transcription_confidence(item) or 0.0,
            )
            combined = " ".join(item.text.strip() for item in frames if item.text.strip())
            logger.info("Replaying buffered call transcription: %r", combined)
            turn_state.processing = True
            await self.push_frame(
                TranscriptionFrame(
                    text=combined,
                    user_id=last.user_id,
                    timestamp=last.timestamp,
                    language=metadata_frame.language,
                    result=metadata_frame.result,
                    finalized=True,
                ),
                direction,
            )

        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)
            if isinstance(frame, UserStartedSpeakingFrame):
                turn_state.user_speaking = True
                await self.push_frame(frame, direction)
                return
            if isinstance(frame, UserStoppedSpeakingFrame):
                turn_state.user_speaking = False
                await self.push_frame(frame, direction)
                return
            if isinstance(frame, TranscriptionFrame) and frame.text.strip():
                turn_state.pending_transcriptions.append(frame)
                if turn_state.processing:
                    if _is_no_more_enquiry(frame.text):
                        turn_state.closing_requested = True
                        logger.info(
                            "Caller requested closing; pending voice reply will be suppressed"
                        )
                    else:
                        logger.info(
                            "Buffering call transcription while response is processing: %r",
                            frame.text.strip(),
                        )
                if turn_state.flush_task is not None:
                    turn_state.flush_task.cancel()
                # Debounce briefly after silence; VAD still lets a caller finish a
                # natural pause without adding a long fixed delay to every turn.
                turn_state.flush_task = asyncio.create_task(
                    self._flush_pending(direction)
                )
                if turn_state.user_speaking:
                    logger.info(
                        "Deferring finalized transcription until caller stops speaking: %r",
                        frame.text.strip(),
                    )
                return
            await self.push_frame(frame, direction)

    class SharedBrainProcessor(FrameProcessor):
        def __init__(self) -> None:
            super().__init__()
            self.bot_speaking = False
            self.turn_lock = asyncio.Lock()

        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)
            if isinstance(frame, BotStartedSpeakingFrame):
                self.bot_speaking = True
                await self.push_frame(frame, direction)
                return
            if isinstance(frame, BotStoppedSpeakingFrame):
                self.bot_speaking = False
                await self.push_frame(frame, direction)
                return
            if not isinstance(frame, TranscriptionFrame) or not frame.text.strip():
                await self.push_frame(frame, direction)
                return
            text = frame.text.strip()
            if turn_state.carried_fragments:
                text = " ".join([*turn_state.carried_fragments, text])
                turn_state.carried_fragments.clear()
                logger.info("Combined carried call fragments: %r", text)
            is_closing_turn = _is_no_more_enquiry(text)
            if is_closing_turn:
                turn_state.closing_requested = False
            confidence = _transcription_confidence(frame)
            stt_language = _transcription_language_code(frame)
            if stt_language and stt_language not in SUPPORTED_STT_LANGUAGES:
                # Sarvam occasionally labels an English station name as Gujarati
                # or Bengali. A locally verified station is safe to accept.
                if not find_station_names(text):
                    logger.info(
                        "Ignoring unsupported-language call transcription %s: %r",
                        stt_language,
                        text,
                    )
                    turn_state.processing = False
                    return
            with SessionLocal() as state_db:
                active_collection = bool(
                    (active_conversation := state_db.get(Conversation, conversation_id))
                    and active_conversation.complaint_collection_state
                )
            minimum_confidence = (
                COLLECTION_TRANSCRIPTION_CONFIDENCE
                if active_collection
                else MIN_TRANSCRIPTION_CONFIDENCE
            )
            # Station names and explicit call controls are validated locally.
            # Sarvam often assigns mixed Latin/Devanagari utterances a score just
            # below 0.60, so dropping these safe turns makes the call appear stuck.
            trusted_low_confidence_turn = bool(find_station_names(text)) or bool(
                voice_agent.is_resume_collection(text)
            ) or _is_no_more_enquiry(text) or bool(
                re.search(
                    r"\b(?:cancel|stop|resume|continue|proceed|yes|no)\b|"
                    r"रद्द|थांबा|पुढे\s*(?:जा|घ्या|जाऊ)|जारी\s*रखें|"
                    r"आगे\s*बढ़|हाँ|हां|हो|नाही|नहीं",
                    text.casefold(),
                )
            )
            if trusted_low_confidence_turn:
                minimum_confidence = min(minimum_confidence, 0.25)
            if confidence is not None and confidence < minimum_confidence:
                logger.info(
                    "Ignoring low-confidence call transcription %.3f (minimum %.2f): %r",
                    confidence,
                    minimum_confidence,
                    text,
                )
                turn_state.processing = False
                return
            if self.bot_speaking:
                if _is_actionable_barge_in(text, active_collection=active_collection):
                    # Preserve deliberate interruptions such as a destination,
                    # enquiry, cancellation, or explicitly added complaint detail.
                    turn_state.carried_fragments.append(text)
                    logger.info("Carrying actionable barge-in during bot speech: %r", text)
                else:
                    logger.info("Ignoring non-actionable speech during bot response: %r", text)
                turn_state.processing = False
                return
            if _is_incomplete_voice_fragment(text):
                turn_state.carried_fragments.append(text)
                logger.info("Carrying incomplete call fragment into next turn: %r", text)
                turn_state.processing = False
                return
            if self.turn_lock.locked():
                logger.info("Ignoring overlapping call transcription: %r", text)
                turn_state.processing = False
                return
            turn_started = time.monotonic()
            db = None
            try:
                async with self.turn_lock:
                    db = SessionLocal()
                    user_message = Message(
                        conversation_id=conversation_id,
                        role="user",
                        content=text,
                    )
                    db.add(user_message)
                    db.flush()
                    stored = list(
                        db.scalars(
                            select(Message)
                            .where(
                                Message.conversation_id == conversation_id,
                                Message.id != user_message.id,
                            )
                            .order_by(Message.created_at.desc(), Message.id.desc())
                            .limit(20)
                        )
                    )
                    user = db.get(User, user_id)
                    caller_language = _reply_language_from_stt(stt_language, text)
                    conversation = db.get(Conversation, conversation_id)
                    collection_reply = None
                    collection_completed = False
                    chat_notification = None
                    collection_language = caller_language
                    collection_paused_for_enquiry = False
                    if (
                        conversation
                        and conversation.complaint_collection_state
                        and voice_agent.is_resume_collection(text)
                    ):
                        collection_language = (
                            conversation.preferred_language or caller_language
                        )
                        collection_reply = collection_resume_reply(conversation)
                        logger.info("Resuming paused voice collection: %r", text)
                    elif (
                        conversation
                        and conversation.complaint_collection_state
                        and voice_agent.is_explicit_enquiry(text)
                    ):
                        # Preserve every collected field while answering a clear,
                        # unrelated Metro enquiry for one turn.
                        collection_paused_for_enquiry = True
                        collection_language = (
                            conversation.preferred_language or caller_language
                        )
                        logger.info("Pausing voice collection for enquiry: %r", text)
                    elif conversation and conversation.complaint_collection_state:
                        collection_language = (
                            conversation.preferred_language or caller_language
                        )
                        collected_snapshot = {
                            "name": conversation.complaint_collection_full_name,
                            "contact": conversation.complaint_collection_contact_number,
                            "station": conversation.complaint_collection_station,
                            "description": conversation.complaint_collection_description,
                        }
                        collection_reply, collection_completed = advance_collection(conversation, text, db)
                        if (
                            not collection_completed
                            and conversation.complaint_collection_state == "confirming"
                        ):
                            collection_reply = compact_voice_confirmation(conversation)
                        if collection_completed and re.search(r"PMC-\d{6}", collection_reply):
                            if collection_language == "marathi":
                                details = (
                                    f"नाव: {collected_snapshot['name']}\n"
                                    f"संपर्क क्रमांक: {collected_snapshot['contact']}\n"
                                    f"स्थानक: {collected_snapshot['station']}\n"
                                    f"तक्रार: {collected_snapshot['description']}"
                                )
                            elif collection_language == "hindi":
                                details = (
                                    f"नाम: {collected_snapshot['name']}\n"
                                    f"संपर्क नंबर: {collected_snapshot['contact']}\n"
                                    f"स्टेशन: {collected_snapshot['station']}\n"
                                    f"शिकायत: {collected_snapshot['description']}"
                                )
                            else:
                                details = (
                                    f"Name: {collected_snapshot['name']}\n"
                                    f"Contact: {collected_snapshot['contact']}\n"
                                    f"Station: {collected_snapshot['station']}\n"
                                    f"Complaint: {collected_snapshot['description']}"
                                )
                            chat_notification = f"{collection_reply}\n\n{details}"
                    elif conversation:
                        collection_category = voice_agent.collection_category(text)
                        if collection_category:
                            collection_reply = start_collection(
                                conversation,
                                collection_category,
                                text,
                                language=caller_language,
                            )
                            if conversation.complaint_collection_state == "confirming":
                                collection_reply = compact_voice_confirmation(conversation)
                    last_assistant = next(
                        (item for item in stored if item.role == "assistant"), None
                    )
                    planned_line_context = any(
                        _mentions_planned_line(item.content) for item in stored[:2]
                    )
                    closing_reply = _call_closing_reply(
                        text,
                        caller_language,
                        allow_bare_negative=bool(
                            last_assistant
                            and _offered_more_help(last_assistant.content)
                        ),
                    )
                    current_stations = find_station_names(text)
                    if len(current_stations) >= 2:
                        # A complete route supersedes any earlier clarification.
                        turn_state.pending_route_origin = None
                        turn_state.pending_route_destination = None
                    if (
                        turn_state.pending_route_destination
                        and len(current_stations) == 1
                        and current_stations[0] != turn_state.pending_route_destination
                    ):
                        fast_reply = (
                            _voice_route_reply(
                                current_stations[0],
                                turn_state.pending_route_destination,
                                caller_language,
                            ),
                            caller_language,
                        )
                        turn_state.pending_route_destination = None
                    elif (
                        turn_state.pending_route_origin
                        and len(current_stations) == 1
                        and current_stations[0] != turn_state.pending_route_origin
                    ):
                        fast_reply = (
                            _voice_route_reply(
                                turn_state.pending_route_origin,
                                current_stations[0],
                                caller_language,
                            ),
                            caller_language,
                        )
                        turn_state.pending_route_origin = None
                    else:
                        fast_reply = _fast_voice_reply(
                            text, planned_line_context=planned_line_context
                        )
                        if route_role := _single_station_route_role(text):
                            station, role = route_role
                            if role == "destination":
                                turn_state.pending_route_destination = station
                                turn_state.pending_route_origin = None
                            else:
                                turn_state.pending_route_origin = station
                                turn_state.pending_route_destination = None
                    if (
                        closing_reply is None
                        and last_assistant
                        and _offered_more_help(last_assistant.content)
                        and " ".join(text.casefold().strip("!?.,;:।").split())
                        in {"yes", "yeah", "yep", "हो", "हां", "हाँ", "जी"}
                    ):
                        fast_reply = (
                            voice_agent.ask_question_reply(caller_language),
                            caller_language,
                        )
                    if collection_reply is not None:
                        reply = collection_reply
                        response_language = collection_language
                    elif closing_reply is not None:
                        reply = closing_reply
                        response_language = caller_language
                    elif fast_reply is not None:
                        reply, _detected_language = fast_reply
                        response_language = caller_language
                        db.add(ResponseSourceLog(
                            source="rules", operation="llm", conversation_id=conversation_id,
                            call_session_id=call_session_id, channel="call", question=text,
                            answer=reply, provider="local", model="deterministic-rules",
                            actual_cost_inr=0.0, uncached_cost_inr=0.0,
                        ))
                    else:
                        qa_cache = QACacheService(db)
                        cached_entry = qa_cache.get_cached_entry(text, caller_language)
                        if cached_entry and reply_has_only_canonical_station_names(cached_entry.answer):
                            reply = cached_entry.answer
                            db.add(make_cache_log(
                                conversation_id=conversation_id, channel="call", question=text,
                                answer=reply, cache_entry_id=cached_entry.id,
                                call_session_id=call_session_id,
                            ))
                        else:
                            request = BrainRequest(
                                user_id=user_id,
                                user_identity=user.whatsapp_number if user else str(user_id),
                                channel="call", text=text, conversation_id=conversation_id,
                                session_id=call_session_id,
                                history=[BrainMessage(role=item.role, content=item.content)
                                         for item in reversed(stored)
                                         if item.role in {"user", "assistant"}],
                                preferred_language=caller_language,
                            )
                            response = await respond(request, db)
                            feedback_category = next(
                                (category for category in response.categories
                                 if category in {"complaint", "suggestion", "appreciation"}),
                                None,
                            )
                            if feedback_category and conversation:
                                reply = start_collection(
                                    conversation,
                                    feedback_category,
                                    text,
                                    language=caller_language,
                                )
                                collection_reply = reply
                                collection_language = caller_language
                                if conversation.complaint_collection_state == "confirming":
                                    collection_reply = compact_voice_confirmation(conversation)
                            else:
                                reply = response.reply_text.strip()
                                db.add(make_llm_log(
                                    conversation_id=conversation_id, channel="call", question=text,
                                    answer=reply, call_session_id=call_session_id,
                                ))
                                qa_cache.store_answer(text, reply, caller_language, "enquiry")
                        response_language = caller_language
                    if collection_paused_for_enquiry:
                        reply = (
                            f"{reply} "
                            f"{voice_agent.paused_collection_reminder(collection_language, conversation.pending_category or 'complaint')}"
                        )
                        response_language = collection_language
                    elif collection_completed:
                        reply = _continue_call_after_collection(
                            reply, response_language
                        )
                    elif closing_reply is None and collection_reply is None:
                        reply = _offer_more_help(reply, response_language)
                    # Keep the internal hang-up marker out of text sanitization;
                    # removing underscores used to turn it into [ENDCALL], so the
                    # detector missed it and the bot answered again after farewell.
                    reply = _sanitize_reply_with_end_marker(reply)
                    if turn_state.closing_requested and not is_closing_turn:
                        db.commit()
                        logger.info(
                            "Suppressing superseded voice reply for call session %s",
                            call_session_id,
                        )
                        return
                    db.add(
                        Message(
                            conversation_id=conversation_id,
                            role="assistant",
                            content=reply,
                        )
                    )
                    spoken_language = _spoken_reply_language(reply, response_language)
                    call = db.get(CallSession, call_session_id)
                    if call and spoken_language not in call.detected_languages:
                        call.detected_languages = [
                            *call.detected_languages,
                            spoken_language,
                        ]
                    db.commit()
                    if chat_notification and user:
                        try:
                            await whatsapp_client.send_text_message(
                                to=user.whatsapp_number,
                                body=chat_notification,
                            )
                            logger.info(
                                "Sent voice complaint confirmation to WhatsApp chat for call session %s",
                                call_session_id,
                            )
                        except Exception:
                            logger.exception(
                                "Failed to send voice complaint confirmation to chat for call session %s",
                                call_session_id,
                            )
            finally:
                if db is not None:
                    db.close()
                turn_state.processing = False
            logger.info(
                "Voice turn response ready in %.2fs for call session %s",
                time.monotonic() - turn_started,
                call_session_id,
            )
            await self.push_frame(
                TTSSpeakFrame(
                    text=_tag_tts_text(
                        reply, _spoken_reply_language(reply, response_language)
                    ),
                    append_to_context=False,
                ),
                direction,
            )

    class GreetingProtector(FrameProcessor):
        """Prevent microphone noise from interrupting the prerecorded greeting."""

        def __init__(self) -> None:
            super().__init__()
            self.started_at: float | None = None

        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)
            if self.started_at is None:
                self.started_at = time.monotonic()
            if (
                direction == FrameDirection.DOWNSTREAM
                and isinstance(frame, InputAudioRawFrame)
                and time.monotonic() - self.started_at < 4.5
            ):
                return
            await self.push_frame(frame, direction)

    class BotSpeechInputGate(FrameProcessor):
        """Prevent speaker echo from becoming a new caller turn."""

        def __init__(self) -> None:
            super().__init__()
            self.bot_speaking = False

        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)
            if isinstance(frame, BotStartedSpeakingFrame):
                self.bot_speaking = True
            elif isinstance(frame, BotStoppedSpeakingFrame):
                self.bot_speaking = False
            elif (
                direction == FrameDirection.DOWNSTREAM
                and self.bot_speaking
                and isinstance(frame, InputAudioRawFrame)
            ):
                return
            await self.push_frame(frame, direction)

    class CallEndDetector(FrameProcessor):
        def __init__(self) -> None:
            super().__init__()
            self.ending = False
            self.termination_task: asyncio.Task[Any] | None = None

        def _schedule_termination(self, delay: float) -> None:
            if self.termination_task is not None:
                self.termination_task.cancel()

            async def finish() -> None:
                await asyncio.sleep(delay)
                from app.services.whatsapp_calling_client import terminate_call

                await terminate_call(call_session_id)
                await task.cancel()

            self.termination_task = asyncio.create_task(finish())

        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)
            if self.ending and isinstance(frame, BotStoppedSpeakingFrame):
                await self.push_frame(frame, direction)
                # End shortly after the polite farewell has actually finished,
                # with a fallback below in case the provider omits this frame.
                self._schedule_termination(0.35)
                return
            if isinstance(frame, (TextFrame, TTSSpeakFrame)) and END_MARKER in frame.text:
                clean = frame.text.replace(END_MARKER, "").strip()
                if clean:
                    replacement = (
                        TTSSpeakFrame(text=clean, append_to_context=False)
                        if isinstance(frame, TTSSpeakFrame)
                        else TextFrame(text=clean)
                    )
                    await self.push_frame(replacement, direction)
                if not self.ending:
                    self.ending = True
                    self._schedule_termination(10.0)
                return
            await self.push_frame(frame, direction)

    transport = SmallWebRTCTransport(
        webrtc_connection=webrtc_connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_out_10ms_chunks=2,
        ),
    )
    audio_buffer = AudioBufferProcessor(
        sample_rate=24000,
        num_channels=2,
        auto_start_recording=True,
    )

    @audio_buffer.event_handler("on_audio_data")
    async def on_audio_data(
        _buffer: Any, audio: bytes, sample_rate: int, num_channels: int
    ) -> None:
        if not audio:
            return
        from pathlib import Path

        recordings_dir = Path(settings.CALL_RECORDINGS_DIR)
        filename = f"call_{call_session_id}.wav"
        path = recordings_dir / filename

        def write_recording() -> None:
            recordings_dir.mkdir(parents=True, exist_ok=True)
            with wave.open(str(path), "wb") as recording:
                recording.setsampwidth(2)
                recording.setnchannels(num_channels)
                recording.setframerate(sample_rate)
                recording.writeframes(audio)

        await asyncio.to_thread(write_recording)
        with SessionLocal() as db:
            call = db.get(CallSession, call_session_id)
            if call:
                metadata = dict(call.provider_metadata or {})
                metadata["recording_file"] = filename
                call.provider_metadata = metadata
                db.commit()
        logger.info("Saved recording for call session %s", call_session_id)

    stt_settings: dict[str, Any] = {"model": settings.SARVAM_STT_MODEL}
    if hasattr(Language, "UNKNOWN"):
        stt_settings["language"] = Language.UNKNOWN
    stt = SarvamSTTService(
        api_key=settings.SARVAM_API_KEY,
        mode=settings.SARVAM_STT_MODE,
        settings=SarvamSTTService.Settings(**stt_settings),
    )

    tts = SarvamTTSService(
        api_key=settings.SARVAM_API_KEY,
        settings=SarvamTTSService.Settings(
            model=settings.SARVAM_TTS_MODEL,
            voice=settings.SARVAM_TTS_SPEAKER_ENGLISH,
            language=Language.EN_IN,
            pace=settings.SARVAM_TTS_PACE,
            temperature=settings.SARVAM_TTS_TEMPERATURE,
        ),
    )
    voices = {
        "english": settings.SARVAM_TTS_SPEAKER_ENGLISH,
        "hindi": settings.SARVAM_TTS_SPEAKER_HINDI,
        "marathi": settings.SARVAM_TTS_SPEAKER_MARATHI,
    }
    original_run_tts = SarvamTTSService.run_tts
    original_append_to_audio_context = SarvamTTSService.append_to_audio_context
    pending_tts: dict[str, dict[str, Any]] = {}

    async def caching_append_to_audio_context(
        self: Any, context_id: str | None, frame: Any
    ) -> None:
        """Capture Sarvam's asynchronously received audio frames per TTS context."""
        pending = pending_tts.get(context_id or "")
        if pending and isinstance(frame, TTSAudioRawFrame):
            pending["chunks"].append(frame.audio)
            pending["sample_rate"] = frame.sample_rate
            pending["num_channels"] = frame.num_channels
        if pending and isinstance(frame, TTSStoppedFrame):
            try:
                chunks = pending["chunks"]
                if chunks and pending.get("sample_rate"):
                    with SessionLocal() as cache_db:
                        existing = cache_db.scalar(select(TTSAudioCache).where(
                            TTSAudioCache.cache_key == pending["cache_key"]
                        ))
                        if existing is None:
                            cache_db.add(TTSAudioCache(
                                cache_key=pending["cache_key"], text=pending["text"],
                                language=pending["language"], model=settings.SARVAM_TTS_MODEL,
                                voice=voices[pending["language"]], audio=b"".join(chunks),
                                sample_rate=pending["sample_rate"],
                                num_channels=pending["num_channels"],
                            ))
                        cache_db.add(make_tts_log(
                            conversation_id=conversation_id,
                            call_session_id=call_session_id,
                            text=pending["text"], source="tts",
                        ))
                        cache_db.commit()
            except Exception:
                logger.exception("TTS cache store failed; synthesized audio was still delivered")
            finally:
                pending_tts.pop(context_id or "", None)
        await original_append_to_audio_context(self, context_id, frame)

    async def multilingual_run_tts(self: Any, text: str, context_id: str):
        text, language = _parse_tts_text(text)
        cache_key = hashlib.sha256(
            "|".join((settings.SARVAM_TTS_MODEL, voices[language], language,
                      str(settings.SARVAM_TTS_PACE), text)).encode("utf-8")
        ).hexdigest()
        try:
            with SessionLocal() as cache_db:
                cached_audio = cache_db.scalar(select(TTSAudioCache).where(
                    TTSAudioCache.cache_key == cache_key
                ))
                if cached_audio:
                    cached_audio.hit_count += 1
                    cached_audio.last_used_at = _utcnow()
                    cache_db.add(make_tts_log(
                        conversation_id=conversation_id, call_session_id=call_session_id,
                        text=text, source="cache", cache_entry_id=None,
                    ))
                    audio = cached_audio.audio
                    sample_rate = cached_audio.sample_rate
                    num_channels = cached_audio.num_channels
                    cache_db.commit()
                    # Smaller frames preserve interruption and transport timing.
                    bytes_per_chunk = max(320, sample_rate * num_channels * 2 // 10)
                    for offset in range(0, len(audio), bytes_per_chunk):
                        yield TTSAudioRawFrame(
                            audio=audio[offset:offset + bytes_per_chunk],
                            sample_rate=sample_rate, num_channels=num_channels,
                            context_id=context_id,
                        )
                    return
        except Exception:
            logger.exception("TTS cache lookup failed; using live Sarvam synthesis")
        pending_tts[context_id] = {
            "cache_key": cache_key, "text": text, "language": language,
            "chunks": [], "sample_rate": 0, "num_channels": 1,
        }
        if language == "marathi":
            target_language = Language.MR_IN
        elif language == "hindi":
            target_language = Language.HI_IN
        else:
            target_language = Language.EN_IN
        target_voice = voices[language]
        if (
            self._settings.language != target_language
            or self._settings.voice != target_voice
        ):
            self._settings.language = target_language
            self._settings.voice = target_voice
            await self._send_config()
        async for audio_frame in original_run_tts(self, text, context_id):
            yield audio_frame

    tts.run_tts = types.MethodType(multilingual_run_tts, tts)
    tts.append_to_audio_context = types.MethodType(caching_append_to_audio_context, tts)
    pipeline = Pipeline(
        [
            transport.input(),
            GreetingProtector(),
            BotSpeechInputGate(),
            VADProcessor(
                vad_analyzer=_build_vad_analyzer()
            ),
            stt,
            BusyTurnGate(),
            SharedBrainProcessor(),
            CallEndDetector(),
            tts,
            transport.output(),
            audio_buffer,
        ]
    )
    task = PipelineTask(pipeline)
    greeting_queued = False

    @transport.event_handler("on_client_connected")
    async def on_connected(_transport: Any, _client: Any) -> None:
        nonlocal greeting_queued
        _set_call_state(call_session_id, "active", answered_at=_utcnow())
        if not greeting_queued:
            greeting_queued = True
            await task.queue_frames(
                [
                    TTSSpeakFrame(
                        text=_tag_tts_text(GREETING_TEXT, "marathi"),
                        append_to_context=False,
                    )
                ]
            )

    @transport.event_handler("on_client_disconnected")
    async def on_disconnected(_transport: Any, _client: Any) -> None:
        await task.cancel()

    runner = PipelineRunner()
    try:
        await runner.run(task)
        _set_call_state(call_session_id, "completed", ended_at=_utcnow())
    except asyncio.CancelledError:
        _set_call_state(call_session_id, "completed", ended_at=_utcnow(), end_reason="disconnected")
    except Exception:
        logger.exception("Voice pipeline failed for call session %s", call_session_id)
        _set_call_state(call_session_id, "failed", ended_at=_utcnow(), end_reason="pipeline_error")
