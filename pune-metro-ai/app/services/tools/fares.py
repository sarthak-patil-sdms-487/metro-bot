"""Verified fare lookup tool."""

from typing import Any

from app.services.llm_client import FARE_MATRIX, NCMC_DISCOUNT_PERCENT, resolve_station_alias


def get_fare(origin: str, destination: str) -> dict[str, Any]:
    canonical_origin = resolve_station_alias(origin)
    canonical_destination = resolve_station_alias(destination)
    if not canonical_origin or not canonical_destination:
        return {"found": False, "error": "station_not_found"}
    fare = FARE_MATRIX.get(canonical_origin, {}).get(canonical_destination)
    if not isinstance(fare, int):
        return {"found": False, "error": "fare_not_found"}
    return {
        "found": True,
        "origin": canonical_origin,
        "destination": canonical_destination,
        "cash_fare_inr": fare,
        "ncmc_fare_inr": round(fare * (1 - NCMC_DISCOUNT_PERCENT / 100), 2),
    }
