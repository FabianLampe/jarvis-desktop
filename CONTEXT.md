# J.A.R.V.I.S. — Kontextdokument

Persönlicher, **lokal laufender** KI-Assistent im Iron-Man-Stil. Neuaufbau, inspiriert von
PewDiePies self-hosted Workspace **Odysseus** als Vorbild, aber mit Jarvis-Persönlichkeit,
Sprachsteuerung, grafischer Oberfläche und **aufgabenabhängigem Multi-Modell-Routing**.

---

## Vision (festgelegt)

- **Lokal-first**: läuft auf dem eigenen PC, keine Pflicht-Cloud, keine laufenden Kosten.
- **Multi-Modell-Routing**: je nach Aufgabe ein anderes Modell (Coding vs. Allgemein vs. Research).
- **Jarvis bleibt Jarvis**: deutsche Persönlichkeit, „Sir", trockener Witz (siehe Glossar/Persona).
- **Grafische Oberfläche** + Sprachein-/ausgabe.
- **Odysseus als Ideengeber**, nicht als unveränderte Basis (Fork-vs-Neubau noch offen).

---

## Hardware-Realität (gemessen, 2026-06-05)

Diese Grenzen bestimmen **jede** Architekturentscheidung:

| Komponente | Wert | Konsequenz |
|---|---|---|
| GPU | GTX 1660 Ti, **6 GB VRAM** | genau **zwei 3B** ODER **ein knappes 7B** gleichzeitig |
| CPU | i5-8600K (6 Kerne, 2017) | 7B auf CPU nur ~4–8 Tok/s (zu langsam interaktiv) |
| RAM | 16 GB (oft nur ~7 GB frei) | kein zweites 7B im RAM |
| F: | **NVMe, 1,1 TB frei** | Zielort für Modelle, App, Docker |
| C: | SATA-SSD, **nur 8 GB frei** | meiden |
| D: | HDD | nur kalte Ablage |

Gemessene Modell-Performance: 7B → ~17 Tok/s (mit CPU-Offload) · 3B → **~87 Tok/s (100 % GPU)**.

---

## Hardware-Realität Mac (gemessen, 2026-10-28)

| Komponente | Wert | Konsequenz |
|---|---|---|
| Chip/RAM | Apple M5 Pro, 18 Kerne, **48 GB Unified Memory** | kein VRAM-Limit wie beim Windows-PC; ~14B resident, 35B als Schwerlast |
| OS/Tools | macOS 26.5, Python 3.12 (Homebrew), Node 26, Ollama 0.40 | venv `.venv` mit 3.12; Windows bleibt 3.11 |
| Werkzeug-Modell | `qwen2.5:14b`: mehrstufige Aufgabe ≈ 16 s; `qwen3:8b` ≈ 32 s (Denkmodus) | Profil `mac`: `qwen2.5:14b` für Chat, Coder und Werkzeugagent |
| XTTS | CPU RTF **0,6**, MPS RTF 2,2 (torch 2.8.0) | `xtts_device: cpu` auf dem Mac; Referenz-WAV fehlt hier (`--voice-ref` erzeugt sie aus der mp3) ⇒ Fallback edge → `say`; XTTS-Pakete in `.venv` installiert, Ende-zu-Ende mit Test-Referenz gelaufen |

Die GPU-/Modellannahmen der Windows-Tabelle oben gelten **nur** für das Profil `home`.

## Glossar (Domänensprache)

- **Plattformadapter** — `jarvis_core/platform/`: gemeinsame Schnittstelle, getrennte Adapter für macOS und Windows
  (Programmstart, Benachrichtigung, OCR, Systemstimme, Prozessabbruch, GPU, Datenordner).
- **Werkzeugsteuerung** — Registry + `ToolAgent`: ein begrenzter Mehrschritt-Ablauf (`tools.max_steps`) über alle
  Werkzeuggruppen; pro Anfrage werden nur passende Gruppen (Schlüsselwörter) ans Modell gegeben.
- **Bestätigung** — UI-Karte mit der konkreten Aktion; gebunden an eine ID, einmalig, verfällt nach 120 s.
- **Taint / Fremdinhalt** — Webseiten, Dokumente, Dateien und Befehlsausgaben sind Daten. Wurde etwas davon im Auftrag
  gelesen, werden Befehle, Gedächtnis-/Erinnerungsänderungen und ausgehende Nachrichten bestätigungspflichtig.
