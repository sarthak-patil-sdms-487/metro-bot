"""Voice-only fast routing that avoids unnecessary remote model turns."""

import re

from app.services.collection_flow import detect_collection_category
from app.services.llm_client import (
    find_station_names,
    find_unsupported_station_names,
    resolve_reply_language,
)


class VoiceAgent:
    channel = "call"

    @staticmethod
    def collection_category(text: str) -> str | None:
        # Complaint collection must be deterministic on a real-time path. The
        # shared brain still classifies general questions when no fast route fits.
        return detect_collection_category(text)

    @staticmethod
    def is_explicit_enquiry(text: str) -> bool:
        """Detect a clear topic switch while a collection form is active."""
        normalized = text.casefold()
        if detect_collection_category(text) is not None:
            return False
        question = bool(re.search(
            r"\b(?:what|when|where|how|fare|route|timing|hours|travel|ticket)\b|\?|"
            r"(?:काय|कधी|कुठे|किती|मार्ग|भाडे|तिकीट|वेळ|प्रवास)|"
            r"(?:क्या|कब|कहाँ|कितना|किराया|समय|यात्रा)", normalized
        ))
        metro_topic = bool(re.search(
            r"metro|मेट्रो|pcmc|swargate|vanaz|ramwadi|"
            r"पीसीएमसी|स्वारगेट|वनाज|रामवाडी|रामवाड़ी", normalized
        ))
        stations = find_station_names(text)
        planned_stations = find_unsupported_station_names(text)
        journey = bool(re.search(
            r"\b(?:go|travel|reach|get)\s+(?:from|to)\b|\bfrom\b.{1,60}\bto\b|"
            r"(?:पासून|वरून|येथून).{1,60}(?:पर्यंत|कडे|जायच)|"
            r"(?:से).{1,60}(?:तक|जाना|जाऊँ)",
            normalized,
        ))
        faq = VoiceAgent.common_information_reply(text) is not None
        return bool(
            faq
            or (question and (metro_topic or stations or planned_stations))
            or journey
        )

    @staticmethod
    def is_resume_collection(text: str) -> bool:
        normalized = " ".join(text.casefold().strip("!?.,;:।").split())
        return bool(re.search(
            r"\b(?:resume|continue|go back to|back to).{0,20}(?:complaint|suggestion|appreciation|feedback)\b|"
            r"(?:तक्रार|सूचना|कौतुक).{0,20}(?:पुढे|सुरू|परत)|"
            r"(?:पुढे|सुरू|परत).{0,20}(?:तक्रार|सूचना|कौतुक)|"
            r"(?:शिकायत|सुझाव|प्रशंसा).{0,20}(?:जारी|वापस|फिर)|"
            r"(?:जारी|वापस|फिर).{0,20}(?:शिकायत|सुझाव|प्रशंसा)",
            normalized,
        ))

    @staticmethod
    def paused_collection_reminder(language: str, category: str = "complaint") -> str:
        labels = {
            "english": {"complaint": "complaint", "suggestion": "suggestion", "appreciation": "appreciation"},
            "hindi": {"complaint": "शिकायत", "suggestion": "सुझाव", "appreciation": "प्रशंसा"},
            "marathi": {"complaint": "तक्रार", "suggestion": "सूचना", "appreciation": "कौतुक"},
        }
        label = labels.get(language, labels["english"]).get(category, category)
        return {
            "english": f"Your current {label} is still saved and paused. Say 'resume {label}' to continue it, or 'cancel {label}' to discard it.",
            "hindi": f"आपकी मौजूदा {label} सुरक्षित है और रुकी हुई है। जारी रखने के लिए '{label} जारी रखें' कहें, या हटाने के लिए '{label} रद्द करें' कहें।",
            "marathi": f"तुमची सध्याची {label} सुरक्षित ठेवून थांबवली आहे. पुढे सुरू करण्यासाठी '{label} पुढे सुरू करा' म्हणा, किंवा काढण्यासाठी '{label} रद्द करा' म्हणा.",
        }.get(language, f"Your current {label} is saved and paused.")

    @staticmethod
    def platform_guidance_reply(text: str) -> tuple[str, str] | None:
        normalized = text.casefold()
        if not re.search(r"\bplatform\b|प्लॅटफॉर्म|प्लेटफॉर्म", normalized):
            return None
        language, _ = resolve_reply_language(text)
        replies = {
            "english": "Platform assignments can change. At the station, follow the line and destination signs or confirm with station staff; I won't guess a platform number.",
            "hindi": "प्लेटफॉर्म बदल सकता है। स्टेशन पर लाइन और गंतव्य के संकेत देखें या स्टाफ से पुष्टि करें; मैं प्लेटफॉर्म नंबर का अनुमान नहीं लगाऊँगी।",
            "marathi": "प्लॅटफॉर्म बदलू शकतो. स्थानकावर लाईन आणि गंतव्याचे फलक पाहा किंवा कर्मचाऱ्यांकडून खात्री करा; मी प्लॅटफॉर्म क्रमांकाचा अंदाज सांगणार नाही.",
        }
        return replies.get(language, replies["english"]), language

    @staticmethod
    def ask_question_reply(language: str) -> str:
        return {
            "english": "Yes, please ask your Pune Metro question.",
            "hindi": "जी, कृपया पुणे मेट्रो से जुड़ा अपना सवाल पूछिए।",
            "marathi": "हो, कृपया पुणे मेट्रोबद्दलचा तुमचा प्रश्न विचारा.",
        }.get(language, "Yes, please ask your Pune Metro question.")

    @staticmethod
    def common_information_reply(text: str) -> tuple[str, str] | None:
        normalized = text.casefold()
        language, _ = resolve_reply_language(text)
        # Feedback and incident reports always take priority over FAQ shortcuts.
        # A word such as "closed" may describe a broken lift, not Metro hours.
        if detect_collection_category(text) is not None:
            return None
        timing = bool(re.search(
            r"\b(?:operat(?:e|ing|ion)|hours?|timings?|schedule)\b|"
            r"\b(?:when|what time).{0,30}(?:start|open|close)|"
            r"(?:वेळ|वेळा|टाइमिंग|किती वाजता|कधी सुरू|कधी बंद)|"
            r"(?:समय|टाइमिंग|कितने बजे|कब खुलती|कब बंद)", normalized
        )) and bool(re.search(r"metro|मेट्रो", normalized))
        if timing:
            replies = {
                "english": "Pune Metro operates daily from 6 AM to 11 PM on both the Purple and Aqua Lines.",
                "hindi": "पुणे मेट्रो पर्पल और एक्वा दोनों लाइनों पर रोज़ सुबह 6 बजे से रात 11 बजे तक चलती है।",
                "marathi": "पुणे मेट्रो पर्पल आणि ॲक्वा या दोन्ही मार्गांवर दररोज सकाळी 6 ते रात्री 11 वाजेपर्यंत चालते.",
            }
            return replies.get(language, replies["english"]), language
        card_topic = bool(re.search(
            r"(?:metro|one pune|ncmc).{0,20}card|card.{0,20}(?:metro|मेट्रो)|"
            r"मेट्रो.{0,15}कार्ड", normalized
        ))
        card_request = bool(re.search(
            r"\b(?:where|how|buy|get|obtain|issue|fee|cost|price)\b|"
            r"(?:कुठे|कसा|कसे|मिळ|शुल्क|किंमत)|"
            r"(?:कहाँ|कैसे|मिले|शुल्क|कीमत)", normalized
        ))
        card = card_topic and card_request
        if card:
            replies = {
                "english": "You can get a One Pune Card at Pune Metro station ticket offices. The issuance fee is 100 rupees.",
                "hindi": "वन पुणे कार्ड पुणे मेट्रो स्टेशन के टिकट कार्यालय से मिल सकता है। इसे जारी करने का शुल्क 100 रुपये है।",
                "marathi": "वन पुणे कार्ड पुणे मेट्रो स्थानकाच्या तिकीट कार्यालयात मिळते. कार्ड जारी करण्याचे शुल्क 100 रुपये आहे.",
            }
            return replies.get(language, replies["english"]), language
        return None


voice_agent = VoiceAgent()
