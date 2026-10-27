"""Bündelt die Briefing-Quellen und erzeugt Dashboard-Daten + Sprech-Skript."""
from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Optional

from . import github, weather

log = logging.getLogger("jarvis.briefing")

_WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
_MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
           "September", "Oktober", "November", "Dezember"]


def _now_text(dt: Optional[datetime] = None) -> str:
    dt = dt or datetime.now()
    return f"{_WEEKDAYS[dt.weekday()]}, {dt.day}. {_MONTHS[dt.month - 1]}, {dt.hour} Uhr {dt.minute:02d}"


class BriefingService:
    """Sammelt alle Quellen parallel und fehlertolerant."""

    def __init__(self, cfg, sysmon):
        self.cfg = cfg
        self.sysmon = sysmon

    def collect(self) -> dict[str, Any]:
        b = self.cfg.briefing
        token = os.environ.get("JARVIS_GITHUB_TOKEN")
        with ThreadPoolExecutor(max_workers=4) as ex:
            tasks = {
                "weather": ex.submit(weather.today, b.weather_lat, b.weather_lon),
                "issues": ex.submit(github.open_issues, b.github_repo, token, b.github_issue_limit),
                "mails": ex.submit(self._mails),
            }
            results = {k: self._safe(f) for k, f in tasks.items()}
        w = results["weather"]
        if w:
            w = {**w, "ort": b.weather_ort}
        return {
            "datetime": _now_text(),
            "weather": w,
            "issues": results["issues"],
            "mails": results["mails"],
            "system": self.sysmon.status(),
            "repo": b.github_repo,
        }

    def _mails(self):
        if not self.cfg.briefing.gmail_enabled:
            return None
        from . import gmail
        service = gmail.build_service()
        if service is None:
            return None
        return gmail.recent_unread(service, self.cfg.briefing.gmail_max)

    @staticmethod
    def _safe(future):
        try:
            return future.result(timeout=12)
        except Exception as exc:  # noqa: BLE001
            log.warning("Briefing-Quelle fehlgeschlagen: %s", exc)
            return None


def build_script(data: dict) -> list[str]:
    """Deterministische deutsche Satzliste (Persona-Ton) — NICHT vom LLM."""
    s: list[str] = []
    s.append(f"Guten Tag, Sir. Es ist {data['datetime']}.")

    w = data.get("weather")
    if w:
        s.append(f"In {w.get('ort', 'Ihrer Region')} ist es {w['temp_now']} Grad und {w['text']}, "
                 f"Höchstwert {w['temp_max']}, Tiefstwert {w['temp_min']} Grad.")
    else:
        s.append("Das Wetter erreiche ich gerade nicht, Sir.")

    mails = data.get("mails")
    if mails is None:
        s.append("Ihr Postfach erreiche ich gerade nicht, Sir.")
    elif not mails:
        s.append("Neue Nachrichten haben Sie keine.")
    else:
        s.append(f"Sie haben {len(mails)} neue Nachrichten.")
        for m in mails[:3]:
            s.append(f"Von {m['sender']}: {m['subject']}.")

    issues = data.get("issues")
    if issues is None:
        s.append("Zu GitHub erreiche ich gerade nichts, Sir.")
    elif not issues:
        s.append("Im Repository sind keine offenen Punkte.")
    else:
        s.append(f"Im Repository sind {len(issues)} offene Punkte.")
        for it in issues[:3]:
            s.append(f"Nummer {it['number']}: {it['title']}.")

    sysd = data.get("system") or {}
    if sysd:
        vram = sysd.get("vram")
        vram_txt = f", Grafikspeicher {vram} Prozent" if vram is not None else ""
        s.append(f"Das System läuft ruhig: Prozessor {sysd.get('cpu', 0)} Prozent, "
                 f"Arbeitsspeicher {sysd.get('ram', 0)} Prozent{vram_txt}.")

    s.append("Möchten Sie zu etwas mehr hören, Sir?")
    return s
