# J.A.R.V.I.S. — Desktop-App (Orb-Front)

Native Desktop-App im Stil der J.A.R.V.I.S.-Visualisierung aus *Avengers: Age of Ultron*:
Ein animierter Orb schwebt always-on-top auf dem Desktop, Klick öffnet das Chat-Panel im
Film-HUD-Stil. Backend ist der eigene `jarvis_core` (Modell-Routing + Persona) direkt
gegen ein lokales Ollama — keine Cloud (ADR 0002).

Design-Spec: [docs/superpowers/specs/2026-06-11-jarvis-desktop-gui-design.md](docs/superpowers/specs/2026-06-11-jarvis-desktop-gui-design.md)

## Voraussetzungen

- Windows 10/11 mit **WebView2-Runtime** (auf Windows 11 vorinstalliert; sonst meldet
  sich die App mit einer Installationsanleitung)
- **Python 3.11** (nativ, kein Docker — ADR 0003)
- **Ollama** unter `http://127.0.0.1:11434` mit den Modellen des Profils:
  - Profil `home`: `ollama pull gemma3:4b` · `ollama pull qwen2.5:7b`
  - Profil `dev`: `ollama pull qwen3.5:0.8b`

## Einrichtung

```bat
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Starten

```bat
start_jarvis.bat                 :: nutzt das Profil aus config.yaml (home)
start_jarvis.bat --profile dev   :: Arbeits-PC-Profil (kein 7B, kein nvidia-smi)
start_jarvis.bat --debug         :: WebView-Devtools
```

## Bedienung

| Aktion | Wirkung |
|---|---|
| **Klick** auf den Orb | Chat-Panel auf/zu (öffnet rechts, bei Platzmangel links) |
| **Ziehen** des Orbs | Fenster frei positionieren (wird gespeichert) |
| **Mausrad** über dem Orb | Orb stufenlos skalieren (wird gespeichert) |
| **Rechtsklick** auf den Orb | Menü: Panel, Verlauf leeren, Beenden |
| **7B-Knopf** | Schwerlast-Spur für die nächste Anfrage erzwingen |
| Schlüsselwort „Schwerlast“ / „tiefe Recherche“ | ebenfalls Schwerlast-Spur |
| **●-Knopf** (Mikrofon) | Push-to-talk — aktiv, sobald der Sprach-Daemon verbunden ist |

Der Orb zeigt, was Jarvis tut: **Leerlauf** (gedimmt) · **Hört zu** (heller Kern,
Halo-Puls) · **Denkt** (schneller, mehr Datenflüsse) · **Spricht** (Kern pulst im
Sprechrhythmus; mit `amplitude` vom Sprach-Daemon zur echten Stimme).

## Sprache („Hey Jarvis“)

```bat
.venv\Scripts\python.exe -m pip install -r requirements-voice.txt   :: einmalig
.venv\Scripts\python.exe -m voice_daemon.selfcheck                  :: Selbsttest
start_daemon.bat                                                    :: Daemon starten
```

Der Daemon läuft neben der App: **„Hey Jarvis“** sagen (openWakeWord, akzent-robust),
kurzer Bestätigungston, auf Deutsch sprechen — faster-whisper (`language=de`, CPU)
transkribiert, die Frage erscheint im Panel, die Antwort kommt als deutsche Stimme
(`de-DE-ConradNeural`) und der Orb pulst zur echten Sprachausgabe. Der **●-Knopf**
im Panel ist Push-to-talk, sobald der Daemon verbunden ist.

**Gesprächsmodus:** Nach jeder Antwort hört Jarvis `follow_up_s` Sekunden weiter
(Standard 6 s, Bestätigungston) — einfach weitersprechen, ohne neues „Hey Jarvis“.
Wer schweigt, beendet das Gespräch.

**Unterbrechen:**

| Situation | Sagen | Wirkung |
|---|---|---|
| Jarvis liest gerade vor | **„Hey Jarvis“** (reinrufen) | Ausgabe stoppt sofort, er hört zu |
| Anfrage zurückziehen | „**Stop**“ · „vergiss es“ · „das war so nicht gemeint“ | Antwort wird verworfen, er hört direkt weiter |
| Gespräch beenden | „**hör auf zuzuhören**“ · „das war alles“ · „schlaf weiter“ | „Sehr wohl, Sir.“ — zurück zum Wake Word |

Einstellungen (Mikrofon, Modell, Stimme, Empfindlichkeit, Gesprächsfenster,
Barge-in-Schwelle) in `config.yaml` unter `voice:`.

**Geklonte Film-Stimme (`tts_engine: xtts`):** Aus einer lokalen Referenzaufnahme
(`voice_daemon/assets/jarvis_reference.wav`, ~30 s saubere Sprache — bleibt aus
Urheberrechtsgründen lokal und kommt nie ins Git) klont XTTS v2 die Stimme und
spricht damit Deutsch. Läuft komplett lokal auf der CPU und ist deshalb **deutlich
langsamer** (~4× Echtzeit: eine Zwei-Satz-Antwort braucht ~30 s Rechenzeit, das
Modell lädt beim Daemon-Start ~30 s; vorgelesen werden max. 300 Zeichen, der Rest
steht im Panel). Installation siehe `requirements-voice.txt` (coqui-tts +
torch 2.8 CPU). Für schnelle Antworten zurück zu `tts_engine: edge`.

## Browser-Steuerung

Jarvis kann per Sprach- oder Textbefehl Chrome bedienen. Voraussetzung: **Node.js**
(für `npx`) und **Chrome**. Beim ersten Browser-Befehl startet Jarvis den
`chrome-devtools-mcp`-Server und öffnet ein **eigenes, dauerhaftes Jarvis-Profil**
(`.chrome-jarvis`) — dein normaler, eingeloggter Chrome bleibt unangetastet. Du
kannst dich im Jarvis-Chrome gezielt bei Diensten anmelden; das bleibt erhalten.

Beispiele: „**Öffne YouTube**“ · „**such nach** Wetter in Hamburg“ · „**suche auf
YouTube nach** Iron Man Trailer“ · „**geh zu** github.com“ · „**lies die Seite
vor**“ · „**scroll runter**“ · „**geh zurück**“. Jarvis sagt jede Aktion an
(„Ich öffne YouTube, Sir.“). Klare Befehle erkennt ein Schlüsselwort-Schnellpfad
sofort; alles andere entscheidet das Modell per Tool-Calling (max. `max_steps`
Schritte). Einstellungen in `config.yaml` unter `browser:` (`enabled`,
`profile_dir`, `headless`, `max_steps`, `search_engine`). Installation des
MCP-Clients: `mcp` aus `requirements-voice.txt`.

## Tagesbriefing

**Zweimal klatschen** (oder „Hey Jarvis, **Briefing**" sagen, oder den **▦-Knopf** im
Panel) → Jarvis liest dir vor, was ansteht, und zeigt zugleich ein **Dashboard**:
offene **GitHub-Issues** (`FabianLampe/jarvis-desktop`), neue **Gmails** (Anzahl, Absender,
Betreff), **Wetter** heute, **Datum/Uhrzeit + Systemstatus**. V1 ist **rein lesend** —
Mails beantworten/senden kommt später.

Einmaliges Setup für echte Daten (ohne läuft alles, zeigt nur „nicht verfügbar"):
- **GitHub:** read-only Token als `JARVIS_GITHUB_TOKEN` in `.env` (sonst nur öffentliche Issues).
- **Gmail (nur lesen):** OAuth-Client (Google Cloud → Desktop-App) als
  `.secrets/credentials.json` ablegen, dann `python -m jarvis_core.briefing.gmail --login`
  (öffnet einmal den Browser, schreibt `.secrets/token.json`, Scope `gmail.readonly`).
- **Wetter:** deinen Ort in `config.yaml` unter `briefing.weather` setzen.

Klatsch-Empfindlichkeit in `config.yaml` unter `voice.clap_*` (oder `clap_enabled: false`).

## Architektur

```
jarvis_app/    pywebview-Front: main.py (Fenster) · bridge.py (js_api) · ws_server.py (Daemon-Schnittstelle)
  ui/          index.html · css/app.css · js/orb-engine.js · js/app.js · fonts/ (lokal gebündelt)
