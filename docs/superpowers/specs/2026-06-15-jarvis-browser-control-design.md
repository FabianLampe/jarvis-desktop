# J.A.R.V.I.S. Browser-Steuerung — Design (V1)

**Datum:** 2026-06-15 · **Status:** vom Nutzer abgesegnet (Brainstorming)

## Zusammenfassung

Jarvis bekommt die Fähigkeit, per Sprach- oder Textbefehl einen **Chrome-Browser
zu bedienen**: Seiten öffnen, suchen, navigieren, Seiteninhalt vorlesen sowie auf
Elemente klicken und in Felder tippen. Die Steuerung läuft über den vom Nutzer
installierten **`chrome-devtools-mcp`-Server** (Node), den Jarvis zur Laufzeit als
Subprozess startet und als MCP-Client anspricht. Chrome läuft mit einem **eigenen,
dauerhaften Jarvis-Profil** — der echte, eingeloggte Browser des Nutzers bleibt
unangetastet.

## Abgesegnete Entscheidungen

| Frage | Entscheidung |
|---|---|
| Welcher Chrome | **Eigenes, dauerhaftes Jarvis-Profil** (`--user-data-dir`), nie der echte Browser |
| Umfang | **Navigieren + Lesen + Klicken/Tippen** (kein voll-autonomer Mehrschritt-Agent) |
| Steuer-Architektur | **Jarvis startet `chrome-devtools-mcp` zur Laufzeit** und spricht ihn per MCP (stdio) an |
| Tool-Auslösung | **Schlüsselwort-Schnellpfad + Ollama-Tool-Calling** (Ansatz B) |
| Autonomie | **Sofort handeln + ansagen** (keine Rückfrage vor Klick/Tippen) |

## Voraussetzungen (gemessen 2026-06-15)

- **Node.js v24** + **npx 11.x** vorhanden → `chrome-devtools-mcp` startbar.
- Plugin-Paket: `chrome-devtools-mcp@1.2.0`, stdio-Transport, npm-Identifier
  `chrome-devtools-mcp`. Start z. B. `npx chrome-devtools-mcp@latest <flags>`.
- Chrome (stable) muss installiert sein; der MCP-Server steuert es.
- **Python-MCP-Client**: das offizielle `mcp`-SDK (stdio-Client). In der Build-Phase
  zu verifizieren; Fallback falls nötig: direkter JSON-RPC-Client über stdio.

## Architektur

```
voice_daemon ──utterance──▶ jarvis_app/bridge ──▶ jarvis_core
                                                   ├─ router.py   (Schnellpfad: Browser-Verben)
                                                   ├─ agent.py    (Tool-Loop: LLM ↔ Werkzeuge)
                                                   └─ tools/
                                                       └─ browser.py (MCP-Client)
                                                                │ stdio (MCP)
                                                                ▼
                                                   npx chrome-devtools-mcp
                                                       --user-data-dir=.chrome-jarvis
                                                                │ CDP
                                                                ▼
                                                            Chrome (Jarvis-Profil)
```

### `jarvis_core/tools/browser.py` — MCP-Browser-Client

- **Lazy Start:** Beim ersten Browser-Befehl startet der MCP-Server als Subprozess
  (stdio). Flags: `--user-data-dir=<repo>/.chrome-jarvis` (dauerhaftes Profil),
  `--channel=stable`. Läuft danach weiter (eine Instanz pro Jarvis-Sitzung).
- **Lebenszyklus:** `ensure_started()`, `shutdown()` (beim App-/Daemon-Ende);
  ein einmaliger Neustart bei Server-Absturz.
- **Hohe Methoden** (kapseln die rohen MCP-Tools des Servers):
  - `open_url(url)` · `search(query, engine="google"|"youtube")`
  - `navigate(action)` — back / forward / reload
  - `read_page() -> str` — Textinhalt für die Zusammenfassung/TTS
  - `snapshot() -> list[Element]` — Accessibility-Snapshot mit `uid`-Referenzen
  - `click(uid)` · `type(uid, text)` · `press_enter()` · `scroll(direction)`
  - `tabs()` · `close_tab()`
- Jede Methode liefert ein **JSON-serialisierbares Ergebnis** (Erfolg/Fehler +
  knapper Text), das der Agent-Loop dem Modell zurückgibt.
- **UI-unabhängig** — keine Importe aus `jarvis_app` (wie der restliche `jarvis_core`).

### `jarvis_core/agent.py` — Tool-Loop

- Erhält die Nutzeräußerung und entscheidet, ob ein Browser-Werkzeug nötig ist:
  1. **Schnellpfad** (in `router.py`): klare Browser-Verben („öffne", „such(e) nach",
     „navigiere zu", „scroll(e)", „klick(e) auf", „lies vor", „geh zu", „zurück") →
     direkt das passende Werkzeug mit extrahiertem Argument.
  2. **Ollama-Tool-Calling:** Sonst bekommt das Modell (qwen2.5:3b) die Werkzeuge als
     Schema (`tools=`-Parameter der Ollama-Chat-API). Antwortet es mit `tool_calls`,
     führt der Loop sie aus und reicht die Ergebnisse zurück.
- **Schleifengrenze:** max. ~6 Werkzeug-Schritte je Anfrage; danach Abschlussantwort
  erzwingen (Schutz vor Endlosschleifen auf dem kleinen Modell).
