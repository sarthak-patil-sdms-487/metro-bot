"""Channel-neutral deterministic collection for complaints, suggestions and appreciation."""

import hashlib
import re
import unicodedata
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import CategoryLog, Conversation, TicketDetails
from app.services.complaint_tracking import create_complaint_tracking
from app.services.llm_client import find_station_names, resolve_station_alias


CATEGORY_WORDS = {
    "complaint": (
        "complaint", "issue", "problem", "trouble",
        "कंप्लेंट", "कॉम्प्लेंट", "तक्रार", "त्रास",
        "शिकायत", "समस्या", "परेशानी",
    ),
    "suggestion": ("suggestion", "suggest", "idea", "सजेशन", "सूचना", "सुझाव"),
    "appreciation": ("appreciation", "appreciate", "praise", "अप्रिशिएशन", "कौतुक", "प्रशंसा", "तारीफ"),
}


def detect_collection_category(text: str) -> str | None:
    normalized = text.casefold()
    return next(
        (category for category, words in CATEGORY_WORDS.items() if any(word in normalized for word in words)),
        None,
    )


def _language(conversation: Conversation) -> str:
    return conversation.preferred_language or "english"


def _localized(language: str, english: str, hindi: str, marathi: str) -> str:
    return {"hindi": hindi, "marathi": marathi}.get(language, english)


def _category_label(category: str, language: str) -> str:
    labels = {
        "complaint": {"english": "complaint", "hindi": "शिकायत", "marathi": "तक्रार"},
        "suggestion": {"english": "suggestion", "hindi": "सुझाव", "marathi": "सूचना"},
        "appreciation": {"english": "appreciation", "hindi": "प्रशंसा", "marathi": "कौतुक"},
    }
    return labels.get(category, labels["complaint"]).get(language, category)


def _pick_phrase(
    conversation: Conversation,
    purpose: str,
    choices: tuple[str, ...],
    seed: str = "",
) -> str:
    """Choose stable conversational variety without making field logic random."""
    identity = f"{conversation.id or conversation.user_id}:{purpose}:{seed}"
    index = int(hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8], 16)
    return choices[index % len(choices)]


def _first_name(conversation: Conversation) -> str:
    full_name = conversation.complaint_collection_full_name or ""
    return full_name.split()[0] if full_name else ""


def _spoken_contact(value: str) -> str:
    digits = _contact_digits(value)
    return f"{digits[:5]} {digits[5:]}" if len(digits) == 10 else value


def start_collection(
    conversation: Conversation, category: str, initial_text: str, language: str | None = None
) -> str:
    if language:
        conversation.preferred_language = language
    conversation.pending_category = category
    # Every submission in a long-running call is an independent dashboard item.
    # Never let values from a completed or interrupted collection leak into it.
    conversation.complaint_collection_full_name = None
    conversation.complaint_collection_contact_number = None
    conversation.complaint_collection_station = None
    conversation.complaint_collection_description = None
    _extract_all_fields(conversation, initial_text)
    prompt = _next_collection_prompt(conversation)
    if not conversation.complaint_collection_description:
        return prompt
    language = _language(conversation)
    acknowledgements = {
        "complaint": {
            "english": ("I'm sorry you had to deal with that.", "That sounds frustrating; let's get it recorded properly."),
            "hindi": ("आपको यह परेशानी हुई, इसका मुझे अफ़सोस है।", "यह परेशान करने वाली बात है; इसे ठीक से दर्ज करते हैं।"),
            "marathi": ("तुम्हाला हा त्रास झाला, याचं मला वाईट वाटतं.", "ही नक्कीच त्रासदायक बाब आहे; ती नीट नोंदवूया."),
        },
        "suggestion": {
            "english": ("That's a useful suggestion.", "Thanks—that's helpful feedback."),
            "hindi": ("यह उपयोगी सुझाव है।", "धन्यवाद—यह काम की प्रतिक्रिया है।"),
            "marathi": ("ही उपयुक्त सूचना आहे.", "धन्यवाद—हा उपयोगी अभिप्राय आहे."),
        },
        "appreciation": {
            "english": ("That's lovely to hear.", "I'm glad you had a good experience."),
            "hindi": ("यह सुनकर अच्छा लगा।", "अच्छा लगा कि आपका अनुभव बढ़िया रहा।"),
            "marathi": ("हे ऐकून छान वाटलं.", "तुमचा अनुभव चांगला होता, हे ऐकून आनंद झाला."),
        },
    }
    category_choices = acknowledgements.get(category, acknowledgements["complaint"])
    acknowledgement = _pick_phrase(
        conversation,
        "opening_ack",
        category_choices.get(language, category_choices["english"]),
        initial_text,
    )
    return f"{acknowledgement} {prompt}"


def _contact_digits(text: str) -> str:
    digits = "".join(
        str(unicodedata.digit(char)) for char in text if char.isdigit()
    )
    if not digits:
        number_words = {
            "zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3",
            "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
            "शून्य": "0", "एक": "1", "दो": "2", "दोन": "2", "तीन": "3", "चार": "4",
            "पांच": "5", "पाच": "5", "छह": "6", "छः": "6", "छ": "6",
            "सहा": "6", "सात": "7", "आठ": "8", "नौ": "9", "नऊ": "9",
        }
        digits = "".join(number_words.get(word, "") for word in re.findall(r"[\w\u0900-\u097f]+", text.casefold()))
    return digits


def _contact(text: str) -> str | None:
    digits = _contact_digits(text)
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    return digits if len(digits) == 10 else None


parse_contact_number = _contact


