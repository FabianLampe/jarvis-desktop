"""Die fünf Integrationen gegen ihre echten Schnittstellen (HTTP/OAuth)."""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import logging
import os
import secrets
import threading
import time
import urllib.parse
import webbrowser
from datetime import datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

import requests

from ..tools.registry import (ALLOW, CONFIRM, Assessment, Tool, ToolContext, ToolError,
                              ToolResult, wrap_untrusted)
from .base import Integration, Status, secrets_dir

log = logging.getLogger("jarvis.integrations")
TIMEOUT = 12


def _http(method: str, url: str, **kw) -> requests.Response:
    try:
        r = requests.request(method, url, timeout=TIMEOUT, **kw)
    except requests.RequestException as exc:
        raise ToolError(f"Dienst nicht erreichbar: {type(exc).__name__}") from exc
    return r


# ====================================================== Google Calendar

CAL_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


class GoogleCalendar(Integration):
    name, label = "google_calendar", "Google Kalender"
    setup_hint = ("OAuth-Client (Desktop-App) als .secrets/credentials.json ablegen, dann "
                  "`python -m jarvis_core.integrations.services calendar-login`")

    def missing(self) -> list[str]:
        return [] if (secrets_dir() / "credentials.json").is_file() else [".secrets/credentials.json"]

    def local_setup_status(self) -> Optional[Status]:
        if not (secrets_dir() / "calendar_token.json").is_file():
            return Status("needs_setup", "Login fehlt: `python -m jarvis_core.integrations.services calendar-login`",
                          [".secrets/calendar_token.json"])
        return None

    def service(self):
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        creds = Credentials.from_authorized_user_file(str(secrets_dir() / "calendar_token.json"), CAL_SCOPES)
        return build("calendar", "v3", credentials=creds, cache_discovery=False)

    def probe(self) -> Status:
        self.service().calendarList().list(maxResults=1).execute()
        return Status("ready", "verbunden")


def calendar_login() -> None:
    from google_auth_oauthlib.flow import InstalledAppFlow
    cred = secrets_dir() / "credentials.json"
    if not cred.is_file():
        raise SystemExit(f"Lege den OAuth-Client als {cred} ab (Google Cloud → Desktop-App).")
    creds = InstalledAppFlow.from_client_secrets_file(str(cred), CAL_SCOPES).run_local_server(port=0)
    (secrets_dir() / "calendar_token.json").write_text(creds.to_json())
    print("Kalender-Login gespeichert.")


# ============================================================= Spotify

SPOTIFY_SCOPES = "user-read-playback-state user-modify-playback-state user-read-currently-playing"
SPOTIFY_REDIRECT = "http://127.0.0.1:8888/callback"


class Spotify(Integration):
    name, label = "spotify", "Spotify"
    env = ("JARVIS_SPOTIFY_CLIENT_ID",)
    setup_hint = ("App im Spotify-Developer-Dashboard anlegen (Redirect-URI "
                  f"{SPOTIFY_REDIRECT}), JARVIS_SPOTIFY_CLIENT_ID in .env, dann "
                  "`python -m jarvis_core.integrations.services spotify-login`. Steuerung braucht Premium.")

    def local_setup_status(self) -> Optional[Status]:
        if not (secrets_dir() / "spotify_token.json").is_file():
            return Status("needs_setup", "Login fehlt: `python -m jarvis_core.integrations.services spotify-login`",
                          [".secrets/spotify_token.json"])
        return None

    def token(self) -> str:
        p = secrets_dir() / "spotify_token.json"
        data = json.loads(p.read_text())
        if data.get("expires_at", 0) > time.time() + 30:
            return data["access_token"]
        r = _http("POST", "https://accounts.spotify.com/api/token", data={
            "grant_type": "refresh_token", "refresh_token": data["refresh_token"],
            "client_id": os.environ["JARVIS_SPOTIFY_CLIENT_ID"]})
        if not r.ok:
            raise ToolError("Spotify-Anmeldung abgelaufen — spotify-login erneut ausführen")
        new = r.json()
        data.update(access_token=new["access_token"], expires_at=time.time() + new["expires_in"])
        data["refresh_token"] = new.get("refresh_token", data["refresh_token"])
        p.write_text(json.dumps(data))
        return data["access_token"]

    def api(self, method: str, path: str, **kw) -> requests.Response:
        return _http(method, "https://api.spotify.com/v1" + path,
                     headers={"Authorization": f"Bearer {self.token()}"}, **kw)

    def probe(self) -> Status:
        r = self.api("GET", "/me")
        return Status("ready", "verbunden") if r.ok else Status("error", f"HTTP {r.status_code}")


