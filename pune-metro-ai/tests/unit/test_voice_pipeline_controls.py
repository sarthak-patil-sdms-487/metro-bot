from array import array
from types import SimpleNamespace

from app.services.voice_pipeline import (
    END_MARKER,
    GREETING_TEXT,
    TURN_AGGREGATION_DELAY_SECONDS,
    VAD_STOP_SECS,
    _build_vad_analyzer,
    _call_closing_reply,
    _continue_call_after_collection,
    _desired_pcm16_gain,
    _fast_voice_reply,
    _is_explicit_thank_you,
    _is_no_more_enquiry,
    _is_incomplete_voice_fragment,
    _is_actionable_barge_in,
    _is_meaningful_barge_in,
    _is_nonlexical_voice_noise,
    _language_detection_probability,
    _looks_like_bot_echo,
    _mentions_planned_line,
    _parse_tts_text,
    _pcm16_gain,
    _offer_more_help,
    _offered_more_help,
    _route_clarification,
    _sanitize_voice_reply,
    _sanitize_reply_with_end_marker,
    _transcription_language_code,
    _tag_tts_text,
    _tts_language,
)
from app.services.voice_agent import voice_agent
from app.services.qa_cache import is_cacheable_question


def test_voice_endpointing_is_fast_but_tolerates_natural_pauses() -> None:
    assert TURN_AGGREGATION_DELAY_SECONDS < 1.0
    assert 0.15 <= VAD_STOP_SECS <= 0.3


def test_bot_echo_is_suppressed_without_blocking_real_barge_in() -> None:
    greeting = (
        "नमस्कार! सेवा गुणवत्ता आणि नोंदीसाठी ही कॉल रेकॉर्ड केली जाऊ शकते. "
        "मी पुणे मेट्रोची सहाय्यक बोलते."
    )

    assert _looks_like_bot_echo("नमस्कार, सेवा गुणवत्ता", greeting) is True
    assert _looks_like_bot_echo("सेवा गुणवत्ता आणि नोंदीसाठी", greeting) is True
    assert _looks_like_bot_echo("Hello, I need help with a complaint", greeting) is False
    assert _looks_like_bot_echo("Stop", greeting) is False


def test_voice_noise_does_not_become_a_dialogue_turn() -> None:
    assert _is_nonlexical_voice_noise("Hmm") is True
    assert _is_nonlexical_voice_noise("umm...") is True
    assert _is_nonlexical_voice_noise("I have a complaint") is False
    assert _is_nonlexical_voice_noise("Thank you") is False


def test_barge_in_requires_deliberate_speech_but_accepts_free_form_requests() -> None:
    assert _is_meaningful_barge_in("Hmm", active_collection=False) is False
    assert _is_meaningful_barge_in("Stop", active_collection=False) is True
    assert _is_meaningful_barge_in(
        "Please help me", active_collection=False
    ) is True
    assert _is_meaningful_barge_in(
        "I want to report a broken lift", active_collection=False
    ) is True


def test_quiet_pcm_is_safely_boosted_without_amplifying_silence() -> None:
    quiet = array("h", [120, -180, 240, -300] * 80).tobytes()
    silence = array("h", [0] * 320).tobytes()
    loud = array("h", [9000, -12000, 14000, -10000] * 80).tobytes()

    gain = _desired_pcm16_gain(quiet)
    boosted = array("h")
    boosted.frombytes(_pcm16_gain(quiet, gain))

    assert gain > 1.0
    assert max(abs(value) for value in boosted) > 300
    assert _desired_pcm16_gain(silence) == 1.0
    assert _desired_pcm16_gain(loud) == 1.0


def test_lift_closed_incident_stays_in_complaint_flow_not_timetable() -> None:
    message = (
        "पीसीएमसी मेट्रो स्टेशन वरती मला जरा त्रास झाला. "
        "लिफ्ट पण बंद होते आणि डब्यात मारामारी चाललेली."
    )

    assert voice_agent.collection_category(message) == "complaint"
    assert voice_agent.common_information_reply(message) is None


def test_incident_language_never_uses_timetable_or_card_faq_shortcuts() -> None:
    incidents = (
        "The station escalator was closed and somebody was fighting in the coach",
        "मेट्रो स्टेशनची लिफ्ट बंद होती आणि प्रवाशांना अडचण झाली",
        "मेट्रो कार्ड काम नहीं कर रहा है, इसकी शिकायत करनी है",
    )

    for message in incidents:
        assert voice_agent.common_information_reply(message) is None