def _extract_name(text: str) -> str | None:
    patterns = (
        r"(?:my (?:full )?name is|name is)\s+([a-z][a-z '-]{1,60}?)(?:[,.!?]|$)",
        r"माझ(?:ं|े) पूर्ण नाव\s+आहे\s+([^,.।!?]{2,60}?)(?:[,.।!?]|$)",
        r"माझ(?:ं|े) नाव\s+आहे\s+([^,.।!?]{2,60}?)(?:[,.।!?]|$)",
        r"माझ(?:ं|े) नाव\s+([^,.।!?]{2,60}?)(?:\s+आहे|[,.।!?]|$)",
        r"मेरा पूरा नाम\s+(?:है\s+)?([^,.।!?]{2,60}?)(?:[,.।!?]|$)",
        r"मेरा नाम\s+है\s+([^,.।!?]{2,60}?)(?:[,.।!?]|$)",
        r"मेरा नाम\s+([^,.।!?]{2,60}?)(?:\s+है|[,.।!?]|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            candidate = " ".join(match.group(1).split()).strip()
            return candidate if _is_valid_name(candidate) else None
    return None


def _is_valid_name(value: str) -> bool:
    normalized = " ".join(value.casefold().strip(" .!?।").split())
    invalid = {
        "hmm", "hm", "okay", "ok", "yes", "no", "हो", "नाही", "हां", "हाँ",
        "काय", "क्या", "what", "आहे", "आहेत", "है", "हैं", "is", "am",
        "माझं नाव काय आहे", "माझे नाव काय आहे", "मेरा नाम क्या है",
    }
    meta = ("already told", "आधीच", "पहले ही", "नाव काय", "नाम क्या")
    non_name_terms = (
        "stall", "shop", "washroom", "toilet", "station", "complaint", "suggestion",
        "स्टॉल", "शॉप", "वॉशरूम", "टॉयलेट", "स्टेशन", "कंप्लेंट", "सजेशन",
        "कचरा", "घाण", "गंदा", "शिकायत", "सुझाव", "तक्रार", "सूचना",
    )
    # These are grammatical/request words, not personal names.  In particular,
    # STT often returns an incomplete answer such as "माझं नाव आहे" and the old
    # extractor consequently stored "आहे" as the caller's name.
    non_name_words = {
        "i", "my", "name", "is", "am", "the", "this", "that", "please", "tell",
        "माझं", "माझे", "माझा", "नाव", "आहे", "आहेत", "मी", "सांगा", "सांगतो", "सांगते",
        "मेरा", "मेरी", "नाम", "है", "हैं", "मैं", "बताइए", "बताता", "बताती",
    }
    words = normalized.split()
    valid_name_characters = all(
        all(
            unicodedata.category(char).startswith(("L", "M"))
            or char in "-'’"
            for char in word
        )
        for word in words
    )
    return (
        2 <= len(normalized) <= 80
        and 1 <= len(words) <= 5
        and len("".join(words)) >= 2
        and normalized not in invalid
        and not any(word in normalized for word in ("नाव", "नाम"))
        and not any(phrase in normalized for phrase in meta)
        and not any(term in normalized for term in non_name_terms)
        and not any(word in non_name_words for word in words)
        and not any(char.isdigit() for char in normalized)
        and valid_name_characters
        and not re.search(r"[,;:/@#]|\b(?:want|need|have|register|change)\b", normalized)
    )


def _normalize_description(text: str) -> str:
    """Keep an officer-friendly issue statement, without conversational framing."""
    value = " ".join(text.strip().split())
    # Remove only explicit reporting/request wrappers.  The incident facts and
    # their original language remain untouched, making this safe for arbitrary
    # complaint topics and for English, Hindi, and Marathi.
    wrappers = (
        r"^(?:hi|hello|hey)[,\s]+",
        r"^(?:please\s+)?(?:i\s+)?(?:want|need|would like)\s+to\s+(?:make|file|register|give)\s+(?:a\s+)?(?:complaint|suggestion)(?:\s+(?:that|about))?\s*[:,.-]?\s*",
        r"^(?:my\s+)?(?:complaint|issue|problem|suggestion)\s+is(?:\s+that)?\s*[:,.-]?\s*",
        r"^(?:मला|माझी|माझी एक)\s+(?:कंप्लेंट|कॉम्प्लेंट|तक्रार|सूचना)\s+(?:करायची|नोंदवायची|द्यायची)\s+(?:आहे|होती)(?:\s+की)?\s*[:,.-]?\s*",
        r"^(?:मेरी|मुझे)\s+(?:एक\s+)?(?:शिकायत|कंप्लेंट|समस्या|सुझाव)\s+(?:दर्ज\s+)?(?:करनी|देनी)\s+(?:है|थी)(?:\s+कि)?\s*[:,.-]?\s*",
    )
    previous = None
    while value and value != previous:
        previous = value
        for pattern in wrappers:
            value = re.sub(pattern, "", value, count=1, flags=re.I).strip()
    # Remove hesitation/filler at sentence boundaries and repeated whitespace,
    # while retaining every substantive fact the passenger supplied.
    value = re.sub(
        r"^(?:(?:um+|uh+|actually|basically|देखिए|मतलब|अं|अरे|बरं|तर)\s*[,.-]?\s*)+",
        "",
        value,
        flags=re.I,
    ).strip()
    # Station is stored in its own validated field. Drop a dangling locative
    # pronoun so the dashboard description reads as the actionable issue rather
    # than "there ..." without context.
    value = re.sub(
        r"^(?:there|at\s+that\s+station|वहाँ|वहां|उस\s+स्टेशन\s+पर|"
        r"तिथे|त्या\s+स्थानकावर)\s*[,;:-]?\s*",
        "",
        value,
        count=1,
        flags=re.I,
    ).strip()
    return value.strip(" ,;:-")


def _extract_description(text: str) -> str | None:
    issue_markers = (
        "dirty", "not working", "broken", "rude", "unsafe", "problem", "issue",
        "garbage", "dustbin", "cleaning", "water", "leak", "crowd", "delay",
        "shop", "stall", "improve", "add", "helpful", "excellent", "good service",
        "घाण", "कचरा", "डस्टबिन", "स्वच्छ", "पाणी", "गळती", "गर्दी", "उशीर",
        "दुकान", "स्टॉल", "सुधार", "सुविधा", "मदत", "उत्कृष्ट", "छान सेवा",
        "बंद", "चालत नाही", "तुट", "खराब", "समस्या", "गंद", "कूड़ा", "काम नहीं",
        "खोलिए", "बेहतर", "अच्छी सेवा", "बहुत मदद",
    )
    parts = [part.strip() for part in re.split(r"[.!?।]+", text) if part.strip()]
    relevant = [part for part in parts if any(marker in part.casefold() for marker in issue_markers)]
    description = ". ".join(relevant) if relevant else None
    return _normalize_description(description) if description else None


def _is_meaningful_description(text: str) -> bool:
    normalized = " ".join(text.casefold().strip(" .!?।").split())
    fillers = {
        "hmm", "hm", "ok", "okay", "yes", "no", "हो", "नाही", "हाँ", "हां",
        "complaint", "suggestion", "appreciation", "तक्रार", "सूचना", "शिकायत", "सुझाव",
    }
    words = re.findall(r"[\w\u0900-\u097f]+", normalized)
    return normalized not in fillers and (len(words) >= 4 or len(normalized) >= 20)


def _extract_all_fields(conversation: Conversation, text: str) -> None:
    if not conversation.complaint_collection_full_name:
        conversation.complaint_collection_full_name = _extract_name(text)
    if _contact(conversation.complaint_collection_contact_number or "") is None:
        if extracted_contact := _contact(text):
            conversation.complaint_collection_contact_number = extracted_contact
    if not conversation.complaint_collection_station:
        stations = find_station_names(text)
        if stations:
            conversation.complaint_collection_station = stations[0]
    description = _extract_description(text)
    if description:
        existing = conversation.complaint_collection_description or ""
        if description.casefold() not in existing.casefold():
            conversation.complaint_collection_description = " ".join(
                part for part in (existing, description) if part
            )


def _next_collection_prompt(
    conversation: Conversation,
    *,
    repair: bool = False,
    continuation: bool = False,
    seed: str = "",
) -> str:
    category = conversation.pending_category or "complaint"
    language = _language(conversation)
    label = _category_label(category, language)
    name = _first_name(conversation)
    if not conversation.complaint_collection_full_name:
        conversation.complaint_collection_state = "collecting_name"
        choices = {
            "english": (
                f"I can help you record that {label}. May I have your full name?",
                f"Let's get that {label} noted properly. What's your full name?",
                "Of course. To begin, could you tell me your full name?",
            ) if not repair else (
                "I didn't catch the name clearly. Could you say your full name once more?",
                "Sorry, the name wasn't clear on my end. What's your full name?",
                "Let me make sure I get this right. Please repeat your full name.",
            ),
            "hindi": (
                f"मैं आपकी {label} दर्ज कर देती हूँ। सबसे पहले, आपका पूरा नाम क्या है?",
                f"ठीक है, आपकी {label} सही तरीके से दर्ज करते हैं। कृपया अपना पूरा नाम बताइए।",
                "ज़रूर। शुरुआत आपके पूरे नाम से करते हैं—आपका नाम क्या है?",
            ) if not repair else (
                "माफ़ कीजिए, नाम साफ़ सुनाई नहीं दिया। कृपया अपना पूरा नाम फिर से बताइए।",
                "मैं नाम ठीक से दर्ज करना चाहती हूँ। आपका पूरा नाम एक बार फिर बताएँगे?",
                "नाम समझ नहीं आया। कृपया थोड़ा स्पष्ट करके अपना पूरा नाम बताइए।",
            ),
            "marathi": (
                f"मी तुमची {label} नोंदवते. सुरुवातीला तुमचं पूर्ण नाव सांगाल का?",
                f"ठीक आहे, तुमची {label} नीट नोंदवूया. तुमचं पूर्ण नाव काय आहे?",
                "नक्की. आधी तुमचं पूर्ण नाव सांगा.",
            ) if not repair else (
                "माफ करा, नाव नीट ऐकू आलं नाही. तुमचं पूर्ण नाव पुन्हा सांगाल का?",
                "नाव अचूक नोंदवायचं आहे. कृपया पूर्ण नाव पुन्हा सांगा.",
                "मला नाव समजलं नाही. जरा स्पष्टपणे तुमचं पूर्ण नाव सांगा.",
            ),
        }
        return _pick_phrase(
            conversation, "name_repair" if repair else "name", choices.get(language, choices["english"]), seed
        )
    if not conversation.complaint_collection_contact_number:
        conversation.complaint_collection_state = "collecting_contact"
        choices = {
            "english": ((
                "What's the best 10-digit number to contact you on?",
                "Could you share your 10-digit mobile number?",
                "What 10-digit contact number should I add?",
            ) if continuation else (
                f"Thanks, {name}. What's the best 10-digit number to contact you on?",
                f"Got it, {name}. Could you share your 10-digit mobile number?",
                f"All right, {name}. What 10-digit contact number should I add?",
            )),
            "hindi": ((
                "अब अपना 10 अंकों का मोबाइल नंबर बताइए।",
                "आपसे संपर्क के लिए 10 अंकों का नंबर क्या है?",
                "कृपया अपना 10 अंकों का संपर्क नंबर बताएँ।",
            ) if continuation else (
                f"ठीक है, {name}। अब अपना 10 अंकों का मोबाइल नंबर बताइए।",
                f"नाम दर्ज हो गया, {name}। आपसे संपर्क के लिए 10 अंकों का नंबर क्या है?",
                f"अच्छा, {name}। कृपया अपना 10 अंकों का संपर्क नंबर बताएँ।",
            )),
            "marathi": ((
                "आता तुमचा 10 अंकी मोबाईल नंबर सांगा.",
                "संपर्कासाठी 10 अंकी नंबर कोणता आहे?",
                "तुमचा 10 अंकी संपर्क क्रमांक सांगाल का?",
            ) if continuation else (
                f"ठीक आहे, {name}. आता तुमचा 10 अंकी मोबाईल नंबर सांगा.",
                f"नाव नोंदवलं, {name}. संपर्कासाठी 10 अंकी नंबर कोणता आहे?",
                f"छान, {name}. तुमचा 10 अंकी संपर्क क्रमांक सांगाल का?",
            )),
        }
        return _pick_phrase(
            conversation, "contact", choices.get(language, choices["english"]), seed
        )
    if not conversation.complaint_collection_station:
        conversation.complaint_collection_state = "collecting_station"
        choices = {
            "english": (
                "Which Pune Metro station is this about?",
                "At which station did this happen?",
                "And which Pune Metro station does this relate to?",
            ),
            "hindi": (
                "यह किस पुणे मेट्रो स्टेशन की बात है?",
                "यह किस स्टेशन पर हुआ था?",
                "अब स्टेशन का नाम बताइए—यह मामला कहाँ का है?",
            ),
            "marathi": (
                "ही बाब कोणत्या पुणे मेट्रो स्थानकाची आहे?",
                "हे कोणत्या स्थानकावर घडलं?",
                "आता स्थानकाचं नाव सांगा—ही बाब नेमकी कुठली आहे?",
            ),
        }
        return _pick_phrase(
            conversation, "station", choices.get(language, choices["english"]), seed
        )
    if not conversation.complaint_collection_description:
        conversation.complaint_collection_state = "collecting_description"
        choices = {
            "english": (
                f"Now tell me in your own words what you'd like us to note for this {label}.",
                "I'm listening—please tell me what happened and what you need us to look into.",
                f"What would you like the team to know about this {label}?",
            ),
            "hindi": (
                f"अब अपने शब्दों में बताइए कि इस {label} में क्या दर्ज करना है।",
                "जी, मैं सुन रही हूँ—क्या हुआ और आप क्या कार्रवाई चाहते हैं?",
                f"इस {label} के बारे में टीम को क्या जानकारी देनी है?",
            ),
            "marathi": (
                "आता तुमच्या शब्दांत सांगा, या नोंदीत नेमकं काय नमूद करायचं आहे?",
                "हो, मी ऐकतेय—काय झालं आणि आम्ही कशाकडे लक्ष द्यावं?",
                "याबद्दल टीमला नेमकी काय माहिती द्यायची आहे?",
            ),
        }
        return _pick_phrase(
            conversation, "description", choices.get(language, choices["english"]), seed
        )
    conversation.complaint_collection_state = "confirming"
    return _summary(conversation)


def _acknowledge_and_continue(
    conversation: Conversation, field: str, value: str
) -> str:
    """Repeat a captured value before advancing to the next missing field."""
    next_prompt = _next_collection_prompt(conversation, continuation=True)
    # The final summary already repeats every value, so avoid saying it twice.
    if conversation.complaint_collection_state == "confirming":
        return next_prompt
    language = _language(conversation)
    spoken_value = _spoken_contact(value) if field == "contact" else value
    acknowledgements = {
        "name": _pick_phrase(
            conversation,
            "ack_name",
            {
                "english": (f"I have your name as {value}.", f"Got it—{value}.", f"That's {value}, noted."),
                "hindi": (f"आपका नाम {value} दर्ज किया है।", f"ठीक है—{value}।", f"मैंने नाम {value} नोट कर लिया है।"),
                "marathi": (f"तुमचं नाव {value} नोंदवलं आहे.", f"ठीक आहे—{value}.", f"मी {value} हे नाव नोंदवलं आहे."),
            }.get(language, (f"I have your name as {value}.",)),
            value,
        ),
        "contact": _pick_phrase(
            conversation,
            "ack_contact",
            {
                "english": (f"I have the number as {spoken_value}.", f"That's {spoken_value}, noted.", f"Got it—{spoken_value}."),
                "hindi": (f"नंबर {spoken_value} दर्ज किया है।", f"ठीक है—{spoken_value}।", f"मैंने {spoken_value} नोट कर लिया है।"),
                "marathi": (f"नंबर {spoken_value} नोंदवला आहे.", f"ठीक आहे—{spoken_value}.", f"मी {spoken_value} नोंदवला आहे."),
            }.get(language, (f"I have the number as {spoken_value}.",)),
            value,
        ),
        "station": _pick_phrase(
            conversation,
            "ack_station",
            {
                "english": (f"Okay, this is about {value} station.", f"Got it—{value} station.", f"I've noted {value} as the station."),
                "hindi": (f"ठीक है, यह {value} स्टेशन की बात है।", f"समझ गई—{value} स्टेशन।", f"मैंने स्टेशन {value} दर्ज किया है।"),
                "marathi": (f"ठीक आहे, ही बाब {value} स्थानकाची आहे.", f"समजलं—{value} स्थानक.", f"मी {value} स्थानक नोंदवलं आहे."),
            }.get(language, (f"Okay, this is about {value} station.",)),
            value,
        ),
    }
    acknowledgement = acknowledgements.get(field)
    return f"{acknowledgement} {next_prompt}" if acknowledgement else next_prompt


def _yes(text: str) -> bool:
    normalized = " ".join(re.sub(r"[,.!?;:।]+", " ", text.casefold()).split())
    explicit = {
        "yes", "yeah", "yep", "हो", "हां", "हाँ", "जी हाँ", "करा", "नोंदवा",
        "go ahead", "please proceed", "proceed", "yes proceed", "yes you can", "yes please",
        "हो करा", "हो तुम्ही करू शकता", "तुम्ही करू शकता", "पुढे जा",
        "हो पुढे जा", "हो पुढे जावा", "पुढे जावा", "कृपया पुढे जा",
        "पुढं जा", "हो पुढं जा", "पुढं जावा", "पुढं जाऊ द्या",
        "हाँ कर दीजिए", "हां कर दीजिए", "आप कर सकते हैं", "आगे बढ़िए",
    }
    confirmation_phrases = (
        "everything is correct", "everything's correct", "all correct", "all good",
        "you can register", "you can go ahead", "you can do it", "please register", "yes do it",
        "सब सही है", "सभी सही है", "सब ठीक है", "जानकारी सही है",
        "दर्ज कर दीजिए", "दर्ज करिए", "दर्ज करें", "नोंद कर दीजिए",
        "आप दर्ज कर सकते हैं", "आप कर सकते हैं",
        "आगे बढ़", "आगे बढ़",
        "सगळं बरोबर आहे", "सर्व बरोबर आहे", "माहिती बरोबर आहे",
        "नोंदवू शकता", "नोंद करा", "तुम्ही नोंद करू शकता", "तुम्ही करू शकता",
        "पुढे जा", "पुढे जाऊ", "पुढं जा", "पुढं जाऊ",
    )
    return normalized in explicit or any(
        phrase in normalized for phrase in confirmation_phrases
    )


def _cancel_requested(text: str) -> bool:
    normalized = " ".join(re.sub(r"[,.!?;:।]+", " ", text.casefold()).split())
    exact = {
        "cancel", "cancel it", "do not register", "don't register", "stop",
        "रद्द", "रद्द करा", "नोंदवू नका", "थांबा",
        "रद्द करें", "दर्ज मत करें", "मत दर्ज कीजिए", "रोकिए",
    }
    phrases = (
        "cancel this", "cancel the current", "do not proceed", "don't proceed",
        "पुढे नको", "नोंद करू नका", "नोंद नको", "रद्द म्हटल",
        "आगे मत", "दर्ज नहीं करना", "रद्द कर",
    )
    return normalized in exact or normalized.startswith("रद्द ") or any(
        phrase in normalized for phrase in phrases
    )


def _another_submission_requested(text: str) -> str | None:
    """Detect an attempt to begin another item before the current one is saved."""
    category = detect_collection_category(text)
    if category is None:
        return None
    normalized = text.casefold()
    continuation_markers = (
        "another", "one more", "next complaint", "again",
        "अजून एक", "आणखी एक", "पुन्हा", "दुसरी", "दुसरा",
        "एक और", "दूसरी", "दूसरा", "फिर से",
    )
    return category if any(marker in normalized for marker in continuation_markers) else None


def _finish_current_before_next_reply(conversation: Conversation) -> str:
    current = _category_label(conversation.pending_category or "complaint", _language(conversation))
    return _localized(
        _language(conversation),
        f"I can take another item in this same call. First, the current {current} is ready but not registered yet. Please say proceed to register it, or cancel the current one. Then tell me your next complaint, suggestion, appreciation, or enquiry.",
        f"मैं इसी कॉल में दूसरी बात भी दर्ज कर सकती हूँ। पहले मौजूदा {current} तैयार है, लेकिन अभी दर्ज नहीं हुई है। इसे दर्ज करने के लिए आगे बढ़िए कहें, या मौजूदा रिकॉर्ड रद्द करें। उसके बाद अपनी अगली शिकायत, सुझाव, प्रशंसा या पूछताछ बताइए।",
        f"मी याच कॉलमध्ये आणखी एक नोंद घेऊ शकते. आधी सध्याची {current} तयार आहे, पण अजून नोंदवलेली नाही. ती नोंदवण्यासाठी पुढे जा म्हणा, किंवा सध्याची नोंद रद्द करा. त्यानंतर पुढची तक्रार, सूचना, कौतुक किंवा चौकशी सांगा.",
    )


def _wants_change(text: str) -> bool:
    normalized = text.casefold().strip(" .!?।")
    if normalized in {"no", "nope", "नाही", "नहीं", "change", "बदल", "बदला"}:
        return True
    return any(
        phrase in normalized
        for phrase in (
            "want to change", "need to change", "have to change", "make a change",
            "change the information", "change information", "not correct", "incorrect",
            "बदलायचं", "बदल करायचा", "माहिती बदला", "दुरुस्त करा", "चुकीचं आहे",
            "बदलना", "जानकारी बदल", "सुधारना", "सुधार दीजिए", "गलत है",
        )
    )


def _additional_detail_requested(text: str) -> bool:
    """Require an explicit add/continuation cue before changing confirmed details."""
    normalized = " ".join(text.casefold().strip(" .!?।").split())
    return bool(re.search(
        r"\b(?:also|add|another detail|one more detail|forgot to mention|in addition)\b|"
        r"(?:आणखी|हेही|पण|सुद्धा|जोड|नमूद)|"
        r"(?:और|भी|जोड़|एक और बात|बताना भूल)",
        normalized,
    ))


def is_additional_collection_detail(text: str) -> bool:
    """Public state-router predicate for an explicitly requested detail append."""
    return _additional_detail_requested(text) and _is_meaningful_description(text)


def _append_confirmed_description(conversation: Conversation, text: str) -> bool:
    """Append a caller-authorized detail without replacing the existing complaint."""
    detail = text.strip(" .,:;-।")
    existing = (conversation.complaint_collection_description or "").strip()
    if not _additional_detail_requested(detail) or not _is_meaningful_description(detail):
        return False
    if detail.casefold() not in existing.casefold():
        conversation.complaint_collection_description = " ".join(
            part for part in (existing, detail) if part
        )
    return True


def collection_resume_reply(conversation: Conversation) -> str:
    """Return the current collection summary without mutating or losing its state."""
    if conversation.complaint_collection_state == "confirming":
        return _summary(conversation)
    return _next_collection_prompt(conversation, continuation=True)


def _correction_field(text: str) -> str | None:
    normalized = text.casefold()
    field_terms = {
        "name": ("name", "नाव", "नाम"),
        "contact": ("number", "contact", "mobile", "phone", "नंबर", "क्रमांक", "मोबाईल", "मोबाइल"),
        "station": ("station", "location", "स्थानक", "स्टेशन", "जगह"),
        "description": (
            "description", "complaint", "issue", "details", "तक्रार", "वर्णन",
            "माहिती", "शिकायत", "समस्या", "विवरण", "जानकारी",
        ),
    }
    return next(
        (field for field, terms in field_terms.items() if any(term in normalized for term in terms)),
        None,
    )


def _strip_correction_prefix(text: str, field: str) -> str:
    terms = {
        "name": r"name|नाव|नाम",
        "contact": r"(?:contact|mobile|phone)?\s*number|contact|mobile|phone|नंबर|क्रमांक|मोबाईल|मोबाइल",
        "station": r"station|location|स्थानक|स्टेशन|जगह",
        "description": r"description|complaint|issue|details|तक्रार|वर्णन|शिकायत|समस्या|विवरण",
    }[field]
    cleaned = re.sub(
        rf"^.*?(?:{terms})\s*(?:is|to|as|:|-|आहे|असे|है|को)?\s*",
        "",
        text.strip(),
        flags=re.I,
    ).strip(" .,:;-।")
    return cleaned


def _apply_correction(
    conversation: Conversation, text: str, field: str | None = None
) -> str | None:
    """Replace one confirmed field and return its name when the value is valid."""
    target = field or _correction_field(text)
    if target == "contact" or (target is None and _contact(text)):
        if contact := _contact(text):
            conversation.complaint_collection_contact_number = contact
            return "contact"
    if target == "station":
        stations = find_station_names(text)
        station = stations[0] if stations else resolve_station_alias(_strip_correction_prefix(text, target))
        if station:
            conversation.complaint_collection_station = station
            return "station"
    if target == "name":
        name = _extract_name(text) or _strip_correction_prefix(text, target)
        if _is_valid_name(name):
            conversation.complaint_collection_full_name = name
            return "name"
    if target == "description":
        description = _strip_correction_prefix(text, target)
        if _is_meaningful_description(description):
            conversation.complaint_collection_description = description
            return "description"
    return None


def _change_question(conversation: Conversation) -> str:
    return _localized(
        _language(conversation),
        "Of course. What would you like to change: the name, contact number, station, or complaint details?",
        "ज़रूर। आप क्या बदलना चाहते हैं—नाम, संपर्क नंबर, स्टेशन या शिकायत की जानकारी?",
        "नक्की. तुम्हाला काय बदलायचं आहे—नाव, संपर्क क्रमांक, स्थानक की तक्रारीची माहिती?",
    )


def _new_value_question(conversation: Conversation, field: str) -> str:
    prompts = {
        "name": (
            "What is the correct full name?", "सही पूरा नाम क्या है?", "योग्य पूर्ण नाव काय आहे?",
        ),
        "contact": (
            "What is the correct 10-digit contact number?", "सही 10 अंकों का संपर्क नंबर क्या है?", "योग्य 10 अंकी संपर्क क्रमांक कोणता आहे?",
        ),
        "station": (
            "What is the correct Pune Metro station?", "सही पुणे मेट्रो स्टेशन कौन सा है?", "योग्य पुणे मेट्रो स्थानक कोणतं आहे?",
        ),
        "description": (
            "Please tell me the corrected complaint details.", "कृपया शिकायत की सही जानकारी बताइए।", "कृपया तक्रारीची दुरुस्त माहिती सांगा.",
        ),
    }
    english, hindi, marathi = prompts[field]
    return _localized(_language(conversation), english, hindi, marathi)


def clear_collection(conversation: Conversation) -> None:
    conversation.complaint_collection_state = None
    conversation.complaint_collection_full_name = None
    conversation.complaint_collection_contact_number = None
    conversation.complaint_collection_station = None
    conversation.complaint_collection_description = None
    conversation.pending_category = None


def _summary(conversation: Conversation) -> str:
    values = (
        conversation.complaint_collection_full_name,
        conversation.complaint_collection_contact_number,
        conversation.complaint_collection_station,
        conversation.complaint_collection_description,
    )
    language = _language(conversation)
    label = _category_label(conversation.pending_category or "complaint", language)
    spoken_number = _spoken_contact(values[1] or "")
    summaries = {
        "english": (
            f"Let me confirm the information. Name: {values[0]}; contact number: {spoken_number}; station: {values[2]}; {label}: {values[3]}. Should I proceed with this information, or would you like to change anything?",
        ),
        "hindi": (
            f"मैं जानकारी की पुष्टि कर देती हूँ। नाम: {values[0]}; संपर्क नंबर: {spoken_number}; स्टेशन: {values[2]}; {label}: {values[3]}। क्या मैं इसी जानकारी के साथ आगे बढ़ूँ, या आप कुछ बदलना चाहते हैं?",
        ),
        "marathi": (
            f"मी माहितीची खात्री करून घेते. नाव: {values[0]}; संपर्क क्रमांक: {spoken_number}; स्थानक: {values[2]}; {label}: {values[3]}. मी याच माहितीसह पुढे जाऊ का, की तुम्हाला काही बदलायचं आहे?",
        ),
    }
    return _pick_phrase(
        conversation,
        "summary",
        summaries.get(language, summaries["english"]),
        "|".join(str(item) for item in values),
    )


def compact_voice_confirmation(conversation: Conversation) -> str:
    """Confirm essential fields on a call without re-speaking long details."""
    name = conversation.complaint_collection_full_name
    number = _spoken_contact(conversation.complaint_collection_contact_number or "")
    station = conversation.complaint_collection_station
    language = _language(conversation)
    confirmations = {
        "english": (
            f"Please confirm: {name}, number {number}, station {station}. "
            "I have recorded the details. Say proceed, change, or add details."
        ),
        "hindi": (
            f"कृपया पुष्टि करें: {name}, नंबर {number}, स्टेशन {station}। "
            "विवरण दर्ज है। आगे बढ़ें, बदलाव, या जानकारी जोड़ें कहें।"
        ),
        "marathi": (
            f"कृपया खात्री करा: {name}, क्रमांक {number}, स्थानक {station}. "
            "तपशील नोंदवले आहेत. पुढे जा, बदल, किंवा माहिती जोडा असं सांगा."
        ),
    }
    return confirmations.get(language, confirmations["english"])


def advance_collection(conversation: Conversation, text: str, db: Session) -> tuple[str, bool]:
    """Consume one follow-up and return (reply, completed)."""
    state = conversation.complaint_collection_state
    category = conversation.pending_category or "complaint"
    value = text.strip()
    correction_states = {
        "confirming", "awaiting_correction", "correcting_name", "correcting_contact",
        "correcting_station", "correcting_description",
    }
    if state not in correction_states:
        _extract_all_fields(conversation, value)
    if state == "collecting_name":
        if not conversation.complaint_collection_full_name:
            meta_answer = any(phrase in value.casefold() for phrase in ("already told", "आधीच", "पहले ही"))
            if _is_valid_name(value) and not meta_answer:
                conversation.complaint_collection_full_name = value
        if not conversation.complaint_collection_full_name:
            return _next_collection_prompt(
                conversation, repair=True, seed=value
            ), False
        return _acknowledge_and_continue(
            conversation, "name", conversation.complaint_collection_full_name
        ), False
    if state == "collecting_contact":
        incoming_digits = _contact_digits(value)
        existing_digits = _contact_digits(
            conversation.complaint_collection_contact_number or ""
        )
        combined_digits = (
            incoming_digits if len(incoming_digits) == 10
            else existing_digits + incoming_digits
        )
        if len(combined_digits) <= 10:
            conversation.complaint_collection_contact_number = combined_digits or None
        if _contact(conversation.complaint_collection_contact_number or "") is None:
            language = _language(conversation)
            repair_choices = {
                "english": (
                    "I only caught part of that number. Could you say all 10 digits again, a little slowly?",
                    "That doesn't seem to be 10 digits yet. Please repeat the complete mobile number.",
                    "Let me get the number right. Could you say the full 10 digits once more?",
                ),
                "hindi": (
                    "मुझे नंबर का कुछ हिस्सा ही सुनाई दिया। कृपया पूरे 10 अंक थोड़ा धीरे फिर से बताइए।",
                    "नंबर अभी 10 अंकों का नहीं लग रहा। पूरा मोबाइल नंबर दोबारा बताएँ।",
                    "मैं नंबर सही दर्ज करना चाहती हूँ। कृपया सभी 10 अंक एक बार फिर बोलिए।",
                ),
                "marathi": (
                    "मला नंबरचा काही भागच ऐकू आला. कृपया पूर्ण 10 अंक थोडे हळू पुन्हा सांगा.",
                    "हा नंबर अजून 10 अंकी दिसत नाही. पूर्ण मोबाईल नंबर पुन्हा सांगाल का?",
                    "नंबर अचूक नोंदवूया. कृपया सगळे 10 अंक पुन्हा सांगा.",
                ),
            }
            return _pick_phrase(
                conversation,
                "contact_repair",
                repair_choices.get(language, repair_choices["english"]),
                value,
            ), False
        return _acknowledge_and_continue(
            conversation, "contact", conversation.complaint_collection_contact_number
        ), False
    if state == "collecting_station":
        if conversation.complaint_collection_station is None:
            station = resolve_station_alias(value)
            if station:
                conversation.complaint_collection_station = station
        if conversation.complaint_collection_station is None:
            language = _language(conversation)
            repair_choices = {
                "english": (
                    "I couldn't match that to a Pune Metro station. Which station was it?",
                    "The station name wasn't clear. Could you say it once more?",
                    "Let me check the location again—what's the Pune Metro station name?",
                ),
                "hindi": (
                    "मैं उस नाम को पुणे मेट्रो स्टेशन से मिला नहीं पाई। यह कौन सा स्टेशन था?",
                    "स्टेशन का नाम साफ़ नहीं आया। कृपया एक बार फिर बताएँ।",
                    "स्थान फिर से पक्का कर लेते हैं—पुणे मेट्रो स्टेशन का नाम क्या है?",
                ),
                "marathi": (
                    "ते नाव पुणे मेट्रो स्थानकाशी जुळलं नाही. कोणतं स्थानक होतं?",
                    "स्थानकाचं नाव स्पष्ट आलं नाही. एकदा पुन्हा सांगाल का?",
                    "ठिकाण पुन्हा खात्री करून घेऊया—पुणे मेट्रो स्थानकाचं नाव काय आहे?",
                ),
            }
            return _pick_phrase(
                conversation,
                "station_repair",
                repair_choices.get(language, repair_choices["english"]),
                value,
            ), False
        return _acknowledge_and_continue(
            conversation, "station", conversation.complaint_collection_station
        ), False
    if state == "collecting_description":
        if _extract_description(value):
            return _next_collection_prompt(conversation), False
        if _is_meaningful_description(value):
            conversation.complaint_collection_description = _normalize_description(value)
            return _next_collection_prompt(conversation), False
        language = _language(conversation)
        repair_choices = {
            "english": (
                "Could you tell me a little more about what happened?",
                "I need one more detail: what should the team look into?",
                f"What would you like us to record for this {category}?",
            ),
            "hindi": (
                "क्या हुआ था, थोड़ा और विस्तार से बताएँगे?",
                "बस एक जानकारी और चाहिए—टीम को किस बात की जाँच करनी है?",
                "आप इसमें क्या दर्ज करवाना चाहते हैं?",
            ),
            "marathi": (
                "नेमकं काय झालं, थोडं अधिक सांगाल का?",
                "आणखी एक माहिती हवी आहे—टीमने कशाकडे लक्ष द्यावं?",
                "यामध्ये तुम्हाला नेमकं काय नोंदवायचं आहे?",
            ),
        }
        return _pick_phrase(
            conversation,
            "description_repair",
            repair_choices.get(language, repair_choices["english"]),
            value,
        ), False
    if state == "awaiting_correction":
        if _cancel_requested(value):
            response_language = _language(conversation)
            cancelled = _localized(
                response_language,
                "No problem, I won't register it. We can make a fresh start whenever you're ready.",
                "कोई बात नहीं, मैं इसे दर्ज नहीं करूँगी। जब आप चाहें, हम फिर से शुरू कर सकते हैं।",
                "काही हरकत नाही, मी ही नोंद करणार नाही. तुम्ही तयार असाल तेव्हा आपण पुन्हा सुरू करू.",
            )
            clear_collection(conversation)
            return cancelled, True
        field = _correction_field(value)
        if corrected_field := _apply_correction(conversation, value, field):
            conversation.complaint_collection_state = "confirming"
            return _summary(conversation), False
        if field:
            conversation.complaint_collection_state = f"correcting_{field}"
            return _new_value_question(conversation, field), False
        return _change_question(conversation), False

    if state and state.startswith("correcting_"):
        field = state.removeprefix("correcting_")
        if _cancel_requested(value):
            clear_collection(conversation)
            return _localized(
                _language(conversation),
                "Okay, I won't register this complaint.",
                "ठीक है, मैं यह शिकायत दर्ज नहीं करूँगी।",
                "ठीक आहे, मी ही तक्रार नोंदवणार नाही.",
            ), True
        if _apply_correction(conversation, value, field):
            conversation.complaint_collection_state = "confirming"
            return _summary(conversation), False
        return _localized(
            _language(conversation),
            f"I couldn't update that yet. {_new_value_question(conversation, field)}",
            f"मैं उसे अभी बदल नहीं पाई। {_new_value_question(conversation, field)}",
            f"ती माहिती अजून बदलता आली नाही. {_new_value_question(conversation, field)}",
        ), False

    if state == "confirming":
        if _another_submission_requested(value):
            return _finish_current_before_next_reply(conversation), False
        if _wants_change(value):
            if corrected_field := _apply_correction(conversation, value):
                conversation.complaint_collection_state = "confirming"
                return _summary(conversation), False
            conversation.complaint_collection_state = "awaiting_correction"
            return _change_question(conversation), False
        normalized = value.casefold()
        if any(
            phrase in normalized
            for phrase in (
                "not complete", "did not hear", "let me finish",
                "पूर्ण नाही", "ऐकली नाही", "पूर्ण सांग", "बोलू द्या",
                "पूरी नहीं", "सुनी नहीं", "पूरा बताने",
            )
        ):
            conversation.complaint_collection_state = "collecting_description"
            return _localized(
                _language(conversation),
                "Please continue. I will wait for the complete details.",
                "कृपया पूरी जानकारी बताइए। मैं आपकी बात पूरी होने तक प्रतीक्षा करूँगी।",
                "कृपया संपूर्ण माहिती सांगा. तुमचं बोलणं पूर्ण होईपर्यंत मी थांबते.",
            ), False
        if _cancel_requested(value):
            response_language = _language(conversation)
            cancelled = _localized(
                response_language,
                "No problem, I won't register it. We can make a fresh start whenever you're ready.",
                "कोई बात नहीं, मैं इसे दर्ज नहीं करूँगी। जब आप चाहें, हम फिर से शुरू कर सकते हैं।",
                "काही हरकत नाही, मी ही नोंद करणार नाही. तुम्ही तयार असाल तेव्हा आपण पुन्हा सुरू करू.",
            )
            clear_collection(conversation)
            return cancelled, True
        if _append_confirmed_description(conversation, value):
            return _localized(
                _language(conversation),
                f"I've added that detail. {_summary(conversation)}",
                f"मैंने वह जानकारी जोड़ दी है। {_summary(conversation)}",
                f"मी ती माहिती जोडली आहे. {_summary(conversation)}",
            ), False
        if not _yes(value):
            return _localized(
                _language(conversation),
                "Please tell me whether I should proceed, or what you would like to change. Say cancel only if you do not want to register it.",
                "कृपया बताइए कि मैं आगे बढ़ूँ या आप क्या बदलना चाहते हैं। दर्ज नहीं करना हो, तभी रद्द कहें।",
                "मी पुढे जाऊ का, की तुम्हाला काय बदलायचं आहे ते सांगा. नोंद करायची नसेल तरच रद्द म्हणा.",
            ), False
        message = (
            f"Name: {conversation.complaint_collection_full_name}\n"
            f"Contact: {conversation.complaint_collection_contact_number}\n"
            f"Station: {conversation.complaint_collection_station}\n"
            f"Description: {conversation.complaint_collection_description}"
        )
        log = CategoryLog(
            user_id=conversation.user_id,
            conversation_id=conversation.id,
            categories=[category],
            message=message,
        )
        db.add(log)
        db.flush()
        db.add(TicketDetails(
            category_log_id=log.id,
            metro_station=conversation.complaint_collection_station,
            passenger_name=conversation.complaint_collection_full_name,
        ))
        token = None
        if category in {"complaint", "suggestion"}:
            token = create_complaint_tracking(
                category_log=log, user_id=conversation.user_id,
                conversation_id=conversation.id, db=db, category=category,
            ).token
        response_language = _language(conversation)
        response_label = _category_label(category, response_language)
        clear_collection(conversation)
        if token:
            confirmations = {
                "english": (
                    f"That's done—your {response_label} is registered. Keep this tracking ID handy: {token}.",
                    f"I've registered your {response_label}. Your tracking ID is {token}.",
                    f"Your {response_label} is now on record. You can follow it using {token}.",
                ),
                "hindi": (
                    f"हो गया—आपकी {response_label} दर्ज कर दी गई है। ट्रैकिंग आईडी {token} संभालकर रखें।",
                    f"मैंने आपकी {response_label} दर्ज कर दी है। आपकी ट्रैकिंग आईडी {token} है।",
                    f"आपकी {response_label} अब दर्ज हो गई है। इसकी स्थिति {token} से देख सकते हैं।",
                ),
                "marathi": (
                    f"झालं—तुमची {response_label} नोंदवली आहे. ट्रॅकिंग आयडी {token} जपून ठेवा.",
                    f"मी तुमची {response_label} नोंदवली आहे. तुमचा ट्रॅकिंग आयडी {token} आहे.",
                    f"तुमची {response_label} आता नोंद झाली आहे. {token} वापरून तिची स्थिती पाहता येईल.",
                ),
            }
            return _pick_phrase(
                conversation,
                "registered",
                confirmations.get(response_language, confirmations["english"]),
                token,
            ), True
        appreciation_replies = {
            "english": (
                "I've shared your appreciation with the team. It's lovely of you to take the time to mention it.",
                "That's been recorded and will reach the team. Thanks for sharing the positive feedback.",
            ),
            "hindi": (
                "मैंने आपकी प्रशंसा टीम के लिए दर्ज कर दी है। समय निकालकर बताने के लिए बहुत धन्यवाद।",
                "आपकी अच्छी प्रतिक्रिया टीम तक पहुँचेगी। इसे साझा करने के लिए धन्यवाद।",
            ),
            "marathi": (
                "तुमचं कौतुक टीमसाठी नोंदवलं आहे. आवर्जून सांगितल्याबद्दल मनापासून धन्यवाद.",
                "तुमचा चांगला अभिप्राय टीमपर्यंत पोहोचेल. तो सांगितल्याबद्दल धन्यवाद.",
            ),
        }
        return _pick_phrase(
            conversation,
            "appreciation_recorded",
            appreciation_replies.get(response_language, appreciation_replies["english"]),
        ), True
    clear_collection(conversation)
    return "Let us start again. Please tell me whether this is a complaint, suggestion, or appreciation.", False
