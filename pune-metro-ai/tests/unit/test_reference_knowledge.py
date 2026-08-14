import os
from pathlib import Path

os.environ.setdefault("WHATSAPP_VERIFY_TOKEN", "test")
os.environ.setdefault("WHATSAPP_ACCESS_TOKEN", "test")
os.environ.setdefault("WHATSAPP_PHONE_NUMBER_ID", "test")
os.environ.setdefault("PRIMARY_LLM_API_KEY", "test")
os.environ.setdefault("FALLBACK_LLM_API_KEY", "test")

from app.services.llm_client import REFERENCE_FILES, SYSTEM_MESSAGE, load_reference_data


DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def test_new_reference_topics_are_loadable() -> None:
    topics = {"card", "card_non_kyc", "vidyarthi_pass", "contact", "timetable"}
    assert topics <= set(REFERENCE_FILES)

    content = load_reference_data(sorted(topics))

    assert "One Pune Card (KYC)" in content
    assert "One Pune Card (Non-KYC / MTS)" in content
    assert "One Pune Vidyarthi Pass" in content
    assert "1800 270 5501" in content
    assert "Pune Metro Timetable (Effective 15th August 2025)" in content


def test_rules_and_stations_include_the_confirmed_updates() -> None:
    rules = (DATA_DIR / "rules.md").read_text(encoding="utf-8")
    stations = (DATA_DIR / "stations.md").read_text(encoding="utf-8")

    assert "## Commuter Etiquette" in rules
    assert "| Women | 14 | 18 | 14 | 46 |" in rules
    assert "| Senior Citizen | 2 | 4 | 2 | 8 |" in rules
    assert "## Purple Line: PCMC – Swargate" in stations
    assert "Bhosari (Nashik Phata)" in stations
    assert "## Aqua Line: Vanaz – Ramwadi" in stations
    assert "Ramwadi" in stations


def test_contact_prompt_uses_the_current_toll_free_number() -> None:
    assert "1800 270 5501" in SYSTEM_MESSAGE


def test_fare_matrix_contains_official_chart_data_without_pending_placeholder() -> None:
    fares = (DATA_DIR / "fares.md").read_text(encoding="utf-8")
    table_rows = [
        line.split("|")[1:-1]
        for line in fares.splitlines()
        if line.startswith("| ")
    ][2:]

    assert "Fares are one-way, in ₹. For a same-station entry, fare is 0." in fares
    assert "| PCMC / PIM | 0 | 15 | 15 |" in fares
    assert "| Ramwadi / RAW | 35 | 35 | 35 |" in fares
    assert "verified against the live passenger-information page" in fares
    assert "pending manual update from official images." not in fares
    assert len(table_rows) == 29
    assert all(len(row) == 30 for row in table_rows)
    assert all(int(row[index + 1].strip()) == 0 for index, row in enumerate(table_rows))


def test_runtime_fares_match_verified_official_table() -> None:
    from app.services.llm_client import FARE_MATRIX

    assert FARE_MATRIX["Shivaji Nagar"]["PCMC"] == 30
    assert FARE_MATRIX["PCMC"]["Swargate"] == 30
    assert FARE_MATRIX["Vanaz"]["Ramwadi"] == 30


def test_timetable_contains_confirmed_service_data_without_pending_placeholder() -> None:
    timetable = (DATA_DIR / "timetable.md").read_text(encoding="utf-8")

    assert "Service hours: 06:00 AM – 11:00 PM (17 hours)." in timetable
    assert "Peak frequency: every 6 minutes" in timetable
    assert "Non-peak frequency: every 10 minutes" in timetable
    assert "Purple Line (PCMC ↔ Swargate)" in timetable
    assert "Aqua Line (Vanaz ↔ Ramwadi)" in timetable
    assert "pending manual update from official images." not in timetable
