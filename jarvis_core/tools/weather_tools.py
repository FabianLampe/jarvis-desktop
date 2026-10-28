"""Wetter über Open-Meteo (kein API-Key). Beliebiger Ort, heute bis übermorgen."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import requests

from .registry import Tool, ToolContext, ToolError, ToolResult

_CODES = {0: "klar", 1: "überwiegend klar", 2: "teils bewölkt", 3: "bewölkt", 45: "neblig", 48: "Reifnebel",
          51: "leichter Nieselregen", 53: "Nieselregen", 55: "starker Nieselregen", 56: "gefrierender Nieselregen",
          57: "gefrierender Nieselregen", 61: "leichter Regen", 63: "Regen", 65: "starker Regen",
          66: "gefrierender Regen", 67: "gefrierender Regen", 71: "leichter Schneefall", 73: "Schneefall",
          75: "starker Schneefall", 77: "Schneegriesel", 80: "leichte Regenschauer", 81: "Regenschauer",
          82: "heftige Regenschauer", 85: "Schneeschauer", 86: "starke Schneeschauer", 95: "Gewitter",
          96: "Gewitter mit Hagel", 99: "schweres Gewitter mit Hagel"}
_DAYS = {"heute": 0, "morgen": 1, "übermorgen": 2, "uebermorgen": 2}
TIMEOUT = 8


def _geocode(place: str) -> tuple[float, float, str]:
    try:
        r = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                         params={"name": place, "count": 1, "language": "de"}, timeout=TIMEOUT)
        r.raise_for_status()
    except requests.RequestException as exc:
        raise ToolError(f"Ortssuche nicht erreichbar ({type(exc).__name__})") from exc
    res = (r.json().get("results") or [])
    if not res:
        raise ToolError(f"Ich finde den Ort „{place}“ nicht")
    x = res[0]
    return float(x["latitude"]), float(x["longitude"]), x.get("name", place)


def weather(ctx: ToolContext, a: dict) -> ToolResult:
    b = ctx.cfg.briefing
    place = str(a.get("place") or "").strip()
    if place:
        lat, lon, name = _geocode(place)
    else:
        lat, lon, name = b.weather_lat, b.weather_lon, b.weather_ort
    day = _DAYS.get(str(a.get("when") or "heute").lower(), 0)
    try:
        r = requests.get("https://api.open-meteo.com/v1/forecast", timeout=TIMEOUT, params={
            "latitude": lat, "longitude": lon, "timezone": ctx.cfg.reminders.timezone, "forecast_days": 3,
            "current": "temperature_2m,weather_code",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                     "precipitation_sum,wind_speed_10m_max"})
        r.raise_for_status()
        d = r.json()
    except (requests.RequestException, ValueError) as exc:
        raise ToolError(f"Wetterdienst nicht erreichbar ({type(exc).__name__})") from exc
    dl = d["daily"]
    code = int(dl["weather_code"][day])
    when = ["heute", "morgen", "übermorgen"][day]
    rain = dl.get("precipitation_probability_max", [None] * 3)[day]
    mm = dl.get("precipitation_sum", [None] * 3)[day]
    parts = [f"{name}, {when}: {_CODES.get(code, 'wechselhaft')}",
             f"{round(dl['temperature_2m_min'][day])} bis {round(dl['temperature_2m_max'][day])} Grad"]
    if rain is not None:
        parts.append(f"Regenwahrscheinlichkeit {round(rain)} Prozent" + (f" (ca. {mm:.1f} mm)" if mm else ""))
    parts.append(f"Wind bis {round(dl['wind_speed_10m_max'][day])} km/h")
    if day == 0 and d.get("current"):
        parts.append(f"aktuell {round(d['current']['temperature_2m'])} Grad")
    text = ", ".join(parts) + "."
    if rain is not None and rain >= 50:
        text += " Ein Schirm wäre ratsam."
    return ToolResult(True, text, {"place": name, "day": when})


def tools() -> list[Tool]:
    S = {"type": "string"}
    return [Tool("weather", "weather",
                 "Wettervorhersage für heute, morgen oder übermorgen (Open-Meteo). Ohne place: Heimatort.",
                 {"place": {**S, "description": "Ort, z. B. Berlin (leer = Heimatort)"},
                  "when": {**S, "enum": ["heute", "morgen", "übermorgen"]}}, (), weather,
                 announce=lambda a: f"Ich sehe nach dem Wetter{' in ' + a['place'] if a.get('place') else ''}, Sir.")]
