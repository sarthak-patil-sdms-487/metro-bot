"""Verified station-resolution tool."""

from typing import Any

from app.services.llm_client import OPERATIONAL_LINES, resolve_station_alias


def get_station_info(station: str, information_needed: str = "general") -> dict[str, Any]:
    canonical = resolve_station_alias(station)
    if not canonical:
        return {"found": False, "query": station, "error": "station_not_found"}
    lines = [name for name, stations in OPERATIONAL_LINES.items() if canonical in stations]
    return {
        "found": True,
        "canonical_name": canonical,
        "lines": lines,
        "information_needed": information_needed,
        "is_operational": bool(lines),
    }
