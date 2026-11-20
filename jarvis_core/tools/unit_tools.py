"""Einheiten offline umrechnen (Länge, Gewicht, Volumen, Geschwindigkeit, Temperatur, Fläche, Daten, Zeit).

Reine Tabellenrechnung ohne Modell und ohne Netz — deterministisch und exakt. Auch vom Schnellpfad genutzt."""
from __future__ import annotations

import re

from .registry import Tool, ToolContext, ToolError, ToolResult

# Faktor zur Basiseinheit je Größe: Länge m, Masse kg, Volumen l, Geschwindigkeit m/s, Fläche m², Daten Byte, Zeit s.
_TABLE: dict[str, tuple[str, float, str]] = {}


def _add(kind: str, base_factor: float, label: str, *names: str) -> None:
    for n in names:
        _TABLE[n] = (kind, base_factor, label)


_ROWS: list[tuple] = [
    ("länge", 1.0, "Meter", "m", "meter"), ("länge", 1000.0, "Kilometer", "km", "kilometer"),
    ("länge", 0.01, "Zentimeter", "cm", "zentimeter"), ("länge", 0.001, "Millimeter", "mm", "millimeter"),
    ("länge", 1609.344, "Meilen", "mi", "meile", "meilen", "mile", "miles"),
    ("länge", 0.3048, "Fuß", "ft", "fuß", "fuss", "foot", "feet"), ("länge", 0.0254, "Zoll", "in", "zoll", "inch", "inches"),
    ("länge", 0.9144, "Yards", "yd", "yard", "yards"), ("länge", 1852.0, "Seemeilen", "nm", "seemeile", "seemeilen"),
    ("masse", 1.0, "Kilogramm", "kg", "kilogramm", "kilo", "kilos"), ("masse", 0.001, "Gramm", "g", "gramm"),
    ("masse", 1e-6, "Milligramm", "mg", "milligramm"), ("masse", 1000.0, "Tonnen", "t", "tonne", "tonnen"),
    ("masse", 0.45359237, "Pfund (lb)", "lb", "lbs", "pfund", "pound", "pounds"),
    ("masse", 0.028349523125, "Unzen", "oz", "unze", "unzen", "ounce", "ounces"),
    ("volumen", 1.0, "Liter", "l", "liter"), ("volumen", 0.001, "Milliliter", "ml", "milliliter"),
    ("volumen", 3.785411784, "Gallonen (US)", "gal", "gallone", "gallonen", "gallon", "gallons"),
    ("volumen", 0.0295735295625, "Flüssigunzen (US)", "floz", "fl.oz", "flüssigunze", "flüssigunzen"),
    ("volumen", 0.2365882365, "Cups (US)", "cup", "cups", "tasse", "tassen"),
    ("volumen", 0.015, "Esslöffel", "el", "esslöffel"), ("volumen", 0.005, "Teelöffel", "tl", "teelöffel"),
    ("geschwindigkeit", 1.0, "m/s", "m/s", "meter pro sekunde"), ("geschwindigkeit", 1 / 3.6, "km/h", "km/h", "kmh", "kilometer pro stunde"),
    ("geschwindigkeit", 0.44704, "mph", "mph", "meilen pro stunde"), ("geschwindigkeit", 1852 / 3600, "Knoten", "kn", "knoten", "kt"),
    ("fläche", 1.0, "m²", "m²", "m2", "quadratmeter", "qm"), ("fläche", 10000.0, "Hektar", "ha", "hektar"),
    ("fläche", 1e6, "km²", "km²", "km2", "quadratkilometer"), ("fläche", 4046.8564224, "Acres", "acre", "acres"),
    ("daten", 1.0, "Byte", "byte", "bytes", "b"), ("daten", 1e3, "Kilobyte", "kb", "kilobyte"),
    ("daten", 1e6, "Megabyte", "mb", "megabyte"), ("daten", 1e9, "Gigabyte", "gb", "gigabyte"),
    ("daten", 1e12, "Terabyte", "tb", "terabyte"),
    ("zeit", 1.0, "Sekunden", "s", "sek", "sekunde", "sekunden"), ("zeit", 60.0, "Minuten", "min", "minute", "minuten"),
    ("zeit", 3600.0, "Stunden", "h", "std", "stunde", "stunden"), ("zeit", 86400.0, "Tage", "d", "tag", "tage", "tagen"),
    ("zeit", 604800.0, "Wochen", "woche", "wochen"),
]
for _row in _ROWS:
    _add(*_row)

