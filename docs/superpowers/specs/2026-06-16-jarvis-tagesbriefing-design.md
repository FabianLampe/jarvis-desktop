# J.A.R.V.I.S. Tagesbriefing — Design (V1)

**Datum:** 2026-06-16 · **Status:** vom Nutzer abgesegnet (Brainstorming)

## Zusammenfassung

Ein **Doppelklatsch** (oder Sprachbefehl „Briefing" / ein Panel-Knopf) löst ein
**Tagesbriefing** aus: Jarvis sagt gesprochen, was ansteht, und zeigt es zugleich als
**Dashboard** im Panel. Inhalt: offene **GitHub-Issues** (`FabianLampe/jarvis-desktop`),
neue **Gmail**-Nachrichten (Anzahl, Absender, Betreff, Kurztext), **Wetter** heute und
**Datum/Uhrzeit + Systemstatus**. V1 ist **rein lesend** — Mails beantworten/senden ist
ausdrücklich eine spätere V2.

## Abgesegnete Entscheidungen

| Frage | Entscheidung |
|---|---|
| V1-Zuschnitt | **Briefing nur lesen** (GitHub + Gmail + Wetter + Datum/Sysmon). Mail-Antwort/Senden = V2 |
| Gmail-Zugang | **Gmail-API + OAuth**, Scope `gmail.readonly` (widerrufbar, scoped) |
| GitHub-Zugang | **REST-API + read-only PAT** (kein `gh` CLI installiert), offene Issues in `FabianLampe/jarvis-desktop` |
| Sonstiges | **Wetter heute** + **Datum/Uhrzeit + Systemstatus** (KEIN Kalender in V1) |
| Auslöser | **Doppelklatsch (Energie-Transienten)** + Sprachbefehl „Briefing" + Panel-Knopf |

## Architektur

```
voice_daemon ── Doppelklatsch (ClapDetector am Mic-Callback) ──┐
                Sprachbefehl „Briefing" (commands)            │ WS {"type":"briefing"}
jarvis_app/panel ── Briefing-Knopf ───────────────────────────┤
                                                               ▼
jarvis_app/bridge ── request_briefing() ──▶ jarvis_core/briefing/aggregator.py
                                              ├─ github.py   (REST, PAT)
                                              ├─ gmail.py    (Gmail API, OAuth readonly)
                                              ├─ weather.py  (Open-Meteo, kein Key)
                                              └─ sysmon       (vorhanden)
                                                   │
                          ┌────────────────────────┴───────────────────────┐
                          ▼ Dashboard-JSON                                  ▼ Sprech-Skript (de)
              jarvis.onBriefing(data) → Panel-Karten          hub.say(satzweise) → XTTS, Captions
```

### Auslöser

**`voice_daemon/clap.py` — ClapDetector:** hängt sich an den vorhandenen Mic-Callback
(`ears._audio_cb` liefert bereits 16-kHz-Frames). Ein Klatsch ist ein kurzer, lauter
Breitband-Impuls: Frame-RMS überschreitet plötzlich ein Vielfaches des gleitenden
Grundpegels mit schnellem Anstieg **und** schnellem Abfall (kein Dauerton). Zwei solche
Impulse im Abstand **0,15–0,8 s** (nicht mehr als zwei) → Auslöser. Danach Cooldown
(~3 s). Während der TTS-Ausgabe unterdrückt (wie Wake Word). Parameter konfigurierbar
(`voice.clap_*`). Bei Erkennung ruft `Ears` einen `on_clap`-Callback → `Daemon` sendet
`link.send_briefing()` → WS `{"type":"briefing"}`.

**Sprach-Fallback:** `voice_daemon/commands.py` erkennt „briefing" / „tagesbriefing" /
„was steht heute an" als eigenen Befehl → ebenfalls `{"type":"briefing"}` (statt an die
App-Antwort zu gehen).

**Panel-Knopf:** kleiner Knopf in der Panel-Kopfzeile → `api.request_briefing()`.

### Briefing-Aggregator (`jarvis_core/briefing/`)

