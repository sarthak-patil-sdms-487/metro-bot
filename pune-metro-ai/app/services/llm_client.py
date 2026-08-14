"""Google Gemini client for generating assistant responses."""

import json
import logging
import re
from difflib import get_close_matches
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import CategoryLog, ComplaintTracking


logger = logging.getLogger(__name__)
FALLBACK_MESSAGE = "Sorry, I'm having trouble answering right now. Please try again."
_shared_http_client: httpx.AsyncClient | None = None
PROJECT_ROOT = Path(__file__).resolve().parents[2]
REFERENCE_FILES = {
    "fares": "fares.md",
    "stations": "stations.md",
    "rules": "rules.md",
    "card": "card.md",
    "card_non_kyc": "card_non_kyc.md",
    "vidyarthi_pass": "vidyarthi_pass.md",
    "contact": "contact.md",
    "timetable": "timetable.md",
}
FARES_MATRIX_PATH = PROJECT_ROOT / "data" / "fares_matrix.json"
UPCOMING_LINES_PATH = PROJECT_ROOT / "data" / "upcoming_lines.md"
STATION_ALIASES_PATH = PROJECT_ROOT / "data" / "station_aliases.json"


def _get_shared_http_client() -> httpx.AsyncClient:
    """Reuse TLS connections across dialogue turns to reduce model latency."""
    global _shared_http_client
    if _shared_http_client is None or _shared_http_client.is_closed:
        _shared_http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )
    return _shared_http_client


async def close_llm_http_client() -> None:
    """Close the shared provider connection pool during application shutdown."""
    global _shared_http_client
    if _shared_http_client is not None and not _shared_http_client.is_closed:
        await _shared_http_client.aclose()
    _shared_http_client = None


