"""Wetter heute über Open-Meteo (kein API-Key)."""
from __future__ import annotations

import logging
from typing import Optional

import requests

log = logging.getLogger("jarvis.briefing.weather")

# https://open-meteo.com/en/docs — WMO-Wettercodes (gekürzt, deutsch)
_CODES = {
    0: "klar", 1: "überwiegend klar", 2: "teils bewölkt", 3: "bewölkt",
    45: "neblig", 48: "Reifnebel", 51: "leichter Nieselregen", 53: "Nieselregen",
    55: "starker Nieselregen", 61: "leichter Regen", 63: "Regen", 65: "starker Regen",
    71: "leichter Schneefall", 73: "Schneefall", 75: "starker Schneefall",
    80: "Regenschauer", 81: "Regenschauer", 82: "heftige Regenschauer",
    95: "Gewitter", 96: "Gewitter mit Hagel", 99: "schweres Gewitter mit Hagel",
}


def today(lat: float, lon: float, timeout: float = 6.0) -> Optional[dict]:
    """Liefert {temp_now, temp_max, temp_min, code, text} oder None bei Fehler."""
    try:
        r = requests.get("https://api.open-meteo.com/v1/forecast", params={
            "latitude": lat, "longitude": lon, "current": "temperature_2m,weather_code",
            "daily": "temperature_2m_max,temperature_2m_min", "timezone": "auto",
            "forecast_days": 1,
        }, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        code = int(data["current"]["weather_code"])
        return {
            "temp_now": round(data["current"]["temperature_2m"]),
            "temp_max": round(data["daily"]["temperature_2m_max"][0]),
            "temp_min": round(data["daily"]["temperature_2m_min"][0]),
            "code": code, "text": _CODES.get(code, "wechselhaft"),
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("Wetter nicht abrufbar: %s", exc)
        return None