- **`github.py`** — `open_issues(repo, token) -> list[Issue]`. `GET /repos/{repo}/issues?state=open&per_page=20`, Pull-Requests herausfiltern (Feld `pull_request`). `Issue = {number, title, labels, updated_at}`. Token aus Umgebung (`JARVIS_GITHUB_TOKEN`, via `.env`). Ohne Token: nur öffentliche Repos, niedrigeres Rate-Limit — sauber gemeldet.
- **`gmail.py`** — `recent_unread(max=10) -> list[Mail]` über `google-api-python-client`. OAuth-Flow einmalig (`InstalledAppFlow`, lokaler Redirect), Scope `https://www.googleapis.com/auth/gmail.readonly`. `credentials.json` (OAuth-Client) und `token.json` liegen lokal unter `.secrets/` (gitignored). `Mail = {sender, subject, snippet, date}` (Body bewusst nicht — V1 liest Betreff + Snippet).
- **`weather.py`** — `today(lat, lon) -> Weather` über Open-Meteo (`https://api.open-meteo.com/v1/forecast`, kein Key). `Weather = {temp_now, temp_max, temp_min, code_text}`. Ort aus Konfig (`briefing.lat/lon/ort`).
- **`sysmon`** — vorhandener `SystemMonitor` (CPU/RAM/VRAM).
- **`aggregator.py`** — `BriefingService.collect() -> BriefingData` ruft die Quellen parallel (Thread-Pool, je Quelle Timeout + Fehlertoleranz: eine kaputte Quelle bricht das Briefing nicht ab, sondern erscheint als „nicht verfügbar"). `BriefingData` ist JSON-serialisierbar (fürs Dashboard) und Quelle für das Sprech-Skript.
  - **`script(data) -> list[str]`** erzeugt das gesprochene Briefing als Satzliste,
    **deterministisch getextet** (Persona-Ton, nicht vom 3B generiert — das halluziniert):
    Begrüßung + Datum/Uhrzeit → Wetter → „Sie haben N neue Nachrichten, Sir" + bis zu 3
    Betreffzeilen → „X offene Punkte im Repository" + Titel → kurzer Systemstatus →
    „Möchten Sie zu etwas mehr hören, Sir?" (Antworten = V2).

### Ausgabe

- **`jarvis_app/bridge.py`** — `request_briefing()` (js_api **und** vom WS-Trigger
  aufgerufen) startet einen Worker: `BriefingService.collect()` → `jarvis.onBriefing(data)`
  ans Panel **und** `script()` satzweise über `hub.say(...)` (bei verbundenem Daemon →
  XTTS-Stimme + Captions; ohne Daemon → Text ins Panel). Orb: „denkt" beim Sammeln,
  „spricht" beim Vortrag.
- **`jarvis_app/ui` (Dashboard)** — neue Panel-Ansicht: Karten im „Film pur"-Stil
  (Datum/Uhrzeit, Wetter, Gmail-Liste, GitHub-Issues, System). `jarvis.onBriefing(data)`
  rendert die Karten; Klick/Chat schaltet zurück. Reines Anzeigen, keine Aktionen in V1.

### Konfiguration (`config.yaml`)

```yaml
briefing:
  github_repo: "FabianLampe/jarvis-desktop"
  github_issue_limit: 20
  gmail_enabled: true
  gmail_max: 10
  weather: { lat: 53.55, lon: 9.99, ort: "Hamburg" }   # Beispiel
voice:
  clap_enabled: true
  clap_threshold: 4.0        # RMS-Vielfaches über Grundpegel
  clap_gap_min_s: 0.15
  clap_gap_max_s: 0.8
  clap_cooldown_s: 3.0
```

Secrets liegen **nicht** in `config.yaml`, sondern in `.env`
(`JARVIS_GITHUB_TOKEN=…`) bzw. `.secrets/credentials.json` + `.secrets/token.json`.

## Sicherheit

- **Rein lesend in V1** — es wird nichts gesendet, nichts verändert.
- Gmail: Scope `gmail.readonly`, jederzeit im Google-Konto widerrufbar; OAuth-Client und
  Token rein lokal (`.secrets/`, gitignored).
- GitHub: read-only PAT (klassisch: Scope `repo`/`public_repo`; fein: nur Issues lesen),
  in `.env` (gitignored).
- Kein Mail-Body, keine Anhänge in V1 (nur Absender/Betreff/Snippet) — minimiert
  versehentliche Preisgabe beim Vorlesen.

## Fehlerbehandlung

- **Quelle nicht verfügbar** (kein Token/Netz/Timeout) → die Karte zeigt „nicht
  verfügbar", das Skript sagt es knapp („Zu GitHub erreiche ich gerade nichts, Sir").
  Andere Quellen laufen normal weiter.
- **Gmail nicht eingerichtet** (kein `token.json`) → Dashboard/Skript weisen einmal auf
  das einmalige Setup hin (`python -m jarvis_core.briefing.gmail --login`).
- **Klatsch-Fehlauslöser** → Cooldown + Energie-/Timing-Kriterien; Sprach-/Knopf-Fallback
  bleibt immer verfügbar.

## Tests

- **ClapDetector** (Unit): synthetische Frames — zwei Transienten im Fenster → Trigger;
  ein Klatsch → kein Trigger; Dauer-Lautstärke (Sprache/Musik) → kein Trigger; Cooldown.
- **github/gmail/weather** (Unit): gegen gemockte HTTP-Antworten — Parsing, PR-Filter,
  Fehler-/Timeout-Pfad, fehlendes Token.
- **aggregator.script** (Unit): aus Mock-`BriefingData` entsteht eine sinnvolle Satzliste;
  kaputte Quelle bricht nicht ab.
- **Dashboard** (Optik): Headless-Screenshot mit Beispiel-`BriefingData`.
- **Bridge** (Smoke): `request_briefing()` → onBriefing-Aufruf + hub.say-Sätze.

## Umsetzung in Strängen

1. **Strang 1 — Auslöser:** `clap.py` + Ears-Anbindung (`on_clap`), `commands`-Befehl,
   `link.send_briefing`, WS-`briefing`-Typ, Panel-Knopf. *Weitgehend unabhängig.*
2. **Strang 2 — Datenquellen + Aggregator:** `briefing/{github,gmail,weather,aggregator}.py`
   + Konfig + Secrets-Handling + Unit-Tests. *Unabhängig.*
3. **Strang 3 — Ausgabe:** Bridge `request_briefing`, `jarvis.onBriefing`, Dashboard-UI,
   Sprech-Anbindung. *Baut auf 1 + 2 auf.*

## Bewusst NICHT in V1 (YAGNI / V2)

- Mails **beantworten/senden** (interaktiver Diktat→Senden-Flow) — sicherheitskritisch,
  eigene sorgfältige V2.
- Google **Kalender**, Mail-**Body**/Anhänge, GitHub-Issues anderer Repos, proaktives
  (zeitgesteuertes) Morgen-Briefing.

## Offene Punkte

- **Wetter-Ort**: Default in `config.yaml` (Beispiel Hamburg) — vom Nutzer beim Setup zu
  setzen; alternativ später IP-Geolokalisierung.
- **Gmail-OAuth-Client**: der Nutzer legt einmalig ein Google-Cloud-OAuth-Client-Secret an
  (Desktop-App) und legt es als `.secrets/credentials.json` ab; Schritt in der Doku.
