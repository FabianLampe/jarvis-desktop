"""Wächter: Jarvis meldet sich von selbst, wenn etwas Aufmerksamkeit braucht.

Geprüft wird in festen Abständen, ohne Sprachmodell: neues Gerät im Heimnetz, Termin
in Kürze, Platte fast voll, Akku fast leer. Jede Sache wird genau einmal gemeldet
(Zustand in proactive.json), nicht bei jedem Durchlauf erneut. Die Texte bestehen aus
festen Sätzen plus bereinigten Bezeichnungen — fremde Namen können nichts anweisen."""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from .tools.network_tools import clean
from .tools.registry import ToolContext

log = logging.getLogger("jarvis.proactive")
HOME_NET_MAX_DEVICES = 64        # größere Netze sind geteilte Netze (Büro, Wohnheim): dort nichts melden


class Watcher:
    def __init__(self, ctx: ToolContext, notify: Callable[[str], None], *,
                 now: Callable[[], float] = time.time):
        self.ctx, self.cfg, self.notify, self._now = ctx, ctx.cfg.proactive, notify, now
        self.path = Path(ctx.data_dir) / "proactive.json"
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._state = self._load()

    # ---------------------------------------------------------- Zustand

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._state, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            log.debug("Wächterzustand nicht speicherbar", exc_info=True)

    def _once(self, key: str, ttl_s: float) -> bool:
        """True, wenn `key` in den letzten ttl_s Sekunden noch nicht gemeldet wurde (und merkt es sich)."""
        seen = self._state.setdefault("seen", {})
        now = self._now()
        for k in [k for k, t in seen.items() if now - t > 14 * 86400]:
            del seen[k]
        if key in seen and now - seen[key] < ttl_s:
            return False
        seen[key] = now
        return True

    # ---------------------------------------------------------- Prüfungen

    def check_devices(self) -> list[str]:
        from .tools.network_tools import scan_devices
        res = scan_devices(self.ctx)
        devs = res["devices"]
        if res["partial"] or len(devs) > HOME_NET_MAX_DEVICES:
            return []
        out = []
        for d in devs:
            if d.get("new") and self._once(f"dev:{d['mac']}", 365 * 86400):
                foreign = clean(d.get("name") or d.get("vendor") or "", 60)      # nur Fremdes bereinigen
                what = foreign or ("ein Gerät mit privater Adresse, vermutlich ein Handy oder Tablet"
                                   if d.get("private_mac") else "ein unbekanntes Gerät")
                out.append(f"Neues Gerät im Netzwerk, Sir: {what} unter {clean(d['ip'], 15)}.")
        return out[:3]

    def check_disk(self) -> list[str]:
        from .tools.health_tools import disk_status
        d = disk_status()
        if d["used_pct"] >= self.cfg.disk_warn_pct and self._once("disk", 86400):
            return [f"Die Festplatte ist zu {round(d['used_pct'])} Prozent belegt, Sir. "
                    f"Es sind noch {d['free'] / 1e9:.0f} Gigabyte frei."]
        return []

    def check_battery(self) -> list[str]:
        import psutil
        b = psutil.sensors_battery()
        if b is None:
            return []
        if b.power_plugged or b.percent > self.cfg.battery_warn_pct + 10:
            self._state.get("seen", {}).pop("battery", None)        # wieder geladen ⇒ nächste Warnung erlaubt
            return []
        if b.percent <= self.cfg.battery_warn_pct and self._once("battery", 6 * 3600):
            return [f"Der Akku steht bei {round(b.percent)} Prozent, Sir. Ein Netzteil wäre angebracht."]
        return []

    def check_calendar(self) -> list[str]:
        from .integrations.services import _integ
        g = _integ(self.ctx, "google_calendar")
        if not g.enabled or g.status().state != "ready":
            return []
        tz = ZoneInfo(self.ctx.cfg.reminders.timezone)
        now = datetime.fromtimestamp(self._now(), tz)
        res = g.service().events().list(
            calendarId="primary", timeMin=now.isoformat(),
            timeMax=(now + timedelta(minutes=self.cfg.calendar_lead_min)).isoformat(),
            singleEvents=True, orderBy="startTime", maxResults=5).execute()
        out = []
        for e in res.get("items", []):
            start = (e.get("start") or {}).get("dateTime")
            if not start:
                continue                                           # ganztägig: steht im Briefing
            begin = datetime.fromisoformat(start.replace("Z", "+00:00"))
            mins = round((begin - now).total_seconds() / 60)
            if mins < 0 or not self._once(f"cal:{e.get('id')}:{start}", 86400):
                continue
            title = clean(e.get("summary") or "ein Termin", 60)
            out.append(f"In {mins} Minuten beginnt „{title}“, Sir." if mins > 1 else f"„{title}“ beginnt jetzt, Sir.")
        return out

    # ------------------------------------------------------------ Ablauf

    def check_once(self) -> list[str]:
        """Ein Durchlauf aller eingeschalteten Prüfungen. Fehler einer Prüfung stören die anderen nicht."""
        c = self.cfg
        checks = [(c.calendar, self.check_calendar), (c.battery, self.check_battery),
                  (c.disk, self.check_disk), (c.new_devices, self.check_devices)]
        notices: list[str] = []
        for enabled, fn in checks:
            if not enabled or self._stop.is_set():
                continue
            try:
                notices += fn()
            except Exception as exc:  # noqa: BLE001 — offline, fehlende Rechte, Kalender nicht eingerichtet …
                log.info("Wächter: %s übersprungen (%s: %s)", fn.__name__, type(exc).__name__, exc)
        self._save()
        return notices

    def _loop(self) -> None:
        # Erst nach der Startsequenz prüfen; Termine häufiger als den Rest.
        if self._stop.wait(90):
            return
        tick = 0
        while not self._stop.is_set():
            full = tick % max(1, self.cfg.interval_min) == 0
            try:
                notices = self.check_once() if full else self._calendar_only()
                for n in notices:
                    self.notify(n)
            except Exception:  # noqa: BLE001
                log.exception("Wächter-Durchlauf fehlgeschlagen")
            tick += 1
            if self._stop.wait(60):
                return

    def _calendar_only(self) -> list[str]:
        if not self.cfg.calendar:
            return []
        try:
            out = self.check_calendar()
        except Exception as exc:  # noqa: BLE001
            log.debug("Wächter: Kalender übersprungen (%s)", exc)
            return []
        self._save()
        return out

    def start(self) -> None:
        if self._thread is None and self.cfg.enabled:
            self._thread = threading.Thread(target=self._loop, name="jarvis-watcher", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