def test_information_requests_still_use_safe_fast_faq_routes() -> None:
    assert voice_agent.common_information_reply("When does Pune Metro close?") is not None
    assert voice_agent.common_information_reply("Pune Metro timing") is not None
    assert voice_agent.common_information_reply("Where can I get a One Pune Card?") is not None
    assert voice_agent.common_information_reply("My Pune Metro card stopped working") is None


def test_cache_is_limited_to_information_requests_not_feedback() -> None:
    assert is_cacheable_question("What is the fare from PCMC to Swargate?") is True
    assert is_cacheable_question("Pune Metro timing") is True
    assert is_cacheable_question("The lift is broken and people were fighting") is False
    assert is_cacheable_question("मेट्रोबद्दल मला एक सूचना द्यायची आहे") is False
    assert is_cacheable_question("मैं कर्मचारियों की प्रशंसा करना चाहता हूँ") is False


def test_live_call_vad_analyzer_can_be_constructed() -> None:
    analyzer = _build_vad_analyzer()

    assert analyzer._params.stop_secs == VAD_STOP_SECS


def test_explicit_thank_you_ends_call_after_english_farewell() -> None:
    reply = _call_closing_reply("Thank you!", "english")

    assert reply is not None
    assert "Thanks for calling Pune Metro" in reply
    assert reply.endswith(END_MARKER)


def test_marathi_thank_you_ends_call_in_marathi() -> None:
    reply = _call_closing_reply("धन्यवाद", "marathi")

    assert reply is not None
    assert "पुणे मेट्रोला कॉल केल्याबद्दल धन्यवाद" in reply
    assert reply.endswith(END_MARKER)


def test_non_thank_you_acknowledgment_does_not_end_call() -> None:
    assert _call_closing_reply("okay", "english") is None


def test_bare_no_only_ends_after_bot_offers_more_help() -> None:
    assert _call_closing_reply("no", "english") is None
    assert _call_closing_reply(
        "no", "english", allow_bare_negative=True
    ).endswith(END_MARKER)


def test_no_more_enquiry_is_detected_in_all_supported_languages() -> None:
    assert _is_no_more_enquiry("That's all") is True
    assert _is_no_more_enquiry("बस इतना ही") is True
    assert _is_no_more_enquiry("आणखी काही नाही") is True
    assert _is_no_more_enquiry("सध्या काही नाही") is True
    assert _is_no_more_enquiry("सध्या तर काही नाही.") is True


def test_completed_answers_offer_more_help_in_call_language() -> None:
    assert _offered_more_help(_offer_more_help("The fare is ₹30.", "english"))
    assert _offered_more_help(_offer_more_help("किराया तीस रुपये है।", "hindi"))
    assert _offered_more_help(_offer_more_help("तिकीट तीस रुपये आहे.", "marathi"))


def test_completed_collection_explicitly_allows_more_tasks_in_same_call() -> None:
    assert "another complaint" in _continue_call_after_collection(
        "Your complaint is registered.", "english"
    )
    assert "दूसरी शिकायत" in _continue_call_after_collection(
        "शिकायत दर्ज हो गई।", "hindi"
    )
    assert "दुसरी तक्रार" in _continue_call_after_collection(
        "तक्रार नोंदवली आहे.", "marathi"
    )


def test_greeting_announces_all_supported_languages() -> None:
    assert "मराठी" in GREETING_TEXT
    assert "हिंदी" in GREETING_TEXT
    assert "इंग्रजी" in GREETING_TEXT


def test_thank_you_detection_preserves_closing_during_busy_turn() -> None:
    assert _is_explicit_thank_you("Thank you") is True
    assert _is_explicit_thank_you("एवढीच माहिती पाहिजे होती, थँक्यू.") is True
    assert _is_explicit_thank_you("नहीं, मुझे इतना ही चाहिए था, थैंक यू।") is True
    assert _is_explicit_thank_you("okay") is False


def test_sarvam_language_probability_is_metadata_not_stt_confidence() -> None:
    frame = SimpleNamespace(
        result={"data": {"language_probability": 0.977}},
    )

    assert _language_detection_probability(frame) == 0.977