_TEMP = {"c": "Celsius", "°c": "Celsius", "celsius": "Celsius", "grad celsius": "Celsius",
         "f": "Fahrenheit", "°f": "Fahrenheit", "fahrenheit": "Fahrenheit", "grad fahrenheit": "Fahrenheit",
         "k": "Kelvin", "kelvin": "Kelvin"}


def _canon(unit: str) -> str:
    u = re.sub(r"\s+", " ", str(unit).strip().casefold()).rstrip(".")
    return {"kilometer/stunde": "km/h", "kilometer pro stunde": "km/h"}.get(u, u)


def _to_celsius(v: float, u: str) -> float:
    return v if u == "Celsius" else (v - 32) * 5 / 9 if u == "Fahrenheit" else v - 273.15


def _from_celsius(c: float, u: str) -> float:
    return c if u == "Celsius" else c * 9 / 5 + 32 if u == "Fahrenheit" else c + 273.15


def fmt(x: float) -> str:
    """Deutsche Zahl mit sinnvoll vielen Stellen (kein 0,30000000000000004)."""
    if x == 0:
        return "0"
    if abs(x) >= 1e9 or abs(x) < 1e-3:
        return f"{x:.4g}".replace(".", ",")
    text = f"{x:,.{3 if abs(x) < 100 else 2}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text.replace(",", "\u2009").replace(".", ",").replace("\u2009", ".")


def convert(value: float, src: str, dst: str) -> tuple[float, str, str]:
    """(Ergebnis, Name Ausgangseinheit, Name Zieleinheit). ToolError bei Unbekanntem/Unpassendem."""
    a, b = _canon(src), _canon(dst)
    if a in _TEMP and b in _TEMP:
        ua, ub = _TEMP[a], _TEMP[b]
        k = _to_celsius(value, ua)
        if k < -273.15 - 1e-9:
            raise ToolError("Unter dem absoluten Nullpunkt gibt es keine Temperatur.")
        return _from_celsius(k, ub), ua, ub
    if a not in _TABLE or b not in _TABLE:
        unknown = src if a not in _TABLE and a not in _TEMP else dst
        raise ToolError(f"Die Einheit „{str(unknown)[:30]}“ kenne ich nicht.")
    ka, fa, la = _TABLE[a]
    kb, fb, lb = _TABLE[b]
    if ka != kb:
        raise ToolError(f"{la} lässt sich nicht in {lb} umrechnen ({ka} ≠ {kb}).")
    return value * fa / fb, la, lb


def compatible(src: str, dst: str) -> bool:
    """Kennt der Rechner beide Einheiten, und passen sie zusammen? (für den Schnellpfad: kein Raten)"""
    a, b = _canon(src), _canon(dst)
    if a in _TEMP and b in _TEMP:
        return True
    return a in _TABLE and b in _TABLE and _TABLE[a][0] == _TABLE[b][0]


def unit_convert(ctx: ToolContext, a: dict) -> ToolResult:
    try:
        value = float(str(a.get("value")).replace(",", "."))
    except (TypeError, ValueError):
        raise ToolError("value muss eine Zahl sein") from None
    res, la, lb = convert(value, str(a.get("from_unit", "")), str(a.get("to_unit", "")))
    return ToolResult(True, f"{fmt(value)} {la} = {fmt(res)} {lb}", {"result": res})


def tools() -> list[Tool]:
    S = {"type": "string"}
    return [Tool("unit_convert", "math",
                 "Rechnet Einheiten offline um: Länge, Gewicht, Volumen, Geschwindigkeit, Temperatur (C/F/K), Fläche, "
                 "Datenmengen, Zeit. Z. B. 5 Meilen in Kilometer.",
                 {"value": {"type": "number"}, "from_unit": S, "to_unit": S}, ("value", "from_unit", "to_unit"),
                 unit_convert, announce=lambda a: "Ich rechne die Einheit um, Sir.")]