- **Narration:** Vor jeder Aktion erzeugt Jarvis einen kurzen Satz („Ich öffne
  YouTube, Sir."), der über den bestehenden Sprech-/Caption-Pfad ausgegeben wird —
  Text und Stimme synchron wie bei normalen Antworten.
- Der Loop ist ein **Generator von Ereignissen** (wie `chat.ChatService.send`):
  `route`/`action`/`token`/`done`/`error`, damit Bridge und Daemon ihn unverändert
  konsumieren.

### Einbindung

- **Bridge/Voice-Pfad bleibt gleich:** Browser-Befehle laufen durch denselben
  Utterance→Antwort→Caption→TTS-Weg. Der Agent-Loop ersetzt für Browser-Anfragen
  intern die reine `ChatService.send`-Strecke; nicht-Browser-Anfragen laufen
  unverändert über `ChatService`.
- **Konfiguration** (`config.yaml`, neuer Abschnitt `browser`):
  ```yaml
  browser:
    enabled: true
    profile_dir: ".chrome-jarvis"   # relativ zum Repo, dauerhaftes Jarvis-Profil
    channel: "stable"               # Chrome-Kanal
    headless: false                 # sichtbarer Browser
    max_steps: 6                    # Obergrenze Werkzeug-Schritte pro Anfrage
    search_engine: "google"         # Default für „such nach …"
  ```

## Werkzeug-Oberfläche (V1)

| Werkzeug | Argumente | Zweck |
|---|---|---|
| `open_url` | url | Adresse öffnen (neuer Tab) |
| `search` | query, engine? | Google-/YouTube-Suche |
| `navigate` | action: back\|forward\|reload | Verlauf/Neuladen |
| `read_page` | — | Seitentext für Zusammenfassung/Vorlesen |
| `click` | uid | Element aus dem Snapshot anklicken |
| `type` | uid, text | Text in Feld eingeben |
| `press_enter` | — | Eingabe bestätigen |
| `scroll` | direction: up\|down | Scrollen |
| `tabs` / `close_tab` | — | Tabs auflisten / schließen |

Element-Referenzen (`uid`) stammen aus dem Accessibility-Snapshot des Servers; das
Modell wählt das Ziel anhand der Beschreibung.

## Sicherheit

- **Strikte Profil-Isolation:** ausschließlich `.chrome-jarvis`; der echte Chrome des
  Nutzers (mit E-Mail/Banking-Logins) wird nie angefasst. Kein `--browserUrl` auf eine
  bestehende Instanz.
- MCP-Server lauscht nur lokal (stdio-Subprozess, kein offener Port nach außen).
- Klick/Tippen ohne Rückfrage ist akzeptiert, weil das Profil sandboxed ist; jede
  Aktion wird hörbar angesagt, sodass der Nutzer eingreifen kann (Barge-in/„Stop").
- **Später (nicht V1):** optionale Domain-Allowlist, Bestätigung für sensible Aktionen.

## Fehlerbehandlung

- **Node/npx fehlt** oder Server startet nicht → „Ich kann den Browser nicht
  erreichen, Sir — Node.js scheint zu fehlen." (+ Log mit konkretem Befehl).
- **Chrome nicht installiert** → entsprechender gesprochener Hinweis.
- **MCP-Server-Absturz** → genau ein automatischer Neustart; scheitert er erneut,
  saubere Meldung statt Hängen.
- **Element nicht gefunden / Tool-Fehler** → „Das konnte ich auf der Seite nicht
  finden, Sir." Der Loop bricht die Anfrage kontrolliert ab.
- **Schleifengrenze erreicht** → Jarvis fasst zusammen, was er erreicht hat.

## Tests

- **Schnellpfad-Erkennung** (Unit, kein Browser): Browser-Verben → richtiges Werkzeug
  + Argument; Nicht-Befehle gehen nicht fälschlich in den Browser.
- **Tool-Schema** (Unit): Schemas sind valides JSON, Argumente konsistent benannt.
- **Agent-Loop** (Unit, gemockter Browser-Client): Tool-Call → Ausführung →
  Ergebnis-Rückgabe → Abschluss; Schleifengrenze greift; Fehlerpfad.
- **Integration** (gegen echten `chrome-devtools-mcp`): lokale Testseite öffnen,
  `read_page` liefert erwarteten Text, `close_tab` räumt auf. Headless im CI-Stil.

## Umsetzung in Subagent-Strängen

Der Nutzer wünscht ausdrücklich parallele Subagents. Schnitt mit Abhängigkeiten:

1. **Strang A — MCP-Browser-Client** (`tools/browser.py`): Server-Start, MCP-Handshake,
   hohe Methoden, gegen den echten Server verifiziert. *Unabhängig.*
2. **Strang B — Werkzeug-Schemas + Schnellpfad** (`router.py`-Erweiterung, Schema-Defs):
   reine Logik, ohne laufenden Browser testbar. *Parallel zu A.*
3. **Strang C — Agent-Loop + Einbindung** (`agent.py`, Bridge/Voice-Anbindung, Doku):
   *baut auf A + B auf, danach.*

A und B laufen parallel (Subagents), C danach; jeweils mit Review-Checkpoint und
grünen Tests vor dem Zusammenführen.

## Offene Punkte

- **Python-MCP-Client-Bibliothek**: `mcp`-SDK vs. schlanker eigener stdio-JSON-RPC-Client
  — in der Build-Phase entscheiden (Funktionstest gegen den Server gibt den Ausschlag).
- **VRAM/Leistung**: Chrome belegt etwas GPU-Speicher neben LLM (+ ggf. XTTS); bei Enge
  Chrome mit reduzierter GPU-Nutzung starten. Beim ersten Integrationslauf prüfen.