def _load_fares_matrix() -> dict:
    """Load the official fare matrix once when this module is imported."""
    try:
        return json.loads(FARES_MATRIX_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("Failed to load fare matrix from %s", FARES_MATRIX_PATH)
        return {}


FARES_MATRIX_DATA = _load_fares_matrix()
MATRIX_STATIONS = FARES_MATRIX_DATA.get("stations", [])
FARE_MATRIX = FARES_MATRIX_DATA.get("fare_matrix", {})
NCMC_DISCOUNT_PERCENT = FARES_MATRIX_DATA.get("_ncmc_discount_percent", 10)


def _load_upcoming_stations() -> frozenset[str]:
    """Read planned Line 3 station names from the maintained reference file."""
    try:
        return frozenset(
            line.removeprefix("- ").strip()
            for line in UPCOMING_LINES_PATH.read_text(encoding="utf-8").splitlines()
            if line.startswith("- ")
        )
    except OSError:
        logger.exception("Failed to load upcoming-line data from %s", UPCOMING_LINES_PATH)
        return frozenset()


UPCOMING_STATIONS = _load_upcoming_stations()
UPCOMING_STATION_ALIASES = {
    "hinjewadi": "Hinjewadi",
    "hinjawadi": "Hinjewadi",
    "hinjavadi": "Hinjewadi",
    "हिंजवडी": "Hinjewadi",
    "हिंजेवाडी": "Hinjewadi",
    "हिंजवाडी": "Hinjewadi",
    "बाणेर": "Baner",
    "बानेर": "Baner",
    "भानेर": "Baner",
    "bane": "Baner",
    "banner": "Baner",
    "megapolis": "Megapolis Society (Maan)",
    "maan": "Megapolis Society (Maan)",
    "wakad": "Wakad Chowk",
    "wakad chowk": "Wakad Chowk",
    "balewadi": "Balewadi Stadium",
    "balewadi stadium": "Balewadi Stadium",
    "shivajinagar hinjewadi corridor": "Hinjewadi–Shivajinagar corridor",
    "hinjewadi shivajinagar corridor": "Hinjewadi–Shivajinagar corridor",
}
CANONICAL_CATEGORIES = frozenset({"complaint", "suggestion", "appreciation", "enquiry"})
# Old LLM prompts and early database rows used these names for the same enquiry type.
CATEGORY_ALIASES = {"query": "enquiry", "en": "enquiry", "others": "enquiry"}

# These one- and two-word messages are too low-signal to delegate to a probabilistic
# classifier. Keep the sets deliberately exact: a longer message still goes through
# normal classification and can therefore ask a real Pune Metro question.
SHORT_GREETINGS = frozenset(
    {
        "hi",
        "hii",
        "hello",
        "helloo",
        "hey",
        "hey there",
        "heyy",
        "heyyy",
        "hii there",
        "gm",
        "namaste",
        "namaskar",
        "namaste kase ahat",
        "kem cho",
        "good morning",
        "good afternoon",
        "good evening",
        "good night",
        "हाय",
        "नमस्ते",
        "नमस्कार",
        "काय",
        "राम राम",
        "जय हिंद",
    }
)
SHORT_ACKNOWLEDGMENTS = frozenset(
    {
        "sure", "ok", "okay", "yes", "thanks", "thank u", "thankyou",
        "thank you", "no", "no thank u", "no thank you", "got it", "cool",
        "alright", "thx", "ty",
    }
)
CONFUSION_SIGNALS = frozenset(
    {
        "confused",
        "i am confused",
        "i'm confused",
        "im confused",
        "i do not understand",
        "i don't understand",
        "i dont understand",
        "what do you mean",
        "huh",
        "i don't get it",
        "i dont get it",
        "मुझे समझ नहीं आया",
        "समझ नहीं आया",
        "मला समजले नाही",
        "समजत नाही",
        "काय म्हणायचे आहे",
    }
)
STATION_ALIASES = {
    "pcmc bhavan": "PCMC",
    "pcmc": "PCMC",
    "shivajinagar": "Shivaji Nagar",
    "shivaji nagar": "Shivaji Nagar",
    "civil court": "District Court",
    "kasba peth budhwar peth": "Kasba Peth",
    "budhwar peth": "Kasba Peth",
    "bund garden road": "Bund Garden",
    "bun garden": "Bund Garden",
    "ban garden": "Bund Garden",
    "band garden": "Bund Garden",
    "बन garden": "Bund Garden",
    "bhosari": "Nashik Phata (Bhosari)",
    "bhosari nashik phata": "Nashik Phata (Bhosari)",
    "nashik phata": "Nashik Phata (Bhosari)",
    "pune station": "Pune Railway Station",
    "pune railway": "Pune Railway Station",
    "ruby hall": "Ruby Hall Clinic",
    "yerawada": "Yerwada",
    "rto pune": "R.T.O. Pune",
    "r t o pune": "R.T.O. Pune",
    "sndt college": "S.N.D.T. College",
    "s n d t college": "S.N.D.T. College",
    "sndt": "S.N.D.T. College",
    "nal stop": "S.N.D.T. College",
    "swargat": "Swargate",
    "vanaj": "Vanaz",
    "vanas": "Vanaz",
    "rambadi": "Ramwadi",
    "ram badi": "Ramwadi",
}

# This is the operational network topology, kept separate from the fare matrix so
# routes cannot be inferred from the order or values of a fare table. District
# Court (also called Civil Court) is the only Purple/Aqua interchange; Swargate is
# the Purple Line terminus and is not an Aqua Line interchange.
OPERATIONAL_LINES = {
    "Purple Line": (
        "PCMC", "Sant Tukaram Nagar", "Nashik Phata (Bhosari)", "Kasarwadi",
        "Phugewadi", "Dapodi", "Bopodi", "Khadki", "Range Hill", "Shivaji Nagar",
        "District Court", "Kasba Peth", "Mahatma Phule Mandai", "Swargate",
    ),
    "Aqua Line": (
        "Vanaz", "Anand Nagar", "Paud Phata", "S.N.D.T. College", "Garware College",
        "Deccan Gymkhana", "Chhatrapati Sambhaji Udyan", "PMC", "District Court",
        "R.T.O. Pune", "Pune Railway Station", "Ruby Hall Clinic", "Bund Garden",
        "Yerwada", "Kalyani Nagar", "Ramwadi",
    ),
}
INTERCHANGE_STATIONS = frozenset({"District Court"})
CANONICAL_STATIONS = frozenset(
    station for stations in OPERATIONAL_LINES.values() for station in stations
)
DEVANAGARI_STATION_ALIASES = {
    "पीसीएमसी": "PCMC",
    "पी सी एम सी": "PCMC",
    "पीसीएमसी भवन": "PCMC",
    "संत तुकाराम नगर": "Sant Tukaram Nagar",
    "संत तुकारामनगर": "Sant Tukaram Nagar",
    "नाशिक फाटा": "Nashik Phata (Bhosari)",
    "नाशिक फाटा भोसरी": "Nashik Phata (Bhosari)",
    "भोसरी": "Nashik Phata (Bhosari)",
    "कासारवाडी": "Kasarwadi",
    "फुगेवाडी": "Phugewadi",
    "दापोडी": "Dapodi",
    "बोपोडी": "Bopodi",
    "खडकी": "Khadki",
    "रेंज हिल": "Range Hill",
    "शिवाजीनगर": "Shivaji Nagar",
    "शिवाजी नगर": "Shivaji Nagar",
    "सिव्हिल कोर्ट": "District Court",
    "सिविल कोर्ट": "District Court",
    "डिस्ट्रिक्ट कोर्ट": "District Court",
    "कसबा पेठ": "Kasba Peth",
    "कसबा पेठ बुधवार पेठ": "Kasba Peth",
    "महात्मा फुले मंडई": "Mahatma Phule Mandai",
    "मंडई": "Mahatma Phule Mandai",
    "स्वारगेट": "Swargate",
    "वनाज": "Vanaz",
    "वनास": "Vanaz",
    "आनंद नगर": "Anand Nagar",
    "आनंदनगर": "Anand Nagar",
    "पौड फाटा": "Paud Phata",
    "पौडफाटा": "Paud Phata",
    "एसएनडीटी कॉलेज": "S.N.D.T. College",
    "एस एन डी टी कॉलेज": "S.N.D.T. College",
    "गरवारे कॉलेज": "Garware College",
    "डेक्कन जिमखाना": "Deccan Gymkhana",
    "छत्रपती संभाजी उद्यान": "Chhatrapati Sambhaji Udyan",
    "छत्रपति संभाजी उद्यान": "Chhatrapati Sambhaji Udyan",
    "पीएमसी": "PMC",
    "पी एम सी": "PMC",
    "आरटीओ": "R.T.O. Pune",
    "आर टी ओ": "R.T.O. Pune",
    "आरटीओ पुणे": "R.T.O. Pune",
    "पुणे रेल्वे स्थानक": "Pune Railway Station",
    "पुणे रेलवे स्टेशन": "Pune Railway Station",
    "पुणे स्टेशन": "Pune Railway Station",
    "रुबी हॉल क्लिनिक": "Ruby Hall Clinic",
    "रूबी हॉल क्लिनिक": "Ruby Hall Clinic",
    "रुबी हॉल": "Ruby Hall Clinic",
    "बंड गार्डन": "Bund Garden",
    "बंडगार्डन": "Bund Garden",
    "बन गार्डन": "Bund Garden",
    "बन्ड गार्डन": "Bund Garden",
    "बनगार्डन": "Bund Garden",
    "येरवडा": "Yerwada",
    "यरवडा": "Yerwada",
    "कल्याणी नगर": "Kalyani Nagar",
    "कल्याणीनगर": "Kalyani Nagar",
    "रामवाडी": "Ramwadi",
}

def _load_station_aliases() -> None:
    """Load additional station aliases from a JSON file if it exists."""
    if not STATION_ALIASES_PATH.exists():
        return
    try:
        with open(STATION_ALIASES_PATH, "r", encoding="utf-8") as f:
            aliases = json.load(f)
            STATION_ALIASES.update(aliases.get("latin", {}))
            DEVANAGARI_STATION_ALIASES.update(aliases.get("devanagari", {}))
    except (OSError, json.JSONDecodeError):
        logger.exception("Failed to load station aliases from %s", STATION_ALIASES_PATH)

_load_station_aliases()

# Curated from DEVANAGARI_STATION_ALIASES: each operational station has one
# generation spelling, while the aliases above remain permissive for input parsing.
# Proper names are phonetic transliterations, never semantic translations.
STATION_DEVANAGARI_NAMES = {
    "PCMC": "पीसीएमसी",
    "Sant Tukaram Nagar": "संत तुकाराम नगर",
    "Nashik Phata (Bhosari)": "नाशिक फाटा (भोसरी)",
    "Kasarwadi": "कासारवाडी",
    "Phugewadi": "फुगेवाडी",
    "Dapodi": "दापोडी",
    "Bopodi": "बोपोडी",
    "Khadki": "खडकी",
    "Range Hill": "रेंज हिल",
    "Shivaji Nagar": "शिवाजी नगर",
    "District Court": "डिस्ट्रिक्ट कोर्ट",
    "Kasba Peth": "कसबा पेठ",
    "Mahatma Phule Mandai": "महात्मा फुले मंडई",
    "Swargate": "स्वारगेट",
    "Vanaz": "वनाज",
    "Anand Nagar": "आनंद नगर",
    "Paud Phata": "पौड फाटा",
    "S.N.D.T. College": "एसएनडीटी कॉलेज",
    "Garware College": "गरवारे कॉलेज",
    "Deccan Gymkhana": "डेक्कन जिमखाना",
    "Chhatrapati Sambhaji Udyan": "छत्रपती संभाजी उद्यान",
    "PMC": "पीएमसी",
    "R.T.O. Pune": "आरटीओ पुणे",
    "Pune Railway Station": "पुणे रेल्वे स्थानक",
    "Ruby Hall Clinic": "रुबी हॉल क्लिनिक",
    "Bund Garden": "बंड गार्डन",
    "Yerwada": "येरवडा",
    "Kalyani Nagar": "कल्याणी नगर",
    "Ramwadi": "रामवाडी",
}
HINDI_MARKERS = {
    "नमस्ते",
    "मुझे",
    "समझ",
    "नहीं",
    "है",
    "हैं",
    "कितना",
    "कितनी",
    "कहाँ",
    "क्या",
    "कैसे",
    "सकते",
    "सकता",
    "चाहिए",
    "रहा",
    "रही",
    "ना",
    "ता",
    "ती",
    "गा",
}
MARATHI_MARKERS = {
    "नमस्कार",
    "मला",
    "समजत",
    "नाही",
    "आहे",
    "आहेत",
    "किती",
    "कुठे",
    "काय",
    "कसे",
    "शकतो",
    "शकते",
    "पाहिजे",
    "आहात",
    "वी",
    "तो",
    "ते",
    "ला",
    "ची",
    "चा",
}
HINDI_SUFFIXES = ("ना", "ता", "ती", "गा")
MARATHI_SUFFIXES = ("वी", "तो", "ते", "ला", "ची", "चा")
ROMANIZED_HINDI_MARKERS = {
    "hai", "hain", "kitna", "kitni", "kahan", "kya", "kaise", "sakte", "sakta", "chahiye",
}
ROMANIZED_MARATHI_MARKERS = {
    "aahe", "aahet", "ahe", "kiti", "kuthe", "kay", "kasa", "kase", "shakto", "shakte",
    "pahije", "mala", "madhe", "bhetu", "shakel",
}
MARATHI_SWITCH_PATTERNS = (
    r"\bmarathi\s+madhe\b",
    r"\bmarathi\s+bhasha\b",
    r"\bmarathi\s+la\b",
    r"मराठीत",
    r"मराठी\s+भाषेत",
)
HINDI_SWITCH_PATTERNS = (
    r"\bhindi\s+mein\b",
    r"\bhindi\s+bhasha\s+mein\b",
    r"हिंदी\s+में",
    r"हिंदी\s+भाषा\s+में",
)
SYSTEM_MESSAGE = (
    "You are a helpful Pune Metro assistant. Answer questions about routes, fares, "
    "timings, rules (including luggage, pets, etiquette, and reserved seating), station "
    "facilities, One Pune cards, Vidyarthi Passes, and customer-care contacts. "
    "For fare questions, use the loaded official fare-matrix reference and do not "
    "infer or estimate a fare that is not shown there. "
    "Only Purple Line (PCMC-Swargate) and Aqua Line (Vanaz-Ramwadi) are currently "
    "operational. If the user asks about a station or corridor not in the loaded "
    "stations/fares reference data (for example, Hinjewadi), do NOT invent a route — "
    "clearly state that the corridor is still under construction and not yet accessible "
    "to passengers. "
    "You must ONLY answer questions related to Pune Metro — its routes, fares, timings, "
    "rules, stations, One Pune Card, Vidyarthi Pass, customer care, and commuter "
    "etiquette. If the user's message is about anything unrelated to Pune Metro (for "
    "example, other transport modes such as buses, trains, or cabs to other cities, "
    "general travel advice, unrelated topics, or anything outside Pune Metro's "
    "services), do NOT answer it using general knowledge. Instead, politely explain "
    "that you can only help with Pune Metro related queries and ask whether they have "
    "a Pune Metro question. "
    "If reference_data does not clearly answer the question and the question is not "
    "about Pune Metro, do not use outside general knowledge (for example, math, home "
    "electronics or networking troubleshooting, or unrelated trivia) to answer it — "
    "instead give the standard scope-boundary response. "
    "Keep responses concise, using 2-4 sentences. Do not proactively provide the "
    "helpline number, phone number, or suggest calling or visiting the app's Help & "
    "Support section in every response. When a user asks for general, non-complaint "
    "contact details, use the loaded contact reference and give the current toll-free "
    "number 1800 270 5501 where relevant. For complaints, acknowledge the issue "
    "empathetically and say that it has been logged and the user will receive a "
    "tracking ID; never direct a complaint to a phone number, helpline, or support "
    "channel. "
    "Never use Markdown formatting such as #, ##, **, or - bullets in your response "
    "— WhatsApp does not render Markdown. Write plain conversational sentences, using "
    "plain hyphens only for a short list if truly needed. "
    "Never use emojis in any response, under any circumstance. This assistant "
    "supports English, Hindi, and Marathi. You MUST detect the language of ONLY "
    "the current user message (ignore the language of previous turns in the "
    "conversation) and reply in that exact same language. Hindi and Marathi are "
    "DIFFERENT languages that share the Devanagari script but have different "
    "grammar and vocabulary — do not default to Marathi. Key Hindi markers include "
    "words like 'है', 'हैं', 'कितना', 'कहाँ', 'क्या', 'कैसे', 'सकते हैं'. Key "
    "Marathi markers include words like 'आहे', 'आहेत', 'किती', 'कुठे', 'काय', "
    "'कसे', 'शकतो/शकते'. Examples: 'पार्किंग शुल्क कितना है?' uses Hindi grammar "
    "('कितना है') -> respond in Hindi. 'पार्किंग शुल्क किती आहे?' uses Marathi "
    "grammar ('किती आहे') -> respond in Marathi. Always match the grammar pattern "
    "of the CURRENT message, not previous messages in the conversation. Use the full "
    "conversation history to resolve ambiguous follow-up "
    "questions — if the user previously asked about a specific station, topic, or "
    "entity and their new message is a vague follow-up (for example, just 'area "
    "sqft' or 'what about fares'), assume they are still referring to the same "
    "station or topic from earlier in the conversation unless they clearly indicate "
    "otherwise."
)
CLASSIFICATION_SYSTEM_MESSAGE = """Classify the user's Pune Metro message. Return ONLY valid JSON in this exact shape:
{
  "intent": "greeting" | "ambiguous" | "direct_query" | "acknowledgment" | "out_of_scope",
  "detected_language": "english" | "hindi" | "marathi",
  "classification_confident": boolean,
  "categories": ["complaint" | "suggestion" | "appreciation" | "enquiry"],
  "subcategories": ["Passenger Amenities" | "Staff Complaints" | "Refund" | "AFC & Ticketing" | "Train Operation & Services" | "Feeder Services" | "Others"],
  "extracted_details": {"metro_station": string|null, "ticket_number": string|null, "payment_method": string|null, "passenger_name": string|null},
  "clarification_question": string|null,
  "clarification_options": [string]|null,
  "reference_topics": ["fares" | "stations" | "rules" | "card" | "card_non_kyc" | "vidyarthi_pass" | "contact" | "timetable"],
  "asking_about_complaint_status": boolean
}
IMPORTANT: subcategories must ONLY contain values from this exact list of 7 strings: Passenger Amenities, Staff Complaints, Refund, AFC & Ticketing, Train Operation & Services, Feeder Services, Others. Do NOT put "fares", "stations", "rules", or any other topic name in subcategories — those belong only in reference_topics.
IMPORTANT: reference_topics must ONLY contain values from: fares, stations, rules, card, card_non_kyc, vidyarthi_pass, contact, timetable — this is separate from subcategories and categories.
Rules: A message can have multiple categories; return every applicable category. Only extract details explicitly stated in the message; never guess. Set asking_about_complaint_status to true only when the user asks about the status of a complaint, registered issue, or ticket. Set classification_confident to true only if the message can be confidently assigned an intent and, for a direct_query, at least one category. If it cannot be confidently classified (including nonsense, uncertainty about the user's meaning, or an unclear follow-up), set intent to "ambiguous", classification_confident to false, and categories/subcategories to empty. Do not force an enquiry guess. If the message is clearly understandable but unrelated to Pune Metro (for example, another city's buses, trains, or cabs; general travel advice; general knowledge; or unrelated services), set intent to "out_of_scope", classification_confident to true, and categories/subcategories to empty. Standalone arithmetic, unit conversions, or general-trivia questions with no Pune Metro context are out_of_scope, not ambiguous, even if grammatically clear. Generic non-Metro troubleshooting such as "my Wi-Fi isn't working" or "my phone battery is low" is out_of_scope unless it explicitly references a Pune Metro station, train, or app. This is different from ambiguous: out_of_scope is clear but not relevant. A greeting is any short standalone salutation with no question or request attached — including but not limited to hi/hello/hey/hii, hey there, good morning/afternoon/evening, gm, namaste/namaskar (in English, Hindi, or Marathi script), राम राम, जय हिंद, काय, kem cho, and casual or romanized spellings such as "Namaste kase ahat". A message that asks anything, even a short yes/no question, must be direct_query (or ambiguous if genuinely unclear), never greeting. Examples: "Hi" -> greeting. "Good evening!" -> greeting. "नमस्ते" -> greeting. "Is the metro running today?" -> direct_query. "Metro kadhi suru zhali?" -> direct_query. "What is 123 x 456?" -> out_of_scope. "20% of 500?" -> out_of_scope. "Wi-Fi का चालत नाही?" -> out_of_scope. "Wi-Fi at PCMC station isn't working" -> direct_query with complaint and Passenger Amenities. reference_topics must contain zero or more topics relevant to answering this specific message. This assistant supports English, Hindi, and Marathi. You MUST identify the language of ONLY the current user message, ignoring previous turns. Hindi and Marathi are DIFFERENT languages that share the Devanagari script but have different grammar and vocabulary — do not default to Marathi. Key Hindi markers include "है", "हैं", "कितना", "कहाँ", "क्या", "कैसे", and "सकते हैं". Key Marathi markers include "आहे", "आहेत", "किती", "कुठे", "काय", "कसे", and "शकतो/शकते". "पार्किंग शुल्क कितना है?" is Hindi because of "कितना है"; "पार्किंग शुल्क किती आहे?" is Marathi because of "किती आहे". Always match the grammar pattern of the CURRENT message, not previous messages. If the message is in a language other than English, Hindi, or Marathi, classify it normally but the reply must politely ask in English for a rephrase in one of the supported languages. Use intent "acknowledgment" for short closing remarks such as "ok", "thanks", "okay", "got it", "cool", "alright", or "thank you", when the user is closing the exchange rather than asking something new. For acknowledgment and out_of_scope messages, categories and subcategories must be empty, every extracted detail must be null, and clarification_question and clarification_options must both be null. For ambiguous messages, clarification_question and clarification_options must both be null because the application will show its shared category menu. For greeting and direct_query, clarification_question and clarification_options must be null."""
FALLBACK_CLASSIFICATION = {
    # A provider failure is not a basis for guessing a category. The webhook will
    # use the shared main menu for this low-confidence result.
    "intent": "ambiguous",
    "detected_language": "english",
    "classification_confident": False,
    "categories": [],
    "subcategories": [],
    "extracted_details": {
        "metro_station": None,
        "ticket_number": None,
        "payment_method": None,
        "passenger_name": None,
    },
    "clarification_question": None,
    "clarification_options": None,
    "reference_topics": [],
    "asking_about_complaint_status": False,
}
BRIEF_LANGUAGE_INSTRUCTION = (
    "Never use emojis. This assistant supports three languages: English, Hindi, "
    "and Marathi. Detect the language of this specific user message independently "
    "and reply only in that language. Use correct, authentic vocabulary, spelling, "
    "and grammar, and never mix Hindi and Marathi words, spellings, or conjuncts "
    "within one response. For any other language, reply in English asking the user "
    "to rephrase in English, Hindi, or Marathi."
)


def detect_language(text: str) -> str:
    """Return a deterministic language hint for English, Hindi, Marathi, or mixed."""
    has_devanagari = bool(re.search(r"[\u0900-\u097F]", text))
    has_latin = bool(re.search(r"[A-Za-z]", text))
    if has_devanagari and has_latin:
        return "mixed"
    if not has_devanagari:
        romanized_words = re.findall(r"[a-z]+", text.casefold())
        hindi_score = sum(word in ROMANIZED_HINDI_MARKERS for word in romanized_words)
        marathi_score = sum(word in ROMANIZED_MARATHI_MARKERS for word in romanized_words)
        if hindi_score > marathi_score:
            return "hindi"
        if marathi_score > hindi_score:
            return "marathi"
        return "english"

    words = re.findall(r"[\u0900-\u097F]+", text)
    hindi_score = sum(
        word in HINDI_MARKERS or word.endswith(HINDI_SUFFIXES) for word in words
    )
    marathi_score = sum(
        word in MARATHI_MARKERS or word.endswith(MARATHI_SUFFIXES) for word in words
    )
    if hindi_score > marathi_score:
        return "hindi"
    if marathi_score > hindi_score:
        return "marathi"
    return "marathi"


def detect_script(text: str) -> str:
    """Return the dominant writing script used by the current message."""
    devanagari_count = len(re.findall(r"[\u0900-\u097F]", text))
    latin_count = len(re.findall(r"[A-Za-z]", text))
    return "devanagari" if devanagari_count > latin_count else "latin"


def resolve_reply_language(text: str) -> tuple[str, str]:
    """Resolve both language and dominant script for a reply to current text."""
    language = detect_language(text)
    script = detect_script(text)
    if language == "mixed":
        latin_words = re.findall(r"[a-z]+", text.casefold())
        devanagari_words = re.findall(r"[\u0900-\u097F]+", text)
        hindi_score = sum(word in ROMANIZED_HINDI_MARKERS for word in latin_words)
        hindi_score += sum(
            word in HINDI_MARKERS or word.endswith(HINDI_SUFFIXES)
            for word in devanagari_words
        )
        marathi_score = sum(word in ROMANIZED_MARATHI_MARKERS for word in latin_words)
        marathi_score += sum(
            word in MARATHI_MARKERS or word.endswith(MARATHI_SUFFIXES)
            for word in devanagari_words
        )
        language = "hindi" if hindi_score > marathi_score else "marathi"
    return language, script


def reply_variant_key(language: str, script: str) -> str:
    """Return the shared canned-template key for a language/script pair."""
    if language == "english":
        return "english"
    return language if script == "devanagari" else f"{language}_romanized"


def localized_reply(
    replies: dict[str, str], language: str, script: str
) -> str:
    """Select a script-matched canned reply with sensible supported fallbacks."""
    key = reply_variant_key(language, script)
    fallback = replies.get(language) or replies.get("english") or next(iter(replies.values()))
    return replies.get(key, fallback)


def detect_language_switch_request(message: str) -> str | None:
    """Return an explicitly requested supported reply language, if present."""
    normalized = " ".join(message.casefold().split())
    if any(re.search(pattern, normalized) for pattern in MARATHI_SWITCH_PATTERNS):
        return "marathi"
    if any(re.search(pattern, normalized) for pattern in HINDI_SWITCH_PATTERNS):
        return "hindi"
    return None


def generate_language_switch_confirmation(
    language: str, script: str = "devanagari"
) -> str:
    """Confirm a persisted language preference without involving an LLM."""
    confirmations = {
        "hindi": "ज़रूर, अब मैं आपको हिंदी में जवाब दूँगा।",
        "hindi_romanized": "Zaroor, ab main aapko Hindi mein jawab dunga.",
        "marathi": "नक्की, आता मी तुम्हाला मराठीत उत्तर देईन।",
        "marathi_romanized": "Nakki, ata mi tumhala Marathit uttar dein.",
    }
    return localized_reply(confirmations, language, script)


def build_greeting_reply(message_text: str, language: str, script: str) -> str:
    """Return a natural, localized reply that echoes the user's greeting style."""
    normalized = " ".join(message_text.casefold().strip("!?.,;:").split())

    GREETING_RESPONSES = {
        "hi": {
            "english": "Hi! How can I help you today?",
            "hindi": "नमस्ते! मैं आपकी कैसे मदद कर सकता हूँ?",
            "hindi_romanized": "Namaste! Main aapki kaise madad kar sakta hoon?",
            "marathi": "नमस्कार! मी तुमची कशी मदत करू शकतो?",
            "marathi_romanized": "Namaskar! Mi tumchi kashi madad karu shakto?",
        },
        "hello": {
            "english": "Hello! How can I help you today?",
            "hindi": "नमस्ते! मैं आपकी कैसे मदद कर सकता हूँ?",
            "hindi_romanized": "Namaste! Main aapki kaise madad kar sakta hoon?",
            "marathi": "नमस्कार! मी तुमची कशी मदत करू शकतो?",
            "marathi_romanized": "Namaskar! Mi tumchi kashi madad karu shakto?",
        },
        "hey": {
            "english": "Hey there! How can I help you?",
            "hindi": "नमस्ते! मैं आपकी कैसे मदद कर सकता हूँ?",
            "hindi_romanized": "Namaste! Main aapki kaise madad kar sakta hoon?",
            "marathi": "नमस्कार! मी तुमची कशी मदत करू शकतो?",
            "marathi_romanized": "Namaskar! Mi tumchi kashi madad karu shakto?",
        },
        "good morning": {
            "english": "Good morning! How can I help you today?",
            "hindi": "सुप्रभात! मैं आपकी कैसे मदद कर सकता हूँ?",
            "hindi_romanized": "Suprabhat! Main aapki kaise madad kar sakta hoon?",
            "marathi": "शुभ सकाळ! मी तुमची कशी मदत करू शकतो?",
            "marathi_romanized": "Shubh sakal! Mi tumchi kashi madad karu shakto?",
        },
        "namaste": {
            "english": "Namaste! How can I help you?",
            "hindi": "नमस्ते! मैं आपकी कैसे मदद कर सकता हूँ?",
            "hindi_romanized": "Namaste! Main aapki kaise madad kar sakta hoon?",
            "marathi": "नमस्कार! मी तुमची कशी मदत करू शकतो?",
            "marathi_romanized": "Namaskar! Mi tumchi kashi madad karu shakto?",
        },
        "namaskar": {
            "english": "Namaskar! How can I help you?",
            "hindi": "नमस्कार! मैं आपकी कैसे मदद कर सकता हूँ?",
            "hindi_romanized": "Namaskar! Main aapki kaise madad kar sakta hoon?",
            "marathi": "नमस्कार! मी तुमची कशी मदत करू शकतो?",
            "marathi_romanized": "Namaskar! Mi tumchi kashi madad karu shakto?",
        },
        "default": {
            "english": "Hello! How can I help you with Pune Metro today?",
            "hindi": "नमस्ते! मैं पुणे मेट्रो के बारे में आपकी क्या मदद कर सकता हूँ?",
            "hindi_romanized": "Namaste! Main Pune Metro ke baare mein aapki kya madad kar sakta hoon?",
            "marathi": "नमस्कार! मी पुणे मेट्रोबद्दल तुमची काय मदत करू शकतो?",
            "marathi_romanized": "Namaskar! Mi Pune Metrobadal tumchi kay madad karu shakto?",
        },
    }

    greeting_key = "default"
    for key in GREETING_RESPONSES:
        if key in normalized:
            greeting_key = key
            break

    return localized_reply(GREETING_RESPONSES[greeting_key], language, script)


async def generate_greeting_reply(message_text: str, language: str, script: str) -> str:
    """Lightly rephrase a fixed greeting without allowing factual additions."""
    fixed_reply = build_greeting_reply(message_text, language, script)
    system_instruction = "\n\n".join(
        [
            _build_language_rule(language, script),
            (
                "Lightly rephrase ONLY the conversational greeting wrapper below. Keep its "
                "meaning, Pune Metro scope, and invitation for help unchanged. Naturally "
                "lead into choosing a topic from the menu below. Do not add "
                "fares, station names, policies, contact details, promises, or emojis. "
                "Return only one short message. "
                f"{BRIEF_LANGUAGE_INSTRUCTION}"
            ),
        ]
    )
    try:
        return await _generate_brief_reply(
            system_instruction,
            f"User greeting: {message_text}\nFixed reply: {fixed_reply}",
        )
    except Exception:
        logger.warning("Greeting rephrase failed; using the fixed greeting", exc_info=True)
        return fixed_reply


def generate_out_of_scope_reply(
    user_message: str, language: str, script: str | None = None
) -> str:
    """Return a concise scope boundary reply in the classified language."""
    script = script or detect_script(user_message)
    replies = {
        "english": (
            "I can only help with Pune Metro related questions — routes, fares, timings, "
            "rules, cards, passes, and customer care. Is there something about Pune Metro "
            "I can help you with?"
        ),
        "hindi": (
            "मैं केवल पुणे मेट्रो से जुड़े प्रश्नों में मदद कर सकता हूँ — जैसे मार्ग, किराया, "
            "समय, नियम, कार्ड, पास और ग्राहक सेवा। क्या मैं पुणे मेट्रो के बारे में किसी "
            "बात में आपकी मदद कर सकता हूँ?"
        ),
        "hindi_romanized": (
            "Main sirf Pune Metro se jude sawalon mein madad kar sakta hoon — route, "
            "kiraya, timing, niyam, card, pass aur customer care. Kya main Pune Metro "
            "ke baare mein aapki koi madad kar sakta hoon?"
        ),
        "marathi": (
            "मी फक्त पुणे मेट्रोशी संबंधित प्रश्नांमध्ये मदत करू शकतो — मार्ग, भाडे, वेळा, "
            "नियम, कार्ड, पास आणि ग्राहक सेवा. पुणे मेट्रोबद्दल मी तुम्हाला काही मदत करू का?"
        ),
        "marathi_romanized": (
            "Mi tumhala phakt Pune Metro shi sambandhit prashnanmadhye madat karu shakto "
            "— marg, bhade, vela, niyam, card, pass ani grahak seva. Pune Metro baddal "
            "mi tumhala kahi madat karu ka?"
        ),
    }
    return localized_reply(replies, language, script)


def generate_unsupported_station_reply(
    user_message: str, preferred_language: str | None = None
) -> str:
    """Explain that a recognized planned Line 3 station cannot yet be used."""
    detected_language, detected_script = resolve_reply_language(user_message)
    detected_language = preferred_language or detected_language
    replies = {
        "english": (
            "The Hinjewadi–Shivajinagar corridor is currently under construction and "
            "not yet open to passengers. Right now you can travel on the Purple Line "
            "(PCMC–Swargate) or the Aqua Line (Vanaz–Ramwadi). I can help plan a route "
            "on these two lines instead."
        ),
        "hindi": (
            "हिंजेवाडी–शिवाजीनगर कॉरिडोर अभी निर्माणाधीन है और यात्रियों के लिए खुला नहीं है। "
            "अभी आप पर्पल लाइन (पीसीएमसी–स्वारगेट) या एक्वा लाइन (वनाज़–रामवाड़ी) पर यात्रा कर सकते हैं। "
            "मैं इन दो लाइनों पर आपका मार्ग तय करने में मदद कर सकती हूँ।"
        ),
        "hindi_romanized": (
            "Hinjewadi–Shivajinagar corridor abhi ban raha hai aur yatriyon ke liye "
            "khula nahi hai. Filhaal aap Purple Line (PCMC–Swargate) ya Aqua Line "
            "(Vanaz–Ramwadi) par safar kar sakte hain. Main in lines par route batane "
            "mein madad kar sakti hoon."
        ),
        "marathi": (
            "हिंजवडी–शिवाजीनगर कॉरिडॉर सध्या बांधकामाधीन आहे आणि प्रवाशांसाठी अजून खुला नाही. "
            "सध्या तुम्ही पर्पल लाईन (पीसीएमसी–स्वारगेट) किंवा अ‍ॅक्वा लाईन (वनाज–रामवाडी) वर प्रवास करू शकता. "
            "या दोन लाईन्सवरील मार्ग नियोजित करण्यात मी मदत करू शकते."
        ),
        "marathi_romanized": (
            "Hinjawadi–Shivajinagar corridor sadhya bandhkamat aahe ani pravashansathi "
            "ajun suru zhalela nahi. Sadhya tumhi Purple Line (PCMC–Swargate) kinva "
            "Aqua Line (Vanaz–Ramwadi) var pravas karu shakta. Ya don lines varcha "
            "route tharvayla mi madat karu shakte."
        ),
    }
    return localized_reply(replies, detected_language, detected_script)


def short_message_intent(message: str) -> str | None:
    """Return a deterministic route for known standalone greetings and closings.

    Short-message routing is intentionally decided before the LLM:
    greeting -> category menu; acknowledgment -> warm closing reply; an otherwise
    ambiguous message -> clarification; anything else -> normal classification.
    """
    normalized = " ".join(message.casefold().strip("!?.,;:").split())
    if normalized in SHORT_GREETINGS:
        return "greeting"
    if normalized in SHORT_ACKNOWLEDGMENTS:
        return "acknowledgment"
    return None


def is_confusion_message(message: str) -> bool:
    """Return whether free text explicitly signals that the user is confused."""
    normalized = " ".join(message.casefold().strip("!?.,;:").split())
    return normalized in CONFUSION_SIGNALS or any(
        normalized.startswith(f"{signal} ")
        for signal in CONFUSION_SIGNALS
        if len(signal) > 3
    )


def normalize_category(category: object) -> str | None:
    """Map legacy category labels to the sole persisted canonical value."""
    if not isinstance(category, str):
        return None
    normalized = category.strip().casefold()
    normalized = CATEGORY_ALIASES.get(normalized, normalized)
    return normalized if normalized in CANONICAL_CATEGORIES else None


def normalize_detected_language(language: object) -> str:
    """Return a supported classifier language, defaulting invalid values to English."""
    if not isinstance(language, str):
        return "english"
    normalized = language.strip().casefold()
    return normalized if normalized in {"english", "hindi", "marathi"} else "english"


def _build_language_rule(
    detected_language: str, detected_script: str = "devanagari"
) -> str:
    """Build the highest-priority language-and-script rule for the current message."""
    rules = {
        "english": "LANGUAGE RULE: The user's message is in English. Respond ONLY in English.",
        "hindi": (
            "LANGUAGE RULE: The user's message is in Hindi. Respond ONLY in Hindi, "
            "using correct Hindi vocabulary and grammar (है, हैं, कितना, कहाँ, क्या, "
            "कैसे, सकते हैं). Do NOT use Marathi words or grammar."
        ),
        "marathi": (
            "LANGUAGE RULE: The user's message is in Marathi. Respond ONLY in Marathi, "
            "using correct Marathi vocabulary and grammar (आहे, आहेत, किती, कुठे, काय, "
            "कसे, शकतो/शकते). Do NOT use Hindi words or grammar."
        ),
        "mixed": (
            "LANGUAGE RULE: The user's message mixes English with Hindi and/or Marathi "
            "(code-mixing), which is normal in this region. Identify the DOMINANT language "
            "of the sentence structure and respond primarily in that language, naturally "
            "including common English loanwords as appropriate."
        ),
    }
    script_rule = (
        " Match the user's SCRIPT too: reply in natural romanized Latin script and do "
        "not switch to Devanagari."
        if detected_language in {"hindi", "marathi"} and detected_script == "latin"
        else (
            " Match the user's SCRIPT too: reply in Devanagari and do not romanize it."
            if detected_language in {"hindi", "marathi"}
            else ""
        )
    )
    return f"{rules[detected_language]}{script_rule}"


def _strip_markdown(text: str) -> str:
    """Convert supported reference Markdown to compact WhatsApp-friendly plain text."""
    cleaned_lines: list[str] = []
    for line in text.splitlines():
        line = re.sub(r"^\s{0,3}#{1,6}\s*", "", line)
        line = re.sub(r"^\s*[-*+]\s+", "• ", line)
        # Remove Markdown emphasis delimiters while retaining their contents.
        line = line.replace("**", "")
        line = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", line)
        cleaned_lines.append(line.strip())

    # Keep at most one blank separator, avoiding prompt bloat from Markdown spacing.
    result: list[str] = []
    for line in cleaned_lines:
        if not line and (not result or not result[-1]):
            continue
        result.append(line)
    return "\n".join(result).strip()


def _sanitize_outbound_text(text: str) -> str:
    """Remove leftover Markdown syntax from a provider-generated WhatsApp reply."""
    return _strip_markdown(text)


def load_reference_data(topics: list[str]) -> str:
    """Load selected reference files as plain text for the LLM prompt."""
    if not topics:
        return ""

    content: list[str] = []
    seen: set[str] = set()
    for topic in topics:
        if topic in seen or topic not in REFERENCE_FILES:
            continue
        seen.add(topic)
        path = PROJECT_ROOT / "data" / REFERENCE_FILES[topic]
        try:
            content.append(_strip_markdown(path.read_text(encoding="utf-8")))
        except OSError:
            logger.exception("Failed to load reference data for topic %s", topic)
    return "\n\n".join(content)


def _normalize_station_name(name: str) -> str:
    """Normalize station text for tolerant station-name matching."""
    normalized = re.sub(r"[^a-z0-9\u0900-\u097F]+", " ", name.casefold()).strip()
    return normalized.replace("\u093c", "")


def resolve_station_alias(name: str) -> str | None:
    """Return an exact fare-matrix station name for user-provided text."""
    normalized = _normalize_station_name(name)
    aliases = {
        _normalize_station_name(station): station
        for station in MATRIX_STATIONS
    }
    aliases.update(
        {
            _normalize_station_name(station): station
            for stations in OPERATIONAL_LINES.values()
            for station in stations
        }
    )
    aliases.update(
        {_normalize_station_name(alias): station for alias, station in STATION_ALIASES.items()}
    )
    aliases.update(
        {
            _normalize_station_name(alias): station
            for alias, station in DEVANAGARI_STATION_ALIASES.items()
        }
    )
    if resolved := aliases.get(normalized):
        return resolved
    # STT sometimes repeats the same station ("Bun Garden, Bun Garden") or
    # includes a harmless qualifier. If the phrase contains exactly one known
    # station, it is still unambiguous and safe to accept.
    embedded_stations = find_station_names(name)
    if len(embedded_stations) == 1:
        return embedded_stations[0]
    # A typo is useful only when the caller supplied a station name by itself.
    # Keep this deliberately conservative so free-form questions cannot turn an
    # unrelated word into a made-up station.
    matches = get_close_matches(normalized, list(aliases), n=1, cutoff=0.88)
    return aliases[matches[0]] if matches else None


def _is_unsupported_station(name: str) -> bool:
    """Return whether a name is a recognized planned, rather than open, station."""
    normalized = _normalize_station_name(name)
    upcoming_aliases = {
        **{
            _normalize_station_name(station): station for station in UPCOMING_STATIONS
        },
        **{
            _normalize_station_name(alias): station
            for alias, station in UPCOMING_STATION_ALIASES.items()
        },
    }
    # Interchange names shared with the two live lines must retain their operational
    # meaning; only a station absent from the fare matrix is unsupported here.
    return normalized in upcoming_aliases and resolve_station_alias(name) is None


def find_unsupported_station_names(text: str) -> list[str]:
    """Find recognized planned Line 3 stations mentioned in a user message."""
    normalized_text = _normalize_station_name(text)
    aliases = {
        **{
            _normalize_station_name(station): station for station in UPCOMING_STATIONS
        },
        **{
            _normalize_station_name(alias): station
            for alias, station in UPCOMING_STATION_ALIASES.items()
        },
    }
    matches: list[tuple[int, str]] = []
    for alias, station in aliases.items():
        match = re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", normalized_text)
        if match and _is_unsupported_station(alias):
            matches.append((match.start(), station))
    return [station for _, station in sorted(set(matches))]


def find_station_names(text: str) -> list[str]:
    """Find fare-matrix station names in message order, including variants."""
    normalized_text = _normalize_station_name(text)
    aliases = {
        **{
            _normalize_station_name(station): station
            for station in MATRIX_STATIONS
        },
        **{_normalize_station_name(alias): station for alias, station in STATION_ALIASES.items()},
        **{
            _normalize_station_name(alias): station
            for alias, station in DEVANAGARI_STATION_ALIASES.items()
        },
    }
    matches: list[tuple[int, str]] = []
    for alias, station in aliases.items():
        # Marathi case suffixes commonly attach directly to a station name
        # (स्वारगेटपर्यंत, स्टेशनपासून). Treat them as boundaries for ASR text.
        suffix = (
            r"(?=पासून|पर्यंत|कडे|कडून|वरून|वरती|वर|हून|ातून|ला|मध्ये|"
            r"चं|चा|ची|चे|च्या|\W|$)"
            if re.search(
            r"[\u0900-\u097F]", alias
            # Sarvam often emits a Latin station followed immediately by a
            # Devanagari case suffix (for example, ``Vanajला``).  A Devanagari
            # character is a valid boundary here, while another Latin letter is
            # still rejected so partial station names cannot match.
            ) else r"(?=$|[^\w]|[\u0900-\u097F])"
        )
        match = re.search(rf"(?<!\w){re.escape(alias)}{suffix}", normalized_text)
        if match:
            matches.append((match.start(), station))

    stations: list[str] = []
    for _, station in sorted(matches):
        if station not in stations:
            stations.append(station)
    return stations


def is_station_or_route_question(text: str) -> bool:
    """Return whether text asks about a station or a journey between stations."""
    normalized = text.casefold()
    # Bare "to" and "from" are not route signals. They occur constantly in
    # feedback ("want to give appreciation") and in time ranges ("from 6 to
    # 11"). Require station vocabulary or an actual journey-shaped phrase.
    explicit_station_language = re.search(
        r"\b(station|route|nearby station|nearest station|closest station)\b|"
        r"स्टेशन|स्थानक|मार्ग|रूट|कहाँ|कुठे",
        normalized,
    )
    journey_language = re.search(
        r"\b(?:go|travel|reach|get)\s+(?:from|to)\b|"
        r"\bfrom\s+[a-z][a-z .'-]{1,50}\s+to\s+[a-z]",
        normalized,
    )
    return bool(explicit_station_language or journey_language)


def generate_unrecognized_station_reply(
    user_message: str, preferred_language: str | None = None
) -> str:
    """Keep an unrecognised station query grounded instead of guessing a name."""
    language, script = resolve_reply_language(user_message)
    language = preferred_language or language
    replies = {
        "english": (
            "I couldn't match that to a Pune Metro station. Please check the name or "
            "send a nearby valid station, such as PCMC, Kasba Peth, District Court, "
            "Vanaz, Pune Railway Station, or Swargate."
        ),
        "hindi": "मुझे वह पुणे मेट्रो स्टेशन नहीं मिला। कृपया नाम जांचें या कोई नज़दीकी मान्य स्टेशन भेजें, जैसे पीसीएमसी, कसबा पेठ, डिस्ट्रिक्ट कोर्ट, वनाज या स्वारगेट।",
        "marathi": "मला ते पुणे मेट्रो स्थानक सापडले नाही. कृपया नाव तपासा किंवा जवळचे वैध स्थानक पाठवा, जसे पीसीएमसी, कसबा पेठ, डिस्ट्रिक्ट कोर्ट, वनाज किंवा स्वारगेट.",
    }
    return localized_reply(replies, language, script)


_STATION_CONTEXT_PATTERN = re.compile(
    r"\b(?:at|from|to|near|via|station)\s+([A-Z][A-Za-z.&()'-]*(?:\s+[A-Z][A-Za-z.&()'-]*){0,4})"
)
_NON_STATION_PROPER_NAMES = frozenset({
    "Pune Metro", "Purple Line", "Aqua Line", "One Pune Card", "NCMC Smart Card",
    "WhatsApp", "Pune Metro Assistant", "Customer Care", "The Purple Line",
    "The Aqua Line",
})


def reply_has_only_canonical_station_names(reply: str) -> bool:
    """Reject English station-like names that are not in the operational list.

    The LLM is already instructed to use grounded data. This final check catches a
    plausible-looking addition such as "MLA Kasba" before it reaches WhatsApp.
    """
    for match in _STATION_CONTEXT_PATTERN.finditer(reply):
        candidate = match.group(1).rstrip(".,;:!?")
        # Trim ordinary sentence continuations after a valid station name.
        words = candidate.split()
        for end in range(len(words), 0, -1):
            if resolve_station_alias(" ".join(words[:end])):
                candidate = " ".join(words[:end])
                break
        if candidate in _NON_STATION_PROPER_NAMES:
            continue
        if resolve_station_alias(candidate) is None:
            return False
    # Catch station-like proper names even where the provider omits the word
    # "station" (for example, the earlier "MLA Kasba" hallucination).
    for candidate in re.findall(r"\b(?:[A-Z]{2,}|[A-Z][a-z]+)(?:\s+(?:[A-Z]{2,}|[A-Z][a-z]+)){1,3}\b", reply):
        if candidate in _NON_STATION_PROPER_NAMES:
            continue
        if resolve_station_alias(candidate) is None:
            return False
    return True


def calculate_fare_estimate(from_station: str, to_station: str) -> str | None:
    """Return an exact fare from the official Pune Metro fare matrix."""
    origin = resolve_station_alias(from_station)
    destination = resolve_station_alias(to_station)
    if origin is None or destination is None:
        return None

    fare = FARE_MATRIX.get(origin, {}).get(destination)
    if not isinstance(fare, int):
        return None
    ncmc_fare = round(fare * (1 - NCMC_DISCOUNT_PERCENT / 100), 2)
    return (
        f"The fare from {origin} to {destination} is ₹{fare} for cash/tokens or "
        f"₹{ncmc_fare:.2f} with an NCMC Smart Card."
    )


def build_route_grounding(from_station: str, to_station: str) -> str | None:
    """Return non-negotiable topology and fare facts for a station-pair reply."""
    origin = resolve_station_alias(from_station)
    destination = resolve_station_alias(to_station)
    if origin is None or destination is None:
        return None

    origin_lines = [line for line, stations in OPERATIONAL_LINES.items() if origin in stations]
    destination_lines = [line for line, stations in OPERATIONAL_LINES.items() if destination in stations]
    if not origin_lines or not destination_lines:
        return None

    shared_line = next((line for line in origin_lines if line in destination_lines), None)
    if shared_line:
        route = f"Travel directly on the {shared_line} from {origin} to {destination}."
    else:
        # There are only two operational lines and District Court is the sole
        # interchange, so this is intentionally not inferred by an LLM.
        route = (
            f"Take the {origin_lines[0]} from {origin} to District Court, transfer there "
            f"to the {destination_lines[0]}, then continue to {destination}."
        )

    fare = calculate_fare_estimate(origin, destination)
    return "\n".join(
        [
            "VERIFIED ROUTE FACTS — reproduce these facts exactly; do not substitute another interchange:",
            route,
            "District Court/Civil Court is the only Purple Line–Aqua Line interchange.",
            "Swargate is a Purple Line terminus, not an Aqua Line interchange.",
            *( [fare] if fare else [] ),
        ]
    )


def check_complaint_status(user_id: int, db: Session) -> str | None:
    """Return a summary of the user's latest tracked complaint, if any."""
    tracking = db.scalar(
        select(ComplaintTracking)
        .where(ComplaintTracking.user_id == user_id)
        .order_by(ComplaintTracking.created_at.desc(), ComplaintTracking.id.desc())
        .limit(1)
    )
    if tracking is None:
        return None
    complaint = db.scalar(
        select(CategoryLog).where(CategoryLog.id == tracking.category_log_id)
    )
    subject = complaint.subcategory if complaint else "your reported issue"
    created_on = tracking.created_at.strftime("%d %B %Y")
    return (
        f"Complaint {tracking.token} is {tracking.status} regarding {subject}, "
        f"logged on {created_on}."
    )


async def generate_reply(
    user_message: str,
    conversation_history: list[dict],
    reference_data: str = "",
    fare_context: str | None = None,
    complaint_status_context: str | None = None,
    preferred_language: str | None = None,
    reference_topics: list[str] | None = None,
) -> str:
    """Generate a response with OpenRouter, falling back to Gemini."""
    resolved_language, detected_script = resolve_reply_language(user_message)
    detected_language = (
        preferred_language
        or detect_language_switch_request(user_message)
        or resolved_language
    )
    logger.info("detect_language(%r) -> %s", user_message, detected_language)
    try:
        return await _generate_reply_openrouter(
            user_message,
            conversation_history,
            reference_data,
            fare_context,
            complaint_status_context,
            detected_language,
            detected_script=detected_script,
            reference_topics=reference_topics,
        )
    except Exception:
        logger.warning("OpenRouter reply generation failed; using Gemini fallback", exc_info=True)
    try:
        return await _generate_reply_gemini(
            user_message,
            conversation_history,
            reference_data,
            fare_context,
            complaint_status_context,
            detected_language,
            detected_script=detected_script,
            reference_topics=reference_topics,
        )
    except Exception:
        logger.exception("Gemini fallback reply generation failed")
        return FALLBACK_MESSAGE


async def generate_category_prompt(
    category: str, last_user_message: str, language: str, script: str | None = None
) -> str:
    """Generate a short, natural invitation to describe a selected category."""
    script = script or detect_script(last_user_message)
    system_instruction = "\n\n".join(
        [
            _build_language_rule(language, script),
            (
                "Write a warm, natural 1-2 sentence Pune Metro support message asking "
                "the user to describe their selected category. Vary the wording, stay PG "
                "and on-topic, and avoid jargon. Return only the message. "
                f"{BRIEF_LANGUAGE_INSTRUCTION}"
            ),
        ]
    )
    return await _generate_brief_reply(system_instruction, f"Selected category: {category}")


async def generate_collection_prompt(
    category: str, field: str, language: str, script: str = "latin"
) -> str:
    """Generate wording only; collection state and required fields stay deterministic."""
    system_instruction = "\n\n".join([
        _build_language_rule(language, script),
        (
            "Write one short, warm support message for a Pune Metro "
            f"{category} form. Ask only for the user's {field}. Do not add facts, "
            "station names, policies, promises, or emojis. Return only the message. "
            f"{BRIEF_LANGUAGE_INSTRUCTION}"
        ),
    ])
    return await _generate_brief_reply(system_instruction, f"Field to collect: {field}")


async def generate_closing_reply(
    user_message: str, preferred_language: str | None = None
) -> str:
    """Generate a brief, warm response to a conversational closing remark."""
    resolved_language, detected_script = resolve_reply_language(user_message)
    detected_language = preferred_language or resolved_language
    system_instruction = "\n\n".join(
        [
            _build_language_rule(detected_language, detected_script),
            (
                "Write a short, warm, natural reply to a user's brief closing or thank-you "
                "message for a Pune Metro assistant. Vary the wording, stay PG and on-topic, "
                "and return only the reply. "
                f"{BRIEF_LANGUAGE_INSTRUCTION}"
            ),
        ]
    )
    return await _generate_brief_reply(system_instruction, user_message)


async def _generate_brief_reply(system_instruction: str, user_message: str) -> str:
    """Generate a brief reply with OpenRouter and Gemini fallback."""
    try:
        return await _generate_brief_reply_openrouter(system_instruction, user_message)
    except Exception:
        logger.warning("OpenRouter brief reply failed; using Gemini fallback", exc_info=True)
    return await _generate_brief_reply_gemini(system_instruction, user_message)


async def classify_message(message: str, pending_category: str | None = None) -> dict:
    """Classify a message with OpenRouter and Gemini fallback."""
    detected_language = detect_language(message)
    logger.info("detect_language(%r) -> %s", message, detected_language)
    deterministic_intent = None if pending_category else short_message_intent(message)
    if deterministic_intent:
        logger.info(
            "Deterministic short-message route for %r: intent=%s, language=%s",
            message,
            deterministic_intent,
            detected_language,
        )
        return {
            "intent": deterministic_intent,
            "detected_language": normalize_detected_language(detected_language),
            "classification_confident": True,
            "categories": [],
            "subcategories": [],
            "extracted_details": {
                "metro_station": None,
                "ticket_number": None,
                "payment_method": None,
                "passenger_name": None,
            },
            "clarification_question": None,
            "clarification_options": None,
            "reference_topics": [],
            "asking_about_complaint_status": False,
        }
    # The classifier is the source of truth for language. Do not prime it with
    # the lightweight heuristic used by deterministic pre-classification routes.
    classification_instruction = CLASSIFICATION_SYSTEM_MESSAGE
    if pending_category:
        classification_instruction += (
            f" The user has already indicated this is a {pending_category}. Focus on "
            "determining the correct subcategory and extracting details; treat the "
            "intent as direct_query unless the message is truly incomprehensible."
        )
    try:
        result = await _classify_message_openrouter(message, classification_instruction)
    except Exception:
        logger.warning("OpenRouter classification failed; using Gemini fallback", exc_info=True)
    else:
        try:
            _validate_classification(result)
            logger.info("Classification result for message %r: %s", message, result)
            return result
        except Exception:
            logger.warning(
                "OpenRouter classification returned an invalid result; using Gemini "
                "fallback. result=%r",
                result,
                exc_info=True,
            )
    try:
        result = await _classify_message_gemini(message, classification_instruction)
        _validate_classification(result)
        logger.info("Classification result for message %r: %s", message, result)
        return result
    except Exception:
        logger.exception("Gemini fallback classification failed")
        return FALLBACK_CLASSIFICATION.copy()


def _build_reply_system_instruction(
    reference_data: str,
    fare_context: str | None,
    complaint_status_context: str | None,
    detected_language: str,
    *,
    detected_script: str = "devanagari",
    reference_topics: list[str] | None = None,
) -> str:
    """Build a provider-neutral system instruction for conversational replies."""
    context_instructions: list[str] = []
    if reference_data:
        context_instructions.append(
            "Use the following reference data to answer accurately. If the answer "
            "isn't in this data, say you're not certain rather than guessing:\n\n"
            f"{reference_data}"
        )
        if "VERIFIED ROUTE FACTS" in reference_data:
            context_instructions.append(
                "For this route/fare question, the verified route facts above are the "
                "source of truth. State the listed line(s), interchange, and fare exactly; "
                "do not invent a different transfer or rely on general knowledge."
            )
    context_instructions.append(
        "STATION GROUNDING: Mention an operational station only when it is present "
        "in the supplied reference data or this canonical list: "
        + ", ".join(sorted(CANONICAL_STATIONS))
        + ". Never autocomplete, translate, or invent a station name. If the user's "
        "station is not in that list, say you cannot recognize it and ask for clarification."
    )
    if fare_context:
        context_instructions.append(
            "The exact fare for this journey has already been calculated: "
            f"{fare_context} Confidently include this fare naturally in your answer "
            "— do not say you're uncertain about the fare, since this number is "
            "confirmed accurate."
        )
    if complaint_status_context:
        context_instructions.append(
            "Use this verified complaint-status information when answering the "
            f"user's question: {complaint_status_context}"
        )
    if detected_language in {"hindi", "marathi"} and detected_script == "devanagari":
        glossary = "; ".join(
            f"{station} -> {devanagari}"
            for station, devanagari in STATION_DEVANAGARI_NAMES.items()
        )
        context_instructions.append(
            "When writing station names in this language, use EXACTLY these phonetic "
            "transliterations (never translate a proper name's meaning): "
            f"{glossary}. Never semantically translate Swargate; use स्वारगेट."
        )
    script_instruction = (
        "Match not only the user's language (Hindi/Marathi/English) but also their "
        "SCRIPT. If the user wrote in romanized Latin script (for example, 'kiti', "
        "'tumhi', or 'ahe' instead of Devanagari), reply in natural romanized Latin "
        "script too; do not switch to Devanagari. If the user wrote in Devanagari, "
        "reply in Devanagari."
    )
    return "\n\n".join(
        [
            _build_language_rule(detected_language, detected_script),
            script_instruction,
            *context_instructions,
            SYSTEM_MESSAGE,
        ]
    )


def _build_reply_history(user_message: str, conversation_history: list[dict]) -> list[dict]:
    """Normalize and complete a chronological OpenAI-compatible conversation."""
    messages = [
        {"role": message["role"], "content": message["content"]}
        for message in conversation_history
        if message.get("role") in {"user", "assistant"}
    ]
    if not messages or messages[-1] != {"role": "user", "content": user_message}:
        messages.append({"role": "user", "content": user_message})
    return messages


async def _generate_reply_openrouter(
    user_message: str,
    conversation_history: list[dict],
    reference_data: str,
    fare_context: str | None,
    complaint_status_context: str | None,
    detected_language: str,
    *,
    detected_script: str = "devanagari",
    reference_topics: list[str] | None = None,
) -> str:
    system_instruction = _build_reply_system_instruction(
        reference_data=reference_data,
        fare_context=fare_context,
        complaint_status_context=complaint_status_context,
        detected_language=detected_language,
        detected_script=detected_script,
        reference_topics=reference_topics,
    )
    messages = [
        {"role": "system", "content": system_instruction},
        *_build_reply_history(user_message, conversation_history),
    ]
    return await _openrouter_chat(messages)


async def _generate_reply_gemini(
    user_message: str,
    conversation_history: list[dict],
    reference_data: str,
    fare_context: str | None,
    complaint_status_context: str | None,
    detected_language: str,
    *,
    detected_script: str = "devanagari",
    reference_topics: list[str] | None = None,
) -> str:
    system_instruction = _build_reply_system_instruction(
        reference_data=reference_data,
        fare_context=fare_context,
        complaint_status_context=complaint_status_context,
        detected_language=detected_language,
        detected_script=detected_script,
        reference_topics=reference_topics,
    )
    contents = [
        {
            "role": "model" if message["role"] == "assistant" else "user",
            "parts": [{"text": message["content"]}],
        }
        for message in _build_reply_history(user_message, conversation_history)
    ]
    return await _gemini_generate(system_instruction, contents)


async def _generate_brief_reply_openrouter(
    system_instruction: str, user_message: str
) -> str:
    return await _openrouter_chat(
        [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": user_message},
        ],
        max_tokens=100,
    )


async def _generate_brief_reply_gemini(system_instruction: str, user_message: str) -> str:
    return await _gemini_generate(
        system_instruction,
        [{"role": "user", "parts": [{"text": user_message}]}],
        generation_config={"maxOutputTokens": 100},
    )


async def _classify_message_openrouter(message: str, system_instruction: str) -> dict:
    response_text = await _openrouter_chat(
        [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": message},
        ],
        response_format={"type": "json_object"},
    )
    return _parse_classification(response_text)


async def _classify_message_gemini(message: str, system_instruction: str) -> dict:
    response_text = await _gemini_generate(
        system_instruction,
        [{"role": "user", "parts": [{"text": message}]}],
        generation_config={"responseMimeType": "application/json"},
    )
    return _parse_classification(response_text)


async def _openrouter_chat(
    messages: list[dict],
    *,
    max_tokens: int | None = None,
    response_format: dict | None = None,
) -> str:
    """Call OpenRouter's OpenAI-compatible chat completions endpoint."""
    payload: dict = {"model": settings.PRIMARY_LLM_MODEL, "messages": messages}
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if response_format is not None:
        payload["response_format"] = response_format
    response = await _get_shared_http_client().post(
        f"{settings.PRIMARY_LLM_BASE_URL.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {settings.PRIMARY_LLM_API_KEY}"},
        json=payload,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    if not isinstance(content, str) or not content.strip():
        raise ValueError("OpenRouter returned an empty response")
    return content.strip()


async def _gemini_generate(
    system_instruction: str,
    contents: list[dict],
    *,
    generation_config: dict | None = None,
) -> str:
    """Call Gemini's generateContent endpoint."""
    payload: dict = {
        "system_instruction": {"parts": [{"text": system_instruction}]},
        "contents": contents,
    }
    if generation_config is not None:
        payload["generationConfig"] = generation_config
    response = await _get_shared_http_client().post(
        (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{settings.FALLBACK_LLM_MODEL}:generateContent"
        ),
        params={"key": settings.FALLBACK_LLM_API_KEY},
        json=payload,
    )
    response.raise_for_status()
    content = response.json()["candidates"][0]["content"]["parts"][0]["text"]
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Gemini returned an empty response")
    return content.strip()


def _parse_classification(response_text: str) -> dict:
    """Strip optional Markdown fencing and parse classification JSON."""
    cleaned = response_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    result = json.loads(cleaned.strip())
    if not isinstance(result, dict):
        raise ValueError("Classification is not an object")
    return _normalize_classification(result)


def _normalize_classification(result: dict) -> dict:
    """Fill optional classification fields omitted by a provider's JSON output."""
    normalized = result.copy()
    normalized["detected_language"] = normalize_detected_language(
        normalized.get("detected_language")
    )
    normalized.setdefault("categories", [])
    if not isinstance(normalized.get("classification_confident"), bool):
        normalized["classification_confident"] = False
    normalized.setdefault("clarification_question", None)
    normalized.setdefault("clarification_options", None)
    if not isinstance(normalized.get("asking_about_complaint_status"), bool):
        normalized["asking_about_complaint_status"] = False

    categories = normalized.get("categories")
    normalized["categories"] = (
        [canonical for item in categories if (canonical := normalize_category(item))]
        if isinstance(categories, list)
        else []
    )

    allowed_subcategories = {
        "Passenger Amenities",
        "Staff Complaints",
        "Refund",
        "AFC & Ticketing",
        "Train Operation & Services",
        "Feeder Services",
        "Others",
    }
    subcategories = normalized.get("subcategories")
    normalized["subcategories"] = (
        [item for item in subcategories if item in allowed_subcategories]
        if isinstance(subcategories, list)
        else []
    )

    allowed_reference_topics = set(REFERENCE_FILES)
    reference_topics = normalized.get("reference_topics")
    normalized["reference_topics"] = (
        [item for item in reference_topics if item in allowed_reference_topics]
        if isinstance(reference_topics, list)
        else []
    )

    details = normalized.get("extracted_details")
    if not isinstance(details, dict):
        details = {}
    normalized["extracted_details"] = {
        "metro_station": details.get("metro_station"),
        "ticket_number": details.get("ticket_number"),
        "payment_method": details.get("payment_method"),
        "passenger_name": details.get("passenger_name"),
    }
    return normalized


def _validate_classification(result: object) -> None:
    """Raise ValueError when Gemini's classification does not match the contract."""
    if not isinstance(result, dict):
        raise ValueError("Classification is not an object")
    expected_keys = {
        "intent",
        "detected_language",
        "classification_confident",
        "categories",
        "subcategories",
        "extracted_details",
        "clarification_question",
        "clarification_options",
        "reference_topics",
        "asking_about_complaint_status",
    }
    if set(result) != expected_keys:
        raise ValueError("Classification has unexpected fields")
    intent = result.get("intent")
    if intent not in {
        "greeting",
        "ambiguous",
        "direct_query",
        "acknowledgment",
        "out_of_scope",
    }:
        raise ValueError("Invalid classification intent")
    if not isinstance(result["classification_confident"], bool):
        raise ValueError("Invalid classification confidence")
    if result["detected_language"] not in {"english", "hindi", "marathi"}:
        raise ValueError("Invalid detected language")
    if not isinstance(result["categories"], list) or not all(
        item in CANONICAL_CATEGORIES for item in result["categories"]
    ):
        raise ValueError("Invalid categories")
    allowed_subcategories = {
        "Passenger Amenities",
        "Staff Complaints",
        "Refund",
        "AFC & Ticketing",
        "Train Operation & Services",
        "Feeder Services",
        "Others",
    }
    if not isinstance(result["subcategories"], list) or not all(
        item in allowed_subcategories for item in result["subcategories"]
    ):
        raise ValueError("Invalid subcategories")
    details = result["extracted_details"]
    detail_keys = {"metro_station", "ticket_number", "payment_method", "passenger_name"}
    if not isinstance(details, dict) or set(details) != detail_keys:
        raise ValueError("Invalid extracted details")
    if not all(value is None or isinstance(value, str) for value in details.values()):
        raise ValueError("Invalid extracted-detail value")
    if result["clarification_question"] is not None and not isinstance(
        result["clarification_question"], str
    ):
        raise ValueError("Invalid clarification question")
    options = result["clarification_options"]
    if options is not None and (
        not isinstance(options, list) or not all(isinstance(item, str) for item in options)
    ):
        raise ValueError("Invalid clarification options")
    allowed_reference_topics = set(REFERENCE_FILES)
    if not isinstance(result["reference_topics"], list) or not all(
        topic in allowed_reference_topics for topic in result["reference_topics"]
    ):
        raise ValueError("Invalid reference topics")
    if not isinstance(result["asking_about_complaint_status"], bool):
        raise ValueError("Invalid complaint-status flag")
    if intent in {"ambiguous", "acknowledgment", "out_of_scope"}:
        if result["categories"] or result["subcategories"] or any(
            value is not None for value in details.values()
        ):
            raise ValueError("Empty-field intent contains classification details")
        if intent == "acknowledgment":
            if not result["classification_confident"]:
                raise ValueError("Acknowledgment result is not confident")
            if result["clarification_question"] is not None or options is not None:
                raise ValueError("Acknowledgment result contains clarification")
            return
        if intent == "out_of_scope":
            if not result["classification_confident"]:
                raise ValueError("Out-of-scope result is not confident")
            if result["clarification_question"] is not None or options is not None:
                raise ValueError("Out-of-scope result contains clarification")
            return
        if result["classification_confident"]:
            raise ValueError("Ambiguous result is marked confident")
        if result["clarification_question"] is not None or options is not None:
            raise ValueError("Ambiguous result contains clarification")
    elif result["clarification_question"] is not None or options is not None:
        raise ValueError("Non-ambiguous result contains clarification")
    elif not result["classification_confident"]:
        raise ValueError("Non-ambiguous result is not confident")
    elif intent == "direct_query" and not result["categories"]:
        raise ValueError("Confident direct query lacks a category")