def spotify_login() -> None:
    cid = os.environ.get("JARVIS_SPOTIFY_CLIENT_ID")
    if not cid:
        raise SystemExit("JARVIS_SPOTIFY_CLIENT_ID fehlt (.env)")
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(8)
    got: dict = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got.update({k: v[0] for k, v in q.items()})
            self.send_response(200); self.end_headers()
            self.wfile.write("Jarvis ist verbunden. Dieses Fenster kann geschlossen werden.".encode())
        def log_message(self, format, *args): pass

    srv = http.server.HTTPServer(("127.0.0.1", 8888), H)
    url = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode({
        "client_id": cid, "response_type": "code", "redirect_uri": SPOTIFY_REDIRECT,
        "scope": SPOTIFY_SCOPES, "code_challenge_method": "S256", "code_challenge": challenge, "state": state})
    print("Öffne:", url); webbrowser.open(url)
    srv.timeout = 180; srv.handle_request()
    if got.get("state") != state or "code" not in got:
        raise SystemExit("Login abgebrochen oder ungültig")
    r = requests.post("https://accounts.spotify.com/api/token", data={
        "grant_type": "authorization_code", "code": got["code"], "redirect_uri": SPOTIFY_REDIRECT,
        "client_id": cid, "code_verifier": verifier}, timeout=TIMEOUT)
    r.raise_for_status()
    d = r.json(); d["expires_at"] = time.time() + d["expires_in"]
    secrets_dir().mkdir(parents=True, exist_ok=True)
    (secrets_dir() / "spotify_token.json").write_text(json.dumps(d))
    print("Spotify-Login gespeichert.")


# ======================================================= Home Assistant

HA_CONFIRM_DOMAINS = {"lock", "alarm_control_panel", "cover", "script", "shell_command", "automation",
                      "homeassistant", "water_heater", "valve"}


class HomeAssistant(Integration):
    name, label = "home_assistant", "Home Assistant"
    env = ("JARVIS_HA_URL", "JARVIS_HA_TOKEN")
    setup_hint = ("In Home Assistant ein Langzeit-Zugangstoken erzeugen; JARVIS_HA_URL "
                  "(z. B. http://homeassistant.local:8123) und JARVIS_HA_TOKEN in .env eintragen")

    def api(self, method: str, path: str, **kw) -> requests.Response:
        return _http(method, os.environ["JARVIS_HA_URL"].rstrip("/") + "/api" + path,
                     headers={"Authorization": f"Bearer {os.environ['JARVIS_HA_TOKEN']}"}, **kw)

    def probe(self) -> Status:
        r = self.api("GET", "/")
        if r.status_code == 401:
            return Status("error", "Token abgelehnt (401)")
        return Status("ready", r.json().get("message", "verbunden")) if r.ok else Status("error", f"HTTP {r.status_code}")


# ============================================================ Telegram

class Telegram(Integration):
    name, label = "telegram", "Telegram"
    env = ("JARVIS_TELEGRAM_TOKEN", "JARVIS_TELEGRAM_CHAT_ID")
    setup_hint = ("Bot über @BotFather anlegen; JARVIS_TELEGRAM_TOKEN und die eigene "
                  "JARVIS_TELEGRAM_CHAT_ID in .env eintragen")

    def api(self, method: str, **kw) -> dict:
        r = _http("POST", f"https://api.telegram.org/bot{os.environ['JARVIS_TELEGRAM_TOKEN']}/{method}", json=kw)
        data = r.json() if r.content else {}
        if not data.get("ok"):
            raise ToolError(f"Telegram: {data.get('description') or r.status_code}")
        return data["result"]

    def probe(self) -> Status:
        me = self.api("getMe")
        return Status("ready", f"Bot @{me.get('username')}")