def test_missing_language_probability_is_allowed() -> None:
    assert _language_detection_probability(SimpleNamespace(result=None)) is None


def test_sarvam_transcription_language_is_extracted() -> None:
    frame = SimpleNamespace(result={"data": {"language_code": "kn-IN"}})
    assert _transcription_language_code(frame) == "kn-IN"


def test_voice_greeting_uses_deterministic_fast_path() -> None:
    reply = _fast_voice_reply("Hello")

    assert reply is not None
    assert reply[1] == "english"
    assert "help" in reply[0].casefold()


def test_short_acknowledgment_never_starts_name_collection() -> None:
    reply = _fast_voice_reply("Okay")

    assert reply is not None
    assert "anything else" in reply[0].casefold()
    assert "name" not in reply[0].casefold()


def test_presence_and_destination_meaning_use_fast_local_replies() -> None:
    presence = _fast_voice_reply("हॅलो, आहेत का तुम्ही?")
    meaning = _fast_voice_reply("गंतव्य म्हणजे काय?")

    assert presence is not None and "ऐकतेय" in presence[0]
    assert meaning is not None and "ज्या स्थानकावर" in meaning[0]


def test_normal_question_uses_shared_brain() -> None:
    reply = _fast_voice_reply("What is the fare from PCMC to Swargate?")
    assert reply is not None
    assert "₹30" in reply[0]


def test_ambiguous_pimpri_route_asks_for_operational_station() -> None:
    clarification = _route_clarification(
        "शिवाजीनगर ते पिंपरीपर्यंत जायचं आहे तर मी कसं जाऊ?"
    )

    assert clarification is not None
    assert "पीसीएमसी" in clarification[0]
    assert "संत तुकाराम नगर" in clarification[0]
    assert clarification[1] == "marathi"


def test_single_station_status_question_is_not_treated_as_route() -> None:
    assert _route_clarification("Is Shivaji Nagar station open?") is None


def test_destination_only_route_asks_for_current_or_nearest_station() -> None:
    clarification = _route_clarification("मला मंडईमध्ये जायचं आहे")

    assert clarification is not None
    assert "Mahatma Phule Mandai" in clarification[0]
    assert "सध्याचं किंवा जवळचं" in clarification[0]


def test_dangling_route_connector_is_carried_to_next_transcript() -> None:
    assert _is_incomplete_voice_fragment("हा मला हिंजवडी पासून ते") is True


def test_line_opening_question_is_not_treated_as_missing_route_fields() -> None:
    question = "भानेर ते हिंजवडी मेट्रो लाईन कधी चालू होणार आहे?"

    assert _route_clarification(question) is None
    reply = _fast_voice_reply(question)
    assert reply is not None
    assert reply[1] == "marathi"
    assert "बांधकामाधीन" in reply[0]
    assert "सुरुवातीचं स्टेशन" not in reply[0]


def test_planned_line_followups_remember_test_one_context() -> None:
    opening = _fast_voice_reply(
        "When will the Hinjewadi and Shivajinagar metro line be open?"
    )
    assert opening is not None
    assert "under construction" in opening[0].casefold()
    assert _mentions_planned_line(opening[0]) is True

    stop = _fast_voice_reply(
        "Okay, will it stop at Bane?", planned_line_context=True
    )
    assert stop is not None
    assert "Baner is a planned station" in stop[0]
    assert "starting station" not in stop[0]

    for question in (
        "Can I travel on this line today?",
        "Yes, can I travel on these lines today?",
    ):
        travel = _fast_voice_reply(question, planned_line_context=True)
        assert travel is not None
        assert "not open for passenger travel today" in travel[0]
        assert "starting station" not in travel[0]
        assert _offer_more_help(travel[0], "english") == travel[0]


def test_planned_line_followups_work_in_hindi_and_marathi() -> None:
    hindi = _fast_voice_reply(
        "क्या मैं आज इस लाइन पर यात्रा कर सकती हूँ?", planned_line_context=True
    )
    marathi = _fast_voice_reply(
        "मी आज या लाईनवर प्रवास करू शकतो का?", planned_line_context=True
    )

    assert hindi is not None and "आज यात्री सेवा" in hindi[0]
    assert marathi is not None and "आज प्रवासी सेवेसाठी" in marathi[0]