- **Arbeitsordner** — `~/Jarvis`; dort schreibt Jarvis ohne Rückfrage.

- **Router** — leichte Instanz, die jede Anfrage klassifiziert und den passenden **Spezialisten** wählt.
  Wählt **automatisch** zwischen den residenten 3B (Coder ↔ Allrounder); die teure 7B-**Schwerlast-Spur**
  nur auf **explizites Signal** (Schlüsselwort/GUI-Schalter), darf sie aber vorschlagen. Latenzfrei.
- **Spezialist** — ein auf eine Aufgabenklasse zugeschnittenes Modell (z.B. Coder-Modell).
- **Resident** — dauerhaft im VRAM geladenes Modell (kein Nachladen → keine Pause).
- **Tausch (Swap)** — Auslagern eines Modells aus dem VRAM, um ein anderes zu laden (kostet Zeit).
- **Schwerlast-Spur** — separater Pfad für Aufgaben, die 3B nicht gut kann (hartes Coding, Deep
  Research): lokales **7B on-demand**, das 3B-Paar wird dafür kurz ausgelagert und danach zurückgeladen.
- **Keine Cloud** — Cloud/API ist **bewusst ausgeschlossen** (Null-Kosten, alles lokal), siehe ADR 0002.
- **Geklonte Film-Stimme (XTTS v2 auf GPU)** — der Sprach-Daemon spricht mit der aus einer
  Referenzaufnahme geklonten Jarvis-Stimme (`tts_engine: xtts`, `xtts_device: cuda`). Gemessen
  2026-06-15 auf der GTX 1660 Ti: XTTS-Synthese **RTF ~0,6** (schneller als Echtzeit), gemma3:4b
  läuft daneben weiter mit **45–54 T/s** — beide passen bei **100% GPU** ins 6-GB-VRAM (XTTS ~2,9 GB
  + gemma ~3,3 GB, knapp). **Caveat:** der allererste Sprachbefehl nach dem Start lädt gemma unter
  VRAM-Druck kalt nach (einmalig bis ~1 min), danach 0,4 s Erstton. **Satz-Streaming:** der erste
  Satz wird gesprochen, während der Rest noch rechnet. Rückweg ohne GPU: `tts_engine: edge` (eine
  Zeile). CPU-Fallback existiert (RTF ~4, vorgelesene Länge dann auf 300 Zeichen begrenzt).