class TelegramBot(threading.Thread):
    """Optionale Gegenstelle: nimmt Textnachrichten NUR vom eigenen Chat entgegen."""

    def __init__(self, tg: Telegram, handler):
        super().__init__(name="jarvis-telegram", daemon=True)
        self.tg, self.handler, self._stop_ev = tg, handler, threading.Event()

    def stop(self):
        self._stop_ev.set()

    def run(self):
        offset, allowed = 0, str(os.environ["JARVIS_TELEGRAM_CHAT_ID"])
        while not self._stop_ev.is_set():
            try:
                for u in self.tg.api("getUpdates", offset=offset, timeout=25):
                    offset = u["update_id"] + 1
                    m = u.get("message") or {}
                    if str((m.get("chat") or {}).get("id")) != allowed or not m.get("text"):
                        continue          # fremde Chats werden ignoriert
                    reply = self.handler(m["text"])
                    self.tg.api("sendMessage", chat_id=allowed, text=reply[:4000])
            except Exception as exc:  # noqa: BLE001
                log.warning("Telegram-Polling: %s", exc)
                self._stop_ev.wait(10)


# ============================================================= Discord

class Discord(Integration):
    name, label = "discord", "Discord"
    env = ("JARVIS_DISCORD_TOKEN", "JARVIS_DISCORD_CHANNEL_ID")
    setup_hint = ("Bot im Discord-Developer-Portal anlegen, zum Server einladen; JARVIS_DISCORD_TOKEN "
                  "und JARVIS_DISCORD_CHANNEL_ID in .env eintragen (nur Senden; Empfangen nicht implementiert)")

    def api(self, method: str, path: str, **kw) -> requests.Response:
        return _http(method, "https://discord.com/api/v10" + path,
                     headers={"Authorization": f"Bot {os.environ['JARVIS_DISCORD_TOKEN']}"}, **kw)

    def probe(self) -> Status:
        r = self.api("GET", "/users/@me")
        if r.status_code == 401:
            return Status("error", "Token abgelehnt (401)")
        return Status("ready", f"Bot {r.json().get('username')}") if r.ok else Status("error", f"HTTP {r.status_code}")


# =========================================================== Werkzeuge

def build(cfg_integrations: dict) -> dict[str, Integration]:
    classes = (GoogleCalendar, Spotify, HomeAssistant, Telegram, Discord)
    return {c.name: c(cfg_integrations.get(c.name)) for c in classes}


def _integ(ctx: ToolContext, name: str) -> Any:
    return ctx.service("integrations", lambda: build(ctx.cfg.integrations))[name]


def integration_status(ctx: ToolContext, a: dict) -> ToolResult:
    ints = ctx.service("integrations", lambda: build(ctx.cfg.integrations))
    lines = [i.status().line(i.label) for i in ints.values()]
    for n, label in (("translation", "Offline-Übersetzung"), ("face_recognition", "Gesichtserkennung")):
        from .optional import optional_status
        lines.append(optional_status(ctx.cfg.integrations.get(n) or {}, n).line(label))
    return ToolResult(True, "\n".join(lines))


def calendar_upcoming(ctx: ToolContext, a: dict) -> ToolResult:
    g = _integ(ctx, "google_calendar"); g.require_ready()
    tz = ZoneInfo(ctx.cfg.reminders.timezone)
    now = datetime.now(tz)
    days = int(a.get("days") or 7)
    try:
        res = g.service().events().list(calendarId="primary", timeMin=now.isoformat(),
                                        timeMax=(now + timedelta(days=days)).isoformat(),
                                        singleEvents=True, orderBy="startTime", maxResults=15).execute()
    except Exception as exc:  # noqa: BLE001
        raise ToolError(f"Kalender-Abfrage fehlgeschlagen: {type(exc).__name__}: {exc}"[:240]) from exc
    ev = res.get("items", [])
    if not ev:
        return ToolResult(True, f"Keine Termine in den nächsten {days} Tagen.")
    rows = [f"{(e['start'].get('dateTime') or e['start'].get('date'))[:16].replace('T', ' ')}  {e.get('summary', '(ohne Titel)')}" for e in ev]
    return ToolResult(True, wrap_untrusted("\n".join(rows)))