def test_clarification_prompt_does_not_get_anything_else_appended() -> None:
    prompt = "कृपया सुरुवातीचं स्टेशन आणि गंतव्य स्टेशन सांगा."

    assert _offer_more_help(prompt, "marathi") == prompt


def test_complete_marathi_route_is_answered_without_llm() -> None:
    reply = _fast_voice_reply(
        "पुणे स्टेशनपासून ते स्वारगेटपर्यंत जायचं आहे, तर कसं जाऊ?"
    )
    assert reply is not None
    assert "डिस्ट्रिक्ट कोर्ट" in reply[0]
    assert reply[1] == "marathi"


def test_incomplete_voice_fragment_is_not_sent_to_llm() -> None:
    assert _is_incomplete_voice_fragment("तर मला") is True
    assert _is_incomplete_voice_fragment("माझं नाव सार्थक पाटील आहे मला") is True
    assert _is_incomplete_voice_fragment("शिवाजीनगर ते स्वारगेट जायचं आहे") is False


def test_marathi_greeting_uses_marathi_tts_configuration() -> None:
    assert _tts_language("मी तुम्हाला कशी मदत करू?") == "marathi"


def test_explicit_language_tag_avoids_hindi_marathi_script_guessing() -> None:
    tagged = _tag_tts_text("कृपया सुरुवातीचं स्टेशन सांगा.", "marathi")
    assert _parse_tts_text(tagged) == (
        "कृपया सुरुवातीचं स्टेशन सांगा.",
        "marathi",
    )


def test_spoken_reply_language_uses_reply_script_not_station_only_stt_language() -> None:
    from app.services.voice_pipeline import _spoken_reply_language

    hindi_reply = "वहाँ पर्पल लाइन से उतरकर एक्वा लाइन पर चढ़ें।"
    marathi_reply = "तिथे पर्पल लाईनवरून उतरून ॲक्वा लाईनवर जा."

    assert _spoken_reply_language(hindi_reply, "english") == "hindi"
    assert _spoken_reply_language(marathi_reply, "english") == "marathi"


def test_collection_enquiry_switch_is_generic_and_does_not_match_more_details() -> None:
    assert voice_agent.is_explicit_enquiry(
        "मला PCMC पासून स्वारगेटला कसं जायचं आहे?"
    ) is True
    assert voice_agent.is_explicit_enquiry(
        "What is the fare from Vanaz to Ramwadi?"
    ) is True
    assert voice_agent.is_explicit_enquiry(
        "I want to travel from Green Park to Central Square"
    ) is True
    assert voice_agent.is_explicit_enquiry(
        "मेट्रो स्टेशनवरती साफसफाई पण नाही आहे"
    ) is False


def test_collection_resume_and_platform_guidance_are_deterministic() -> None:
    assert voice_agent.is_resume_collection("तक्रार पुढे सुरू करा") is True
    assert voice_agent.is_resume_collection("Please resume my complaint") is True
    assert voice_agent.is_resume_collection("शिकायत जारी रखें") is True

    guidance = voice_agent.platform_guidance_reply(
        "District Court पर कौन से प्लेटफॉर्म पर लाइन बदलनी है?"
    )
    assert guidance is not None
    assert "अनुमान" in guidance[0]
    assert guidance[1] == "hindi"


def test_background_speech_filter_keeps_only_actionable_barge_ins() -> None:
    assert _is_actionable_barge_in("Ramwadi", active_collection=False) is True
    assert _is_actionable_barge_in(
        "What is the fare from Vanaz to Ramwadi?", active_collection=True
    ) is True
    assert _is_actionable_barge_in(
        "साफसफाई पण नाही हेही जोडा", active_collection=True
    ) is True
    assert _is_actionable_barge_in(
        "फोल्डरमध्ये बाकीचे काम उद्या करूया", active_collection=True
    ) is False


def test_voice_reply_removes_markdown_and_line_breaks() -> None:
    assert _sanitize_voice_reply("- First option\n- Second option") == (
        "First option Second option"
    )


def test_voice_reply_sanitization_preserves_internal_hangup_marker() -> None:
    reply = _sanitize_reply_with_end_marker(
        f"You're welcome. Goodbye. {END_MARKER}"
    )

    assert reply == f"You're welcome. Goodbye. {END_MARKER}"
    assert "[ENDCALL]" not in reply