- **Sprach-Daemon** — eigenständiger Python-Prozess am PC: Always-on Wake Word („Hey Jarvis",
  **openWakeWord** auf CPU — ersetzt den alten Whisper-Schnipsel-Vergleich, der an
  nicht-muttersprachlichem Englisch scheiterte), Whisper-STT **fest auf Deutsch**, edge-tts (TTS).
  Spricht mit der Desktop-App über deren **WebSocket-Schnittstelle** (ws://127.0.0.1:8765) —
  entkoppelt von der Oberfläche; Odysseus kann später dieselbe Schnittstelle bedienen.

---

## Entscheidungen

- **Lokal-first**, API nur als schlafender Stecker (Kosten vermeiden).
- **Router entscheidet** das Modell (kleiner/günstiger Klassifikator), Schlüsselwörter als Sicherheitsnetz.
- **Zwei 3B-Spezialisten dauerhaft resident** → sofortiges Routing ohne Tausch-Pause.
  Siehe [ADR 0001](docs/adr/0001-local-model-strategy.md).
  - `qwen2.5:3b` (Allrounder + Router) · `qwen2.5-coder:3b` (Coding) · je 2048 Kontext.
  - **Update 2026-06-12** (Nutzer-Feedback „Jarvis kann keine Wissenschaft"): home-Profil
    nutzt jetzt **ein residentes `gemma3:4b` für Allrounder- und Coder-Spur**. Gemessen:
    qwen2.5:3b antwortete physikalisch falsch; qwen2.5:7b fällt mit laufender GUI auf
    2–5 T/s (Orb/Desktop belegt VRAM); gemma3:4b antwortet korrekt (Rayleigh-Test) bei
    **50 T/s, 0,3 s Erstton**. Zwei Modelle (4B+3B) passen neben der GUI nicht mehr
    gleichzeitig ins 6-GB-VRAM → eine Residenz statt Paar; das Residenz-Prinzip aus
    ADR 0001 bleibt, hartes Coding läuft wie gehabt über die Schwerlast-Spur.
- **Schwerlast-Spur = lokales 7B on-demand**, **keine Cloud**. Siehe [ADR 0002](docs/adr/0002-heavy-lane-local-7b-no-cloud.md).
- **Basis = Fork von Odysseus** (MIT), **nativ unter Python 3.11**, kein Docker, Zielort F:.
  Siehe [ADR 0003](docs/adr/0003-fork-odysseus-native-python311.md). Odysseus liefert Agent, Deep
  Research, Memory (ChromaDB), E-Mail (IMAP/SMTP), Kalender (CalDAV), Notizen/Cron, Vision/PDF, MCP.
  **Wir ergänzen:** Modell-Routing, Jarvis-Persona, Sprache (Wake Word/STT/TTS), Spezial-Integrationen, Branding.
- **Sprache V1 = nativer Sprach-Daemon am PC** (kein Browser-Mic), spricht über die Odysseus-API.
  Wake-Word-Whisper läuft auf CPU → keine Konkurrenz zu den GPU-Modellen.
- **Router-Steuerung**: 3B-Paar automatisch (gratis/sofort), 7B-Schwerlast nur auf Ansage
  (Schlüsselwort/GUI), Jarvis darf Eskalation vorschlagen. Mechanik: Schlüsselwort-Schnellpfad +
  residentes 3B (bzw. `fastembed`-Embedding) als Klassifikator für mehrdeutige Fälle.
- **Persona = voll, überall** (Deutsch, „Sir", Witz) — als globaler System-Prompt vor *jedem* Modell
  (beide 3B + 7B). Leitplanke: Code in Code-Blöcken bleibt korrekt; Persona lebt in Prosa/Kommentaren,
  nie auf Kosten der Syntax.
- **GUI V1 = native Desktop-App „Orb-Front"** (ersetzt den zunächst geplanten Odysseus-Reskin,
  Entscheidung 2026-06-11): Age-of-Ultron-Orb schwebt always-on-top, Klick öffnet Chat-Panel im
  Film-HUD-Stil; eigener `jarvis_core` (Router + Persona + Ollama) als Backend, Odysseus dockt
  später an. Details: [Spec](docs/superpowers/specs/2026-06-11-jarvis-desktop-gui-design.md).
  **Umgesetzt 2026-06-11** in diesem Repo (`jarvis_app/` + `jarvis_core/`, Start:
  `start_jarvis.bat`); Orb-Engine auf Nutzerwunsch Richtung Filmstill erweitert
  (Hologramm-Stadt-Paneele, Schweif-Bögen — siehe Spec-Nachtrag).

---

## Fähigkeits-Ziele (was Jarvis können soll — noch zu priorisieren)

Aus dem alten Jarvis + Odysseus. **Noch nicht entschieden**, welche in V1 kommen.

**Kern:** Chat mit Persönlichkeit · Sprachein-/ausgabe (Wake Word, STT, TTS) · Dateioperationen ·
Shell-/Code-Ausführung · Notizen · Erinnerungen · System-Monitor/-Steuerung.

**Wissen & Web:** Websuche · Browser/YouTube · Deep Research (mehrstufig) · RAG-Wissensbasis ·
PDF lesen/zusammenfassen · Wolfram · DeepL.

**Produktivität:** Google Calendar · Gmail/E-Mail-Triage · Dokumenten-Editor (aus Odysseus).

**Medien/Geräte:** Spotify · Steam · Home Assistant · WhatsApp/Telegram/Discord.

**Wahrnehmung:** Vision (Screen-OCR, Webcam) · Gesichtserkennung.

**Proaktiv:** Morning Briefing · System-Alerts · Cron-/Reminder-Tasks.

**Aus Odysseus zusätzlich:** Modell-„Cookbook" (Hardware-Scan + Modellempfehlung) ·
Blind-Modellvergleich · persistenter Memory/Skills-Store · 2FA · PWA-Mobile.

### V1-Umfang (festgelegt)

- **Odysseus-Kern** (kommt mit dem Fork): Chat, Agent, Deep Research, Memory, E-Mail, Kalender,
  Notizen/Cron, Vision/PDF, MCP.
- **+ Jarvis-Eigenbau**: Modell-Routing · Persona · Sprach-Daemon.
- **+ V1-Integrationen**: **PC-Steuerung & Monitoring** (Lautstärke, Screenshot, App öffnen,
  Zwischenablage, System-Monitor + proaktive Alerts) · **Extra-Tools & Wahrnehmung** (Wolfram, DeepL,
  Portfolio, Gesichtserkennung — `dlib`-Installrisiko auf Windows).
- **Später via MCP**: Spotify · Steam · Home Assistant · WhatsApp/Telegram/Discord.

---

## Status

**Update 2026-10-30 (Werkzeuge):** 82 registrierte Funktionen, einschließlich Browser-Snapshot,
Git/GitHub, strukturierten Tests/Dateiprüfungen, SSH/Pi-Diagnose, Datenanalyse/Diagrammen,
PDF/Word/Excel-Ausgabe, optionaler Mac-AX-Steuerung, gespeicherten Workflows und allgemeinem
stdio-MCP-Adapter. Einrichtung und Grenzen: [Werkzeug-Erweiterungen](docs/werkzeug-erweiterungen.md).
Neue Remote-/Mac-Adapter benötigen eigene Konfiguration; Workflows bleiben im normalen Policy-/Abbruchpfad.

**Update 2026-10-30:** [ADR 0005](docs/adr/0005-automatic-reasoning.md) erweitert die frühere
„Schwerlast nur auf Ansage“-Regel für Profile mit `reasoning:`. Im Mac-Profil darf Jarvis
`qwen3.6:35b` nun automatisch für komplexe Aufgaben / nach Werkzeugfehlern wählen.
`reasoning.mode: auto|on|off` steuert dies; Home/Dev bleiben bei ihrer bisherigen Modellauswahl.
Ollama-Thinking, begrenzte Generierung und interner Thinking-Kontext in Tool-Aufträgen sind angebunden.

**Alle Grundsatzfragen geklärt** (Modell-Strategie, Schwerlast-Spur, Fork/nativ, Sprache,
Router-Steuerung, Feature-Umfang, Persona, GUI). Architektur in ADR 0001–0003 + obigen Entscheidungen.

**Umgesetzt (2026-06-11):** Native Desktop-GUI mit Orb-Front + Chat-Panel inkl.
`jarvis_core` (Router mit Schlüsselwort-Schnellpfad, Persona, Ollama-Streaming mit
7B-Swap-Logik, System-Monitor, Profile dev/home), WebSocket-Schnittstelle für den
Sprach-Daemon (ws://127.0.0.1:8765), 26 pytest-Tests grün. Siehe README.md.

**Umgesetzt (2026-06-11, abends): Sprach-Daemon** (`voice_daemon/`, `start_daemon.bat`):
„Hey Jarvis“ via openWakeWord (akzent-robust, CPU) → Aufnahme bis Stille →
faster-whisper **small, language=de** (CPU int8, keine VRAM-Konkurrenz) → Text läuft
über die WS-Schnittstelle durch denselben Router/Verlauf wie das Panel → Antwort wird
mit edge-tts (de-DE-ConradNeural) vorgelesen, **Live-Amplituden treiben den Orb-Kern**.
Push-to-talk über den Mic-Knopf im Panel. Selbsttest: `python -m voice_daemon.selfcheck`
(deutscher TTS→STT-Roundtrip wortgenau bestanden, Wake-Score 0.99).
Damit ist Schritt 5 der Umsetzungs-Reihenfolge erledigt; der alte Englisch-Ansatz
(Whisper-Wake-Word, EN-Sprache) ist Geschichte — Jarvis hört und spricht Deutsch.

**Umgesetzt (2026-06-16): Tagesbriefing V1 (nur lesen)** (`jarvis_core/briefing/`,
`voice_daemon/clap.py`): Doppelklatsch (Energie-Transienten am Mic-Stream) / Sprachbefehl
„Briefing" / Panel-Knopf → gesprochenes Briefing + Dashboard im Panel: offene GitHub-Issues
(REST+PAT), neue Gmails (OAuth `gmail.readonly`, `.secrets/`), Wetter (Open-Meteo),
Datum/Uhrzeit + Systemstatus. Quellen parallel + fehlertolerant; Sprech-Skript deterministisch
(nicht vom 3B). Rein lesend — **Mail beantworten/senden = V2**. Spec/Plan:
`docs/superpowers/specs/2026-06-16-jarvis-tagesbriefing-design.md` (+ plans/).

**Umgesetzt (2026-06-15): Browser-Steuerung V1** (`jarvis_core/agent.py`,
`jarvis_core/browser_intent.py`, `jarvis_core/tools/`): Jarvis bedient Chrome per
Sprach-/Textbefehl über den **chrome-devtools-mcp**-Server (Node, zur Laufzeit als
stdio-MCP-Subprozess, Python-`mcp`-SDK als Client). **Eigenes dauerhaftes
Jarvis-Chrome-Profil** (`.chrome-jarvis`) — nie der echte Browser. Umfang:
öffnen/suchen/navigieren/vorlesen/klicken/tippen. Auslösung: Schlüsselwort-
Schnellpfad (sofort) + Ollama-Tool-Calling (qwen2.5:3b, `max_steps`). Jede Aktion
wird angesagt; Antwort läuft über den bestehenden Caption/TTS-Pfad. Spec/Plan:
`docs/superpowers/specs/2026-06-15-jarvis-browser-control-design.md`,
`docs/superpowers/plans/2026-06-15-jarvis-browser-control.md`. Integrationstest
gegen echten Server grün (Chrome headless: öffnen+lesen+schließen). Bewusst
nicht in V1: voll-autonome Mehrschritt-Aufgaben, echter Chrome, Domain-Allowlist.

**Umgesetzt (2026-10-28): macOS-Linie** ([ADR 0004](docs/adr/0004-macos-und-werkzeugsteuerung.md)):
Plattformadapter (mac/windows), Profil `mac` (`profile: auto`), `start_jarvis_mac.sh`, `jarvis_core.doctor`,
Werkzeugsteuerung (Terminal ohne Shell, Dateien, Systeminfo, App-Start, Prozesse) mit Bestätigungen in der UI und Stopp-Knopf,
Gedächtnis (SQLite/FTS5), Dokumentensuche (MarkItDown → FastEmbed `paraphrase-multilingual-mpnet-base-v2` → Chroma, mit
Datei + Seite/Folie + Überschrift + Zeilen), Erinnerungen (APScheduler 3.x, SQLite-JobStore, Europe/Berlin, verpasste
Termine), Recherche mit Quellen (DuckDuckGo/SearXNG + Trafilatura, SSRF-Schutz), SymPy im isolierten Prozess,
Bildschirm-OCR (Apple Vision), Integrationen Google Calendar / Spotify / Home Assistant / Telegram / Discord mit
Einrichtungsstatus. `config.yaml` repariert (fehlendes `home:`-Profil, doppelte Schlüssel; Loader lehnt Duplikate ab).
`BrowserController` an chrome-devtools-mcp (neue `pageId`-Pflicht) und mcp-SDK 2.x (`is_error`) angepasst.
Sprachausgabe: `tts_engine: voicebox` (lokaler Voicebox-Server, `POST /generate/stream`, Kette voicebox → edge → `say`), nur gegen einen Stand-in-Server getestet.
Offen/nicht gebaut: Gesichtserkennung (bewusst), WhatsApp (bewertet, abgelehnt), Discord-Empfang, Offline-Übersetzung
ungetestet. Odysseus bleibt Referenz (AGPL-Hinweis, ADR 0004) — ADR 0003 gilt für diese Linie nicht mehr.

## Umsetzungs-Reihenfolge (V1)

1. **Fork klonen** nach F:, venv `py -3.11`, `pip install -r requirements.txt` (+ optional Whisper),
   `python setup.py`, `uvicorn app:app` → prüfen, dass Odysseus nativ läuft.
2. **Ollama auf F:** umziehen (`OLLAMA_MODELS`), 3B-Paar + 7B bereitstellen
   (Defaults gesetzt: `OLLAMA_MAX_LOADED_MODELS=2`, `OLLAMA_KEEP_ALIVE=30m`).
3. **Modell-Router** einbauen (Auto-3B Coder↔Allrounder; 7B-Schwerlast on-demand mit Evict/Reload)
   in Odysseus' `src/llm_core` + `chat_processor`.
4. **Persona** als globalen System-Prompt/Preset (Deutsch, „Sir", voll).
5. **Sprach-Daemon**: erledigt — openWakeWord + faster-whisper (de) + edge-tts,
   angebunden über die WS-Schnittstelle der Desktop-App (statt Odysseus-API).
6. **V1-Integrationen**: PC-Steuerung/Monitoring + Extra-Tools/Wahrnehmung (Wolfram, DeepL,
   Portfolio, Gesichtserkennung).
7. **GUI**: erledigt — native Desktop-App in diesem Repo (`jarvis_app/`), Orb-Engine
   Richtung Filmstill ausgebaut. Siehe
   [Spec 2026-06-11](docs/superpowers/specs/2026-06-11-jarvis-desktop-gui-design.md) inkl. Nachtrag; kein Reskin mehr nötig.