def calendar_create(ctx: ToolContext, a: dict) -> ToolResult:
    from ..memory.when import parse_when
    g = _integ(ctx, "google_calendar"); g.require_ready()
    tz = ctx.cfg.reminders.timezone
    start = parse_when(str(a.get("start", "")), tz=tz)
    end = parse_when(str(a["end"]), tz=tz) if a.get("end") else start + timedelta(hours=1)
    body = {"summary": str(a.get("title", "")), "start": {"dateTime": start.isoformat(), "timeZone": tz},
            "end": {"dateTime": end.isoformat(), "timeZone": tz}}
    try:
        ev = g.service().events().insert(calendarId="primary", body=body).execute()
    except Exception as exc:  # noqa: BLE001
        raise ToolError(f"Termin nicht angelegt: {type(exc).__name__}: {exc}"[:240]) from exc
    return ToolResult(True, f"Termin angelegt: {body['summary']} am {start:%d.%m.%Y %H:%M} (id {ev.get('id')})")


def _assess_calendar_create(ctx: ToolContext, a: dict) -> Assessment:
    return Assessment(CONFIRM, f"KALENDERTERMIN ANLEGEN: „{a.get('title')}“ am {a.get('start')}"
                               + (f" bis {a.get('end')}" if a.get("end") else ""))


def spotify_now(ctx: ToolContext, a: dict) -> ToolResult:
    s = _integ(ctx, "spotify"); s.require_ready()
    r = s.api("GET", "/me/player/currently-playing")
    if r.status_code == 204:
        return ToolResult(True, "Es läuft gerade nichts.")
    if not r.ok:
        raise ToolError(f"Spotify: HTTP {r.status_code}")
    it = r.json().get("item") or {}
    return ToolResult(True, f"{it.get('name')} — {', '.join(x['name'] for x in it.get('artists', []))}")


def spotify_control(ctx: ToolContext, a: dict) -> ToolResult:
    s = _integ(ctx, "spotify"); s.require_ready()
    act = str(a.get("action"))
    if act == "play" and a.get("query"):
        sr = s.api("GET", "/search", params={"q": a["query"], "type": "track", "limit": 1})
        items = (sr.json().get("tracks", {}) if sr.ok else {}).get("items", [])
        if not items:
            raise ToolError(f"Spotify findet nichts zu „{a['query']}“")
        r = s.api("PUT", "/me/player/play", json={"uris": [items[0]["uri"]]})
        what = f"{items[0]['name']} — {items[0]['artists'][0]['name']}"
    else:
        path, method = {"play": ("/me/player/play", "PUT"), "pause": ("/me/player/pause", "PUT"),
                        "next": ("/me/player/next", "POST"), "previous": ("/me/player/previous", "POST")}.get(act, (None, None))
        if not path:
            raise ToolError("action muss play, pause, next oder previous sein")
        r = s.api(method, path); what = act
    if r.status_code in (200, 202, 204):
        return ToolResult(True, f"Spotify: {what}")
    reason = (r.json().get("error", {}) if r.content else {}).get("reason") or r.status_code
    raise ToolError(f"Spotify lehnt ab ({reason}) — Premium-Konto und aktives Wiedergabegerät nötig")


def ha_states(ctx: ToolContext, a: dict) -> ToolResult:
    h = _integ(ctx, "home_assistant"); h.require_ready()
    r = h.api("GET", "/states")
    if not r.ok:
        raise ToolError(f"Home Assistant: HTTP {r.status_code}")
    needle = str(a.get("filter") or "").lower()
    rows = [f"{s['entity_id']} = {s['state']}" for s in r.json()
            if needle in s["entity_id"].lower() or needle in str(s.get("attributes", {}).get("friendly_name", "")).lower()]
    return ToolResult(True, wrap_untrusted("\n".join(rows[:40]) or "Keine passenden Geräte"))


