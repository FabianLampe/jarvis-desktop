# J.A.R.V.I.S. Desktop-GUI — Design (V1)

**Datum:** 2026-06-11 · **Status:** vom Nutzer abgesegnet (Brainstorming mit Visual Companion) ·
**umgesetzt** in diesem Repo (`jarvis_app/` + `jarvis_core/`, siehe Nachtrag unten)

## Zusammenfassung

Eine native Desktop-App im Stil der J.A.R.V.I.S.-Visualisierung aus *Avengers: Age of Ultron*:
Ein animierter Orb schwebt always-on-top auf dem Desktop; Klick öffnet ein Chat-Panel im
Film-HUD-Stil. Die App spricht über einen eigenen, schlanken Python-Kern (`jarvis_core`)
direkt mit Ollama — Modell-Routing und Jarvis-Persona inklusive.

**Diese Entscheidung ersetzt die bisherige GUI-V1-Festlegung** in `CONTEXT.md`
(„leichtes Reskin der Odysseus-Weboberfläche"): Die eigene Orb-Front wird vorgezogen.
Der Odysseus-Fork bleibt geplant und dockt später über dieselbe Backend-Schnittstelle an.

**Entwicklung** auf dem Arbeits-PC (AMD Ryzen 5, Radeon 740M, nur C:), **Zielplattform**
der Heim-PC aus den ADRs (GTX 1660 Ti 6 GB, F:-NVMe, qwen2.5-3B-Paar + 7B-Schwerlast).
Zwei Konfig-Profile decken beide ab.

## V1-Funktionsumfang

1. **Text-Chat** — Eingabefeld + Verlauf, Streaming-Antworten.
2. **Orb mit vier Zuständen** — der Orb verändert sich sichtbar mit dem, was Jarvis gerade
   tut. Die Zustände wirken als Modifikatoren direkt auf die Engine-Parameter
   (Basis = konfigurierte Werte):

   | Zustand | Sichtbares Verhalten | Engine-Modifikatoren |
   |---|---|---|
   | **Leerlauf** | gedimmt, ruhige Rotation | Glühen ×0.75 · Tempo ×1.0 |
   | **Hört zu** | heller Kern, Halo pulsiert (~1,1 s) | Glühen ×1.3 · Kern +30 % · Halo-Puls an |
   | **Denkt** | Ringe & Rotation deutlich schneller, mehr Datenflüsse | Tempo ×2.5 · Datenpunkte ×2 · Glühen ×1.1 |
   | **Spricht** | Kern & Nervenbahn-Impulse pulsen im Sprechrhythmus | Kern-Puls ~0,85 s · Impulsrate ×2 · Glühen ×1.2 |

   Übergänge blenden weich über (~400 ms), kein hartes Umschalten. Zustände kommen in V1
   vom Chat-Lebenszyklus (Senden→Denken→Antwort-Streaming); die Sprach-Daemon-Anbindung
   nutzt später dieselben Zustände über die WebSocket-Schnittstelle. **Erweiterungspunkt
   (Filmreferenz Animal Logic: „driven by dialogue waveforms"):** das state-Protokoll
   trägt ein optionales Feld `amplitude` (0–1); liefert der Sprach-Daemon beim Sprechen
   echte TTS-Amplituden, pulsen Kern und Impulse zur tatsächlichen Stimme statt synthetisch.
3. **Router-/Modellanzeige** — welches Modell antwortet (Allrounder/Coder/7B), Token-Tempo,
   plus 7B-Schalter („Schwerlast") für die nächste Anfrage.
4. **System-Monitor** — CPU, RAM, VRAM in der Panel-Statusleiste (Alerts: später).

**Nicht in V1:** Sprach-Daemon selbst (eigenes Vorhaben, Schnittstelle ist definiert),
Odysseus-Anbindung, proaktive Alerts, Tools/Skills, Tray-Menü.

## Visuelles Design (abgesegnet)

### Form-Faktor: schwebender Orb (Variante A)

- Rahmenloses, transparentes Always-on-top-Fenster; nur der Orb ist sichtbar.
- Klick auf den Orb ⇄ Chat-Panel klappt daneben auf/zu.
- Orb-Standardgröße ~170 px; per Mausrad über dem Orb stufenlos skalierbar,
  per Drag frei positionierbar. Größe und Position werden gespeichert.

### Orb: „Nervenbahnen"-Engine (Canvas, abgesegnete Referenz-Implementierung)

Nachbau der Age-of-Ultron-Matrix (Animal Logic: „sphärische Basis mit zentralem Herz und
rotierenden Ringen; Code/Daten fließen zwischen den Ringschichten"):

- Goldene, organisch verästelte **Nervenbahnen** (Random-Walk-Wachstum mit Verzweigung,
  dichte Sektoren + Lücken), dick am Kern, fein auslaufend.
- **Ringbänder** (Gyroskop-Breitenkreise, oben dominant, als Doppelbänder), dezent.
- **Gerade Strahlen** durch die Kugel, **Goldstaub** mit Cluster-Verteilung,
  ~9 % fast weiße „Hot-Spots", ~13 % Bokeh-Unschärfepunkte.
- **Auge**: weißgoldener Kern, heller Iris-Ring, zwei wirbelnde Teilbögen.
- **Datenpunkte** pendeln zwischen den Ringschichten (AWN-Referenz).
- Tiefe über Z-abhängige Helligkeit/Breite, additives Blending, sanfte Taumel-Rotation.

**Festgezurrte Parameter (Nutzer-Tuning):**

| Parameter | Wert |
|---|---|
| Funken | 250 |
| Adern (Nervenbahnen) | 25 |
| Gerade Strahlen | 4 |
| Ring-Helligkeit | 0.5 |
| Glühen/Bloom | 0.9 |
| Tempo | 0.7 |
| Seed | 5 |

Referenz-Implementierung: [`assets/2026-06-11-orb-engine-reference.html`](assets/2026-06-11-orb-engine-reference.html)
(abgesegneter Stand inkl. Vergleich mit dem Filmstill; die Engine wird unverändert in die
App übernommen, nur die Slider-UI entfällt und die Parameter kommen aus der Konfiguration).

### Chat-Panel: Stil „Film pur" (Variante A)

- Tiefschwarzer Grund (#060402), 1-px-Bronzerahmen mit Winkel-Markern an den Ecken.
- Kopfzeile „J.A.R.V.I.S." + „ONLINE ▮" (Michroma/Technik-Font, Letter-Spacing).
- **Statusleiste**: `MODELL · TEMPO (T/S) · CPU · VRAM · SCHWERLAST: BEREIT/AKTIV`.
- Verlauf: Jarvis-Zeilen orange glühend (#ffb45e + Text-Glow), Nutzer-Zeilen gedämpft
  rechtsbündig; Absender-Label klein in Kapitälchen.
- Eingabezeile: Textfeld, **7B-Schalter**, Mikrofon-Knopf (Push-to-talk, ausgegraut bis
  der Sprach-Daemon existiert).
- Schrift: Rajdhani (Text), Michroma (Labels); beide lokal gebündelt, kein CDN-Zwang.

## Architektur

```
┌────────────────────────────── jarvis_app (pywebview) ─────────────────────────────┐
│  ui/ (HTML/CSS/JS)                                                                 │
│   ├─ orb-engine.js   ← abgesegnete Canvas-Engine, Parameter aus Konfig             │
│   ├─ panel.*         ← Chat-Panel „Film pur", Statusleiste                         │
│   └─ states.js       ← idle/listen/think/speak                                     │
│  bridge.py (js_api): send_message(text, force_7b) · get_status() · save_layout()   │
│  main.py: frameless/transparent/topmost-Fenster, Persistenz von Größe/Position     │
└──────────────┬─────────────────────────────────────────────┬──────────────────────┘
               │ direkter Import                              │ ws://127.0.0.1:8765
        ┌──────▼──────── jarvis_core ────────────┐    ┌───────▼────────────────┐
        │ router.py   Schlüsselwort-Schnellpfad   │    │ Sprach-Daemon (später) │
        │             + 3B-Klassifikator;         │    │ sendet listen/speak,   │
        │             7B nur auf Ansage (ADR 0002)│    │ empfängt push-to-talk  │
        │ persona.py  System-Prompt (DE, „Sir")   │    └────────────────────────┘
        │ ollama_client.py  Streaming, Swap-Logik │
        │ sysmon.py   psutil (+nvidia-smi @home)  │──── HTTP → Ollama :11434
        │ config.py   Profile dev/home (YAML)     │
        └─────────────────────────────────────────┘
```

- **`jarvis_core` ist app-unabhängig**: keine UI-Importe, klar definierte Funktionen —
  später vom Odysseus-Fork oder dem Sprach-Daemon direkt nutzbar.
- **Bridge-Vertrag** (alle Aufrufe JSON-serialisierbar):
  `send_message(text, force_7b) → stream_id`; Tokens/Events gehen per
  `webview.evaluate_js` als `jarvis.onToken(stream_id, token)`,
  `jarvis.onState(state)`, `jarvis.onStatus({model, tps, cpu, ram, vram, heavy})`.
- **WebSocket-Schnittstelle** (V1: Server läuft, nur definiert): JSON-Nachrichten
  `{"type":"state","value":"listening"}` (Daemon→App) und
  `{"type":"push_to_talk"}` (App→Daemon).

### Modell-Routing (V1-Verhalten)

1. Erzwungen: 7B-Schalter aktiv oder Schlüsselwort („Schwerlast", „tiefe Recherche") → 7B.
2. Schnellpfad: Code-Indikatoren (Backticks, `def `, Fehlermeldungs-Muster, „Code",
   „Funktion", „Bug" …) → Coder-Modell.
3. Sonst: Allrounder. Der Allrounder darf in seiner Antwort Eskalation aufs 7B **vorschlagen**.
4. 7B-Ablauf wie ADR 0002: 3B-Paar entladen → 7B laden → antworten → 3B-Paar zurückladen;
   Statusleiste zeigt „SCHWERLAST: AKTIV", der Orb wechselt auf „Denkt".

Ein 3B-Klassifikator für mehrdeutige Fälle ist im Router als Erweiterungspunkt vorgesehen,
V1 nutzt die Schlüsselwort-Heuristik.

### Konfiguration (`config.yaml`, Profile)

```yaml
profile: dev            # dev | home
orb:    { sparks: 250, veins: 25, rays: 4, ring_brightness: 0.5,
          bloom: 0.9, speed: 0.7, seed: 5, size_px: 170 }
profiles:
  dev:   { general: "qwen3.5:0.8b", coder: "qwen3.5:0.8b", heavy: null,  gpu_monitor: false }
  home:  { general: "qwen2.5:3b",   coder: "qwen2.5-coder:3b",
           heavy: "qwen2.5:7b",     gpu_monitor: true }
```

`heavy: null` ⇒ 7B-Schalter ausgeblendet, Schwerlast-Status „NICHT VERFÜGBAR" (dev-PC).
`gpu_monitor: false` ⇒ Statusleiste zeigt „VRAM —" (kein nvidia-smi auf dem dev-PC).

## Fehlerbehandlung

- **Ollama nicht erreichbar**: Orb auf Leerlauf gedimmt + Panel-Meldung
  „Verbindung zum Mainframe unterbrochen, Sir." + Wiederholungsversuch alle 10 s.
- **Modell fehlt** (404 vom Ollama): Meldung mit konkretem Befehl
  („`ollama pull qwen2.5:3b`, wenn ich bitten darf, Sir.").
- **Stream bricht ab**: Teilantwort bleibt stehen, Hinweis am Nachrichtenende, Orb → Leerlauf.
- **WebView2 fehlt** (frische Windows-Installation): Start-Check mit verständlicher
  Installationsanleitung statt Absturz.

## Tests

- **Router**: Unit-Tests der Heuristik (Code-Erkennung, Schlüsselwörter, 7B-Erzwingung,
  dev-Profil ohne heavy).
- **Ollama-Client**: gegen gemockten HTTP-Server (Streaming, Fehlerfälle, Swap-Reihenfolge).
- **Bridge**: Smoke-Test der JSON-Verträge.
- **Optik**: Headless-Screenshot der UI (Edge `--headless --screenshot`) als
  Sichtprüfung — etabliertes Verfahren aus dem Brainstorming.

## Offene Punkte

Keine — alle Designfragen wurden im Brainstorming entschieden (Form-Faktor, Look,
Orb-Parameter, Panel-Stil, Zustände, Architektur, Profile).

---

## Nachtrag (2026-06-11, Umsetzung): Film-Look-Erweiterungen der Orb-Engine

Auf Nutzerwunsch („so soll Jarvis am Ende aussehen", Foto-Referenz
wurde die abgesegnete Engine bei der Übernahme in die App um zwei Schichten Richtung
Age-of-Ultron-Filmstill erweitert — die festgezurrten Parameter (Funken 250 · Adern 25 ·
Strahlen 4 · Ring-Helligkeit 0.5 · Glühen 0.9 · Tempo 0.7 · Seed 5) bleiben unverändert:

- **Wireframe-Fragmente („Hologramm-Stadt")** — rechteckige Gitter-Paneele mit
  Quer-/Längslinien, hellen Eckknoten und vereinzelten „Türmen" auf der Kugelschale,
  geclustert wie der Goldstaub. Liefert die gebaute Code-Stadt-Textur des Films.
  Konfig-Schlüssel `orb.panels` (Default 90).
- **Schweif-Bögen** — rotierende helle Teilringe mit nachziehendem, ausblendendem
  Schweif und hellem Kopfpunkt. Konfig-Schlüssel `orb.arcs` (Default 3).

Außerdem umgesetzt wie spezifiziert: vier Zustände als Engine-Modifikatoren mit
~400-ms-Blende, `amplitude`-Feld (Kern/Impulse folgen echten TTS-Amplituden),
Offline-Dämpfung des Orbs, Linienstärken skalieren mit der Orb-Größe.

**Abweichung zur Architektur-Skizze:** `states.js` ging in `orb-engine.js`
(Zustands-Modifikatoren) + `app.js` (Bridge-Glue) auf; zusätzlich gibt es
`jarvis_core/chat.py` (ChatService) als UI-unabhängige Orchestrierung von
Router + Persona + Ollama-Swap, die Bridge und später der Sprach-Daemon teilen.
Sichtprüfung per Edge-Headless-Screenshot: `jarvis_app/ui/preview.html` (gemockte
Bridge, Parameter `?open=1 · ?state=… · ?zone=…`).
