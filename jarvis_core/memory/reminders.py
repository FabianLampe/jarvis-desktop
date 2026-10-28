"""Erinnerungen und wiederkehrende Aufgaben (APScheduler 3.x, persistent in SQLite).

Wahrheit liegt in der Tabelle `reminders`; der APScheduler-JobStore (SQLite) hält
die Auslöser dauerhaft. Beim Start werden verpasste Termine ausdrücklich erkannt
und als „verpasst“ zugestellt — der Scheduler läuft nur, solange Jarvis läuft und
der Mac nicht schläft. Es gibt kein Aufwecken des Rechners.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from .store import _Tx

log = logging.getLogger("jarvis.reminders")

_ACTIVE: dict[str, "ReminderService"] = {}      # db-Pfad → Dienst (für den Job-Einstieg)


def fire(db_key: str, reminder_id: int) -> None:
    """Job-Einstieg (muss auf Modulebene stehen, damit der JobStore ihn speichern kann)."""
    svc = _ACTIVE.get(db_key)
    if svc is not None:
        svc._fire(reminder_id)


def _trigger(due: datetime, repeat: Optional[str], tz: ZoneInfo):
    if not repeat:
        return DateTrigger(run_date=due, timezone=tz)
    kw = dict(hour=due.hour, minute=due.minute, timezone=tz)
    if repeat == "daily":
        return CronTrigger(**kw)
    if repeat == "weekdays":
        return CronTrigger(day_of_week="mon-fri", **kw)
    if repeat == "weekly":
        return CronTrigger(day_of_week=due.weekday(), **kw)
    if repeat == "monthly":
        return CronTrigger(day=due.day, **kw)
    raise ValueError(f"Unbekannte Wiederholung: {repeat}")


class ReminderService:
    def __init__(self, data_dir: Path, tz: str = "Europe/Berlin", misfire_grace_s: int = 86400,
                 on_due: Optional[Callable[[dict], None]] = None):
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.tz = ZoneInfo(tz)
        self.grace = timedelta(seconds=misfire_grace_s)
        self.db_path = self.dir / "reminders.db"
        self.on_due = on_due
        self._key = str(self.db_path)
        self._init_db()
        self.scheduler = BackgroundScheduler(
            jobstores={"default": SQLAlchemyJobStore(url=f"sqlite:///{self.dir / 'scheduler.sqlite'}")},
            timezone=self.tz, job_defaults={"coalesce": True,
                                            # nach Ruhezustand: verspätet zustellen, solange innerhalb der Kulanz
                                            "misfire_grace_time": max(60, int(self.grace.total_seconds()))})
        self._started = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------ DB

    def _db(self):
        con = sqlite3.connect(self.db_path, timeout=10)
        con.row_factory = sqlite3.Row
        return _Tx(con)

    def _init_db(self) -> None:
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS reminders(
                id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, due_at TEXT NOT NULL,
                repeat TEXT, status TEXT NOT NULL DEFAULT 'active', last_fired TEXT, created TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS deliveries(
                id INTEGER PRIMARY KEY AUTOINCREMENT, reminder_id INTEGER, text TEXT NOT NULL,
                due_at TEXT NOT NULL, fired_at TEXT NOT NULL, missed INTEGER NOT NULL DEFAULT 0,
                delivered_at TEXT)""")

    def _now(self) -> datetime:
        return datetime.now(self.tz)

    # --------------------------------------------------- Lebenszyklus

    def start(self) -> list[dict]:
        """Startet den Scheduler. Gibt die beim Start erkannten verpassten Termine zurück."""
        if self._started:
            return []
        _ACTIVE[self._key] = self
        missed = self._catch_up()
        self.scheduler.start()
        self._started = True
        for r in self.list():
            self._schedule(r)
        return missed

    def shutdown(self) -> None:
        if self._started:
            self.scheduler.shutdown(wait=False)
            self._started = False
        _ACTIVE.pop(self._key, None)

    def _catch_up(self) -> list[dict]:
        """Verpasste Termine (während Jarvis aus war) erkennen und als „verpasst“ vormerken."""
        now = self._now()
        out = []
        for r in self.list():
            due = datetime.fromisoformat(r["due_at"])
            if due > now:
                continue
            if r["repeat"]:                         # zuletzt verpasstes Vorkommen zählt, nicht das älteste
                trig = _trigger(due, r["repeat"], self.tz)
                last, nxt = due, trig.get_next_fire_time(None, due + timedelta(seconds=1))
                while nxt is not None and nxt <= now:
                    last, nxt = nxt, trig.get_next_fire_time(nxt, nxt + timedelta(seconds=1))
                due = last
            late = now - due
            # Wiederkehrend: nur der zuletzt verpasste Termin zählt (coalesce)
            self._record_delivery(r, due, missed=True, too_old=late > self.grace)
            out.append(self._advance(r, now))
        return out

    # -------------------------------------------------------- Anlegen

    def create(self, text: str, due: datetime, repeat: Optional[str] = None) -> dict:
        text = text.strip()
        if not text:
            raise ValueError("Text der Erinnerung fehlt")
        due = due.astimezone(self.tz)
        if due <= self._now() and not repeat:
            raise ValueError(f"Der Zeitpunkt liegt in der Vergangenheit ({due:%d.%m.%Y %H:%M})")
        with self._db() as db:
            cur = db.execute("INSERT INTO reminders(text,due_at,repeat,created) VALUES (?,?,?,?)",
                             (text, due.isoformat(), repeat, self._now().isoformat()))
            rid = int(cur.lastrowid or 0)
        r = self.get(rid)
        assert r is not None, "gerade angelegte Erinnerung fehlt"
        if self._started:
            self._schedule(r)
        return r

    def get(self, rid: int) -> Optional[dict]:
        with self._db() as db:
            row = db.execute("SELECT * FROM reminders WHERE id=?", (rid,)).fetchone()
        return dict(row) if row else None

    def list(self, include_inactive: bool = False) -> list[dict]:
        with self._db() as db:
            q = "SELECT * FROM reminders" + ("" if include_inactive else " WHERE status='active'") + " ORDER BY due_at"
            return [dict(r) for r in db.execute(q)]

    def cancel(self, rid: int) -> bool:
        with self._db() as db:
            n = db.execute("UPDATE reminders SET status='cancelled' WHERE id=? AND status='active'", (rid,)).rowcount
        if self._started:
            try:
                self.scheduler.remove_job(f"r{rid}")
            except Exception:  # noqa: BLE001
                pass
        return n > 0

    # ------------------------------------------------- Zeitplanung

    def _schedule(self, r: dict) -> None:
        due = datetime.fromisoformat(r["due_at"])
        self.scheduler.add_job(fire, _trigger(due, r["repeat"], self.tz), args=[self._key, r["id"]],
                               id=f"r{r['id']}", replace_existing=True)

    def _next_after(self, r: dict, after: datetime) -> Optional[datetime]:
        if not r["repeat"]:
            return None
        trig = _trigger(datetime.fromisoformat(r["due_at"]), r["repeat"], self.tz)
        return trig.get_next_fire_time(None, after)

    def _advance(self, r: dict, now: datetime) -> dict:
        """Nach einem (auch verpassten) Termin: nächste Fälligkeit setzen bzw. abschließen."""
        nxt = self._next_after(r, now)
        with self._db() as db:
            if nxt is None:
                db.execute("UPDATE reminders SET status='done', last_fired=? WHERE id=?",
                           (now.isoformat(), r["id"]))
            else:
                db.execute("UPDATE reminders SET due_at=?, last_fired=? WHERE id=?",
                           (nxt.isoformat(), now.isoformat(), r["id"]))
        if nxt is None and self._started:
            try:
                self.scheduler.remove_job(f"r{r['id']}")
            except Exception:  # noqa: BLE001
                pass
        updated = self.get(r["id"])
        assert updated is not None
        return updated

    # --------------------------------------------------- Zustellung

    def _record_delivery(self, r: dict, due: datetime, missed: bool, too_old: bool = False) -> dict:
        text = r["text"] + (f" (verpasst — fällig war {due:%d.%m. %H:%M})" if missed else "")
        with self._db() as db:
            cur = db.execute("INSERT INTO deliveries(reminder_id,text,due_at,fired_at,missed,delivered_at) "
                             "VALUES (?,?,?,?,?,?)",
                             (r["id"], text, due.isoformat(), self._now().isoformat(), int(missed),
                              # älter als die Kulanz (misfire_grace_s): protokolliert, aber nicht mehr angesagt
                              self._now().isoformat() if too_old else None))
            did = cur.lastrowid
        if too_old:
            log.info("Erinnerung #%s war %s fällig — älter als die Kulanz, wird nicht mehr angesagt",
                     r["id"], due.strftime("%d.%m. %H:%M"))
        return {"id": did, "reminder_id": r["id"], "text": text, "missed": missed,
                "due_at": due.isoformat()}

    def _fire(self, rid: int) -> None:
        r = self.get(rid)
        if not r or r["status"] != "active":
            return
        due = datetime.fromisoformat(r["due_at"])
        late = self._now() - due > timedelta(minutes=2)       # z. B. nach dem Aufwachen aus dem Ruhezustand
        d = self._record_delivery(r, due, missed=late)
        self._advance(r, self._now())
        self.deliver(d)

    def deliver(self, d: dict) -> bool:
        ok = False
        if self.on_due is not None:
            try:
                self.on_due(d)
                ok = True
            except Exception:  # noqa: BLE001
                log.exception("Zustellung der Erinnerung %s fehlgeschlagen", d["id"])
        if ok:
            with self._db() as db:
                db.execute("UPDATE deliveries SET delivered_at=? WHERE id=?", (self._now().isoformat(), d["id"]))
        return ok

    def undelivered(self) -> list[dict]:
        with self._db() as db:
            return [dict(r) for r in db.execute(
                "SELECT * FROM deliveries WHERE delivered_at IS NULL ORDER BY id")]

    def deliver_pending(self) -> int:
        n = 0
        for row in self.undelivered():
            if self.deliver({"id": row["id"], "reminder_id": row["reminder_id"],
                             "text": row["text"], "missed": bool(row["missed"]),
                             "due_at": row["due_at"]}):
                n += 1
        return n

    def format(self, r: dict) -> str:
        due = datetime.fromisoformat(r["due_at"]).astimezone(self.tz)
        rep = {"daily": "täglich", "weekdays": "werktags", "weekly": "wöchentlich",
               "monthly": "monatlich"}.get(r["repeat"] or "", "")
        return f"#{r['id']} {due:%a %d.%m.%Y %H:%M} {rep}: {r['text']}".replace("  ", " ")