def ha_call(ctx: ToolContext, a: dict) -> ToolResult:
    h = _integ(ctx, "home_assistant"); h.require_ready()
    dom, svc, ent = str(a.get("domain")), str(a.get("service")), str(a.get("entity_id", ""))
    r = h.api("POST", f"/services/{dom}/{svc}", json={"entity_id": ent} if ent else {})
    if not r.ok:
        raise ToolError(f"Home Assistant: HTTP {r.status_code} {r.text[:120]}")
    return ToolResult(True, f"Home Assistant: {dom}.{svc} {ent} ausgeführt")


def _assess_ha(ctx: ToolContext, a: dict) -> Assessment:
    if str(a.get("domain")) in HA_CONFIRM_DOMAINS or ctx.tainted:
        return Assessment(CONFIRM, f"SMART HOME: {a.get('domain')}.{a.get('service')} {a.get('entity_id', '')}")
    return Assessment(ALLOW)


def telegram_send(ctx: ToolContext, a: dict) -> ToolResult:
    t = _integ(ctx, "telegram"); t.require_ready()
    t.api("sendMessage", chat_id=os.environ["JARVIS_TELEGRAM_CHAT_ID"], text=str(a.get("text", ""))[:4000])
    return ToolResult(True, "Telegram-Nachricht gesendet")


def discord_send(ctx: ToolContext, a: dict) -> ToolResult:
    d = _integ(ctx, "discord"); d.require_ready()
    r = d.api("POST", f"/channels/{os.environ['JARVIS_DISCORD_CHANNEL_ID']}/messages", json={"content": str(a.get("text", ""))[:1900]})
    if not r.ok:
        raise ToolError(f"Discord: HTTP {r.status_code}")
    return ToolResult(True, "Discord-Nachricht gesendet")


def _assess_outbound(ctx: ToolContext, a: dict) -> Assessment:
    if ctx.tainted:
        return Assessment(CONFIRM, f"NACHRICHT SENDEN (nach Lesen fremder Inhalte): {a.get('text', '')[:200]}")
    return Assessment(ALLOW)


def tools() -> list[Tool]:
    S, I = {"type": "string"}, {"type": "integer"}
    return [
        Tool("integration_status", "integrations", "Zeigt, welche Integrationen eingerichtet sind.", {}, (), integration_status),
        Tool("calendar_upcoming", "integrations", "Nächste Termine im Google Kalender.", {"days": I}, (), calendar_upcoming, untrusted=True),
        Tool("calendar_create", "integrations", "Legt einen Kalendertermin an (Bestätigung nötig).",
             {"title": S, "start": S, "end": S}, ("title", "start"), calendar_create, _assess_calendar_create),
        Tool("spotify_now", "integrations", "Was gerade auf Spotify läuft.", {}, (), spotify_now),
        Tool("spotify_control", "integrations", "Spotify steuern: play (optional query), pause, next, previous.",
             {"action": {**S, "enum": ["play", "pause", "next", "previous"]}, "query": S}, ("action",), spotify_control),
        Tool("ha_states", "integrations", "Home-Assistant-Geräte und Zustände (optional gefiltert).", {"filter": S}, (), ha_states, untrusted=True),
        Tool("ha_call", "integrations", "Home-Assistant-Dienst aufrufen, z. B. light.turn_on.",
             {"domain": S, "service": S, "entity_id": S}, ("domain", "service"), ha_call, _assess_ha),
        Tool("telegram_send", "integrations", "Sendet eine Telegram-Nachricht an den eigenen Chat.", {"text": S}, ("text",), telegram_send, _assess_outbound),
        Tool("discord_send", "integrations", "Sendet eine Nachricht in den konfigurierten Discord-Kanal.", {"text": S}, ("text",), discord_send, _assess_outbound),
    ]


if __name__ == "__main__":
    import sys
    from .. import config
    config.load_env_file()
    {"calendar-login": calendar_login, "spotify-login": spotify_login}.get(
        sys.argv[1] if len(sys.argv) > 1 else "", lambda: print("calendar-login | spotify-login"))()