jarvis_core/   UI-unabhängig: config · persona · router · ollama_client · chat · sysmon
               + browser_intent · agent (Browser-Tool-Loop) · tools/ (browser MCP-Client, schemas)
               + briefing/ (github · gmail · weather · aggregator — Tagesbriefing)
voice_daemon/  ears (Wake Word + Aufnahme) · stt (Whisper de) · mouth (edge/xtts) · link (WS) · selfcheck
config.yaml    Profile dev/home + Orb-Parameter (Seed 5) + voice- + browser-Sektion
tests/         pytest: Router, Ollama-Client, ChatService, Bridge, WS, Browser-Intent/Schemas/Agent
```

WS-Protokoll (`ws://127.0.0.1:8765`):
Daemon → App `{"type":"state","value":"listening","amplitude":0.7}` ·
`{"type":"utterance","text":"…"}` — App → Daemon `{"type":"push_to_talk"}` ·
`{"type":"speak","text":"…"}`.

## Entwicklung

```bat
.venv\Scripts\python.exe -m pytest tests\ -q       :: Tests
```

UI ohne App im Browser begutachten: `jarvis_app/ui/preview.html` öffnen
(gemockte Bridge; Parameter `?open=1`, `?state=think`, `?zone=500`).
Headless-Screenshot (Sichtprüfung wie im Brainstorming etabliert):

```bat
msedge --headless --screenshot=artifacts\ui.png --window-size=760,660 ^
  --virtual-time-budget=5000 "file:///<pfad-zum-repo>/jarvis_app/ui/preview.html?open=1"
```

## Hinweis

Der Ordner `codex/` gehört nicht zur App (Altlast eines Werkzeug-Experiments) und wird
von Code und Doku ignoriert.
