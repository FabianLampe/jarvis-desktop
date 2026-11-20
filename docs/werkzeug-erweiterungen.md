# Neue Jarvis-Werkzeuge

Der Branch enthält 82 registrierte Werkzeugfunktionen (21 zusätzliche Funktionen).
Das Modell erhält pro Auftrag die passenden Werkzeuggruppen. Registrierung bedeutet
nicht, dass optionale Pakete, Konten oder macOS-Rechte bereits auf deinem Rechner eingerichtet sind.

## Aktualisieren

Jarvis beenden, im Repository ausführen, danach App und gegebenenfalls Sprach-Daemon neu starten:

```sh
git switch macos-jarvis
git pull --ff-only origin macos-jarvis
.venv/bin/python -m pip install -r requirements-tools.txt
./start_jarvis_mac.sh --check
./start_jarvis_mac.sh
```

Unter Windows entsprechend `.venv\Scripts\python.exe -m pip install -r requirements-tools.txt`.
Die macOS-Pakete werden dort durch Plattformmarker übersprungen. Die Datenwerkzeuge verwenden
dieselbe Python-Umgebung wie Jarvis; die Pakete daher in `.venv` installieren.

Der Check und „Welche Integrationen sind eingerichtet?“ zeigen auch den lokalen
Einrichtungsstatus der Erweiterungen. Dieser Status startet keine MCP-Server und
behauptet keine erfolgreiche SSH-Verbindung.

## Was hinzugekommen ist

| Werkzeug | Funktion / Beispiel |
|---|---|
| `browser_snapshot` | Buttons und Felder samt `uid` im Browser erfassen; vor `click`/`type_text` verwenden. |
| `git_status` | Repository-Branch und geänderte Dateien anzeigen. |
| `git_diff` | Änderungen lesen, optional `staged` oder eine einzelne Datei. |
| `git_branch` | Branch erstellen, optional direkt wechseln; bestehende Branches werden nicht überschrieben. |
| `github` | Issues/PRs lesen, Issue erstellen oder Titel/Text ändern, Draft-PR erstellen. |
| `run_tests` | pytest mit relativen Testpfaden oder Node-IDs ausführen; Zähler aus JUnit-XML. |
| `verify_file` | Datei auf Existenz, Textinhalt, Mindestgröße und/oder SHA256 prüfen. |
| `ssh_diagnose` | System, Netzwerk, Dienststatus oder Logs eines konfigurierten Linux-/Pi-Hosts lesen. |
| `ssh_run` | Explizite Argumentliste auf einem konfigurierten Host ausführen. |
| `analyze_table` | CSV/TSV/XLSX: Zeilen, Spalten, Datentypen, Fehlwerte und Zahlenstatistiken. |
| `plot_table` | Linien-, Balken- oder Histogramm als PNG erzeugen. |
| `create_document` | PDF, DOCX oder XLSX aus Titel, Absätzen und Tabellenzeilen erzeugen. |
| `mac_ui_snapshot` | Accessibility-Elemente einer laufenden Mac-App lesen. |
| `mac_ui_action` | Element klicken, Textfeld setzen oder Tastenkürzel senden. |
| `tool_catalog` | Werkzeugnamen bzw. Schemas einer Gruppe zum Planen eines Ablaufs anzeigen. |
| `workflow_save` | Folge konkreter Werkzeugaufrufe dauerhaft speichern. |
| `workflow_list` | Gespeicherte Namen auflisten. |
| `workflow_get` | Gespeicherte Schritte ansehen. |
| `workflow_run` | Schritte mit den normalen Prüfungen und Bestätigungen ausführen. |
| `mcp_list_tools` | Werkzeuge und Eingabeschemas eines konfigurierten stdio-Servers entdecken; optional `filter`. |
| `mcp_call` | Ein entdecktes Werkzeug mit Schema-Prüfung aufrufen. |

## Sofort ausprobieren

- „Zeige den Git-Status des Repositories `/Pfad/zum/Projekt`.“
- „Erstelle dort einen Branch `feature/meine-aenderung`.“
- „Führe im Projekt die Tests aus und zeige die Fehler.“
- „Prüfe, ob `Projekt/README.md` den Text Installation enthält.“
- „Analysiere `messwerte.csv` und erstelle ein Liniendiagramm mit Zeit und Leistung als `leistung.png`.“
- „Erstelle einen PDF-Bericht `auswertung.pdf` aus diesen Ergebnissen.“
- „Zeige die klickbaren Elemente der aktuellen Browserseite.“

Relative Dateipfade gelten im Arbeitsordner `~/Jarvis`. Ein Git-/Test-Repository wird über `repo` angegeben.
Tests laufen mit Jarvis' Python-Umgebung; projektspezifische Python-Abhängigkeiten müssen darin vorhanden sein.
`run_tests` unterstützt pytest, keine beliebigen Shell-Optionen oder npm-Testskripte. Andere Runner bleiben
über das vorhandene Terminalwerkzeug erreichbar.

GitHub-Lesen funktioniert für öffentliche Repositories auch ohne Token. Für private Repositories und
Schreibaktionen `JARVIS_GITHUB_TOKEN` in `.env` setzen; Repository-Rechte passend begrenzen.
Ein Draft-PR braucht bereits vorhandene Remote-Branches `head` und `base`. Das Werkzeug pusht nicht automatisch.

## Raspberry Pi / SSH

In `config.yaml` eigene Werte eintragen:

```yaml
extensions:
  ssh:
    enabled: true
    hosts:
      pi:
        hostname: "raspberrypi.local"
        user: "DEIN_PI_BENUTZER"
        port: 22
```

Den bestehenden `extensions`-Abschnitt bearbeiten, keinen doppelten YAML-Schlüssel anlegen.
Der Mac muss den Host erreichen können. Einmal selbst mit `ssh DEIN_PI_BENUTZER@raspberrypi.local`
anmelden, den Hostschlüssel prüfen und Schlüssel-Login einrichten. Jarvis läuft mit `BatchMode=yes`
und `StrictHostKeyChecking=yes`: kein Passwortdialog, kein automatisches Akzeptieren unbekannter Schlüssel.
Private Schlüssel bleiben bei OpenSSH und werden nicht vom Modell gelesen.

Danach beispielsweise „Diagnostiziere das Netzwerk auf meinem SSH-Host pi“ oder
„Lies die letzten Logs des Dienstes ollama auf pi“. Die Diagnose umfasst feste Linux-Befehle
(`ip`, `getent`, `ping`, `systemctl`, `journalctl`). Fehlende Programme/Rechte oder inaktive Dienste
werden als Fehler gemeldet. Sie repariert keine Netzwerkeinstellungen automatisch.
`ssh_run` erlaubt einen ausdrücklich bestätigten Befehl, etwa einen Dienstneustart.

## Mac-Apps bedienen

```yaml
extensions:
  mac_ui:
    enabled: true
```

Danach unter **Systemeinstellungen → Datenschutz & Sicherheit → Bedienungshilfen** das
startende Terminal bzw. Jarvis freigeben. Bei Bedarf Terminal/Jarvis neu starten. Ohne diese
Freigabe meldet das Werkzeug die fehlende Berechtigung.

„Zeige die Fensterelemente von Finder“ liefert bis zu 200 Elemente, mit begrenzter Baumtiefe und
kurzen Textwerten. Aktionen beziehen sich auf diese Element-IDs, nicht auf geratene Bildschirmkoordinaten.
Klicken verwendet `AXPress`, Texteingabe setzt `AXValue` in unterstützten Textfeldern.
Tastenkürzel wie `cmd+s`, `cmd+shift+s`, `tab` und `return` funktionieren nur, solange dieselbe
App im Vordergrund bleibt. IDs gelten 30 Sekunden und werden nach einer Aktion ungültig.
Danach einen neuen Snapshot abrufen. Geschützte Passwortfelder werden nicht ausgelesen/beschrieben.
Layoutabhängige Kürzel mit Y/Z und Sonderzeichen sind nicht unterstützt; entsprechende Menüelemente per AX verwenden.

Die Bedienbarkeit hängt vom Accessibility-Baum der jeweiligen App ab. Spiele, Canvas-Oberflächen
oder Apps ohne passende AX-Aktionen sind damit nicht automatisch bedienbar.

## Wiederverwendbare Abläufe

Beispiel für einen speicherbaren Workflow `projekt_pruefen`:

```json
{
  "name": "projekt_pruefen",
  "description": "Änderungen ansehen und die Projekttests starten",
  "steps": [
    {"tool": "git_status", "args": {"repo": "/Pfad/zum/Projekt"}},
    {"tool": "git_diff", "args": {"repo": "/Pfad/zum/Projekt"}},
    {"tool": "run_tests", "args": {"repo": "/Pfad/zum/Projekt", "targets": ["tests"]}}
  ]
}
```

Jarvis kann dies über `workflow_save` speichern, nachdem du die konkrete Folge bestätigt hast.
Anschließend „Starte den Workflow projekt_pruefen“. Die Speicherung liegt im Jarvis-Datenordner
unter `workflows/`. Workflows speichern feste Argumente; es gibt keine Variablen-/Shell-Templating-Sprache.
UI-IDs gehören deshalb nicht in dauerhaft gespeicherte Workflows.

Jeder Schritt läuft durch dieselbe Registry wie ein normaler Einzelauftrag. Das gilt auch für
Bestätigungen nach fremdem Inhalt, Löschaktionen, Tests und Schreibzugriffe. Der erste Fehler,
eine Ablehnung oder ein Abbruch stoppt den Ablauf. Frühere erfolgreiche Änderungen werden nicht
automatisch zurückgerollt. Das gemeinsame `tools.max_steps`-Budget gilt weiterhin. Rekursion ist ausgeschlossen.

## Weitere MCP-Server

```yaml
extensions:
  mcp:
    enabled: true
    servers:
      mein_server:
        command: "/absoluter/pfad/zum/serverprogramm"
        args: []
        env_names: []
```

Server außerhalb von Jarvis nach dessen Anleitung installieren. `command` und `args` stammen
aus deiner Konfiguration, nicht aus einer vom Modell geratenen Serverinstallation.
Benötigte Zugangsdaten in Umgebungsvariablen/`.env` hinterlegen und nur deren Namen in
`env_names` aufnehmen. Keine Tokens in `args` oder `config.yaml` schreiben.

„Zeige die MCP-Werkzeuge von mein_server“ startet die konfigurierte stdio-Verbindung zur Erkennung.
`mcp_call` prüft die Argumente gegen das vom Server angebotene Schema. Auch als lesend beworbene
Aufrufe benötigen eine Bestätigung, weil Server ihre eigenen Seiteneffekte definieren.
Serverinhalte sind Fremdinhalt. Verbindungen werden je Aufruf geöffnet und geschlossen;
Fehler, Server-Fehlerantworten, Zeitlimits und Abbruch sind keine Erfolge.
Zustand im Serverprozess bleibt damit nicht zwischen Aufrufen erhalten; der Server muss benötigte Daten selbst persistieren.

Dieser Adapter unterstützt stdio und Textantworten; HTTP/SSE-Server und die direkte Darstellung von
MCP-Bild-/Audioantworten sind nicht enthalten. Nichttext-Blöcke werden im Ergebnis gezählt.

## Grenzen und Prüfung

- Dateien für Datenanalyse maximal 25 MB, 100000 Zeilen und 100 Spalten; CSV/TSV als UTF-8.
- Bei umfangreicher Statistik mit `columns` auf einzelne Spalten eingrenzen.
- Balkendiagramme maximal 100, Liniendiagramme maximal 10000 Zeilen; größere Daten vorher aggregieren.
- Dokumenttext maximal 50000 Zeichen, Tabellen maximal 20000 Zellen; PDF-Tabellen maximal 8 Spalten.
- Daten-/Dokumenterstellung läuft als fester Python-Worker mit Zeitlimit, ohne freien Modellcode.
- Ausgabe wird erst nach Erfolg übernommen. Existierende Dateien benötigen `overwrite` und Bestätigung.
- Große strukturierte Ergebnisse bleiben gültiges JSON und melden `truncated: true`.

Getestet wurden lokale Git-Repositories, echte pytest-Läufe, CSV/Diagramm-/Dokumentausgabe,
Bestätigungen/Abbruch und ein eigener stdio-MCP-Testserver. Mac-AX-Aufrufe sind gegen einen
API-Stand-in geprüft; kein realer Mac, SSH-Pi oder GitHub-Schreibzugriff wurde aus dieser Umgebung bedient.


## Überarbeitung November 2026: Auswahl, Netzwerk, Recherche, Schutz, Diagnose, Sehen, Wächter

### Werkzeugauswahl durch das Modell (`tools.router: auto`)

Bisher entschied allein eine Stichwortliste, ob Jarvis Werkzeuge benutzt. Die Liste bleibt als
Schnellweg. Trifft sie nichts, ordnet das Modell die Anfrage in einem kurzen Aufruf (warm ca. 0,1–0,6 s)
einer oder mehreren Werkzeuggruppen zu oder schickt sie in den normalen Chat. Fällt die Weiche aus,
läuft die Anfrage wie bisher als Chat. `tools.router: keywords` stellt das alte Verhalten wieder her.
Das Schrittlimit je Auftrag liegt bei 12 (`tools.max_steps`).

### Neue Werkzeuge

| Werkzeug | Gruppe | Funktion |
|---|---|---|
| `network_overview` | network | Verbindungsart, eigene IP, Netzbereich, Router, DNS, WLAN-Kanal/Signal, Internet-Latenz. |
| `network_devices` | network | Geräte im Netz mit IP, Name, Hersteller, MAC; markiert Router, diesen Mac und neue Geräte. |
| `network_name_device` | network | Einem Gerät einen eigenen Namen geben („192.168.178.31 ist mein iPhone“). |
| `network_speedtest` | network | Download, Upload, Latenz (Apples `networkQuality`, ca. 20 s). |
| `network_ports` | network | Erreichbare Dienste an einem Gerät; nur private Adressen im eigenen Netz. |
| `system_health` | health | Speicherplatz, RAM, CPU, Akku, Laufzeit, größte Verbraucher, Auffälligkeiten. |
| `bluetooth_devices` | health | Gekoppelte/verbundene Bluetooth-Geräte samt Akkustand. |
| `software_updates` | health | Ausstehende macOS-Updates (installiert nichts). |
| `screen_look` | vision | Bildschirm ansehen und Fragen dazu beantworten (lokales Modell, Bild bleibt auf dem Rechner). |
| `image_look` | vision | Bilddatei ansehen (PNG, JPG, HEIC …). |

Hinweise: Den WLAN-Namen verbirgt macOS ohne Ortungsfreigabe für das startende Terminal bzw. Jarvis.
Herstellernamen stammen aus der öffentlichen IEEE-Liste, die einmalig geladen und 90 Tage gecacht wird
(`network.vendor_lookup: false` schaltet das ab). In sehr großen Netzen (mehr als 1024 Adressen) wird nur
der eigene /24-Bereich aktiv abgefragt und die Liste zusammengefasst.

### Recherche

- Quellen werden eingestuft: etablierte Seiten zuerst, soziale Netzwerke und Frageportale gar nicht,
  Unbekanntes nur als gekennzeichnete Ergänzung, solange weniger als zwei etablierte Belege vorliegen.
  `research.only_trusted: true` liest ausschließlich etablierte Quellen; eigene Listen über
  `research.trusted_domains` und `research.blocked_domains`.
- Je Quelle gehen die zur Frage passendsten Absätze ans Modell statt des Seitenanfangs; die Recherche hat
  ein eigenes Ausgabebudget (18 000 Zeichen) und wird nicht mehr mitten im Text gekappt.
- `fetch_url` kennt `focus` für lange Seiten. Seiten, die ihren Inhalt erst per JavaScript laden, liest es
  bewusst nicht über den Browser nach (dort greift die Adressprüfung nicht); Jarvis meldet das und Sir kann
  die Seite ausdrücklich im Browser öffnen lassen.

### Schutz vor Prompt Injection

- Fremder Text wird vor dem Modell bereinigt: unsichtbare Zeichen, Rollenmarken von Chat-Vorlagen und
  nachgemachte Blockmarkierungen werden entfernt. Sieht der Text nach Anweisungen aus, bekommt das Modell
  eine ausdrückliche Warnung.
- Abfluss-Schutz: Hat ein Auftrag fremden Inhalt UND persönliche Daten gelesen, braucht jedes Werkzeug mit
  Weg nach außen (Websuche, Seitenabruf, Browser, Telegram, Discord, GitHub) die Bestätigung des Nutzers.
- Die Vorsicht nach fremdem Inhalt gilt drei Folgeaufträge weiter (so lange steht der Inhalt im Verlauf)
  und endet mit „Neuer Chat“.
- Seitenabrufe prüfen die Adresse am Verbindungsaufbau selbst; DNS-Rebinding auf interne Adressen und
  Umgebungs-Proxys kommen nicht mehr durch.
- `tests/test_injection.py` enthält Angriffstexte und Abläufe, die bei jeder Änderung mitlaufen.

### Wächter (`proactive`)

Jarvis meldet sich von selbst per Mitteilung, im Panel und – bei laufendem Sprach-Daemon – per Stimme:
neues Gerät im Heimnetz, Termin beginnt in Kürze, Platte fast voll, Akku fast leer. Jede Sache wird
einmal gemeldet. In großen, geteilten Netzen (mehr als 64 Geräte) bleibt die Gerätemeldung stumm.
Abschalten über `proactive.enabled: false` oder einzeln je Prüfung.

## Kamera, Bilder im Chat, Code im Editor (November 2026)

| Werkzeug / Funktion | Gruppe | Funktion |
|---|---|---|
| `camera_look` | vision | Nimmt ein Foto mit der Kamera auf (rund 1–2 s, grünes Licht) und beantwortet eine Frage dazu, z. B. Gesichtsausdruck, „wie sehe ich aus“, was vor der Kamera ist. Das Foto wird danach gelöscht; `save: true` legt es unter `Kamera/` ab. Keine Identifizierung von Personen, keine Vermutungen über sensible Merkmale. |
| `open_in_editor` | editor | Öffnet Datei oder Ordner in VS Code (Standard, `tools.editor`), Cursor, Xcode, PyCharm, Sublime, Zed. |
| Bild anhängen | Chat | Bild einfügen (⌘V), ins Panel ziehen oder über ⧉ wählen. Es landet unter `~/Jarvis/Bilder/` und Jarvis wertet es mit `image_look` aus. |

Kamera einrichten: Beim ersten Foto fragt macOS nach der Kamerafreigabe für das Programm, aus dem Jarvis
gestartet wurde (meist Terminal). Wurde sie abgelehnt: Systemeinstellungen → Datenschutz & Sicherheit →
Kamera → Terminal einschalten, Jarvis neu starten. Die Aufnahme läuft in einem eigenen Prozess
(`camera_worker.py`, OpenCV aus `requirements-tools.txt`). Nach dem Lesen fremder Inhalte braucht die Kamera
eine Bestätigung, damit eine Webseite sie nicht einschalten kann.

Code: „Schreib ein Python-Skript … und öffne es in VS Code“ legt die Datei unter `~/Jarvis/Code/` an,
führt sie einmal zur Prüfung aus (nur wenn sie ohne Eingaben läuft) und öffnet sie im Editor.

## Kurzbefehle, Mac-Apps, Projekte, Musik, Bilder, Kamerawache (November 2026, Teil 2)

Gemeinsame Regeln: AppleScript ist immer fester Code (`on run argv`), Werte kommen nur als Argument
(`tools/mac_script.py`); alle Unterprozesse sind argv-Listen ohne Shell; Namen von Kurzbefehlen, Titeln,
Playlists, Notizen und Dateien werden mit `clean()` bereinigt und als Fremdinhalt markiert. Tests starten nie
echte Prozesse (`tests/conftest.py` sperrt `mac_script._spawn`).

| Werkzeug | Gruppe | Richtlinie |
|---|---|---|
| `shortcuts_list`, `shortcut_run` | shortcuts | Name muss exakt in `shortcuts list` stehen. Ausführen = Rückfrage, außer der Name steht in `shortcuts.allowed` **und** es wurde nichts Fremdes gelesen. Eingabe über `--input-path` (temporäre Datei). Ausgabe = Fremdinhalt; Abfluss-Schutz (OUTBOUND). |
| `app_type_text(app, text)` | mac_ui | Aktiviert die App, tippt per System Events (App und Text nur als argv). Nur wenn `extensions.mac_ui.enabled`. Rückfrage bei Terminal, iTerm, Systemeinstellungen, Schlüsselbund & Co. und nach Fremdinhalt. Ohne Bedienungshilfen-Freigabe kommt die Anleitung „Systemeinstellungen → Datenschutz & Sicherheit → Bedienungshilfen → Terminal“. |
| `project_create` | projects | `~/Jarvis/Code/<name>` (Name `[A-Za-z0-9_-]`): README, .gitignore, main.py, `.venv`, `git init`. Mit `packages` immer Rückfrage; nach Fremdinhalt Rückfrage. |
| `project_install` | projects | `pip install` in die `.venv` des Projekts. **Immer** Rückfrage (fremder Code). Paketnamen nur Name + Version (`flask>=3,<4`), keine URLs, Pfade, Optionen. |
| `run_script` | terminal | Liegt ein Python-Skript in einem Projekt mit `.venv`, läuft es mit deren Python. |
| `git_commit` | git | Im Arbeitsordner ohne Rückfrage, außerhalb und nach Fremdinhalt mit. Hooks sind aus; `.env`, `*.pem`, `*.key`, `id_rsa*` … werden nie aufgenommen (und im Ergebnis genannt). |
| `git_push` | git | **Immer** Rückfrage (zeigt Remote-URL und Branch). Kein Force, keine Refspecs. |
| `music_control`, `music_now` | music | Apple Music. `play` mit `query`: erst Playlist gleichen Namens, dann Stimmung → Playlist mit passendem Namen („etwas Ruhiges“ → „…Entspannt…“), sonst Titel-/Interpretensuche in der Mediathek; passt nichts, nennt Jarvis ehrlich die vorhandenen Playlists. Die App startet nur bei `play`. „Musik“ ohne Dienst = Apple Music, Spotify nur auf Zuruf. |
| `notes_create`, `notes_search` | notes | Apple Notizen. Text wird zu maskiertem HTML (kein Markup-Einschleusen). Anlegen: nach Fremdinhalt Rückfrage. Suche: persönliche Daten (PRIVATE), Ausgabe = Fremdinhalt. |
| `spotlight_search` | spotlight | `mdfind` im Benutzerordner (ohne ~/Library, versteckte und geschützte Pfade), Namen vor Inhalt, höchstens 20 Treffer, nach Änderungsdatum. Suchbegriff wird auf harmlose Zeichen reduziert. PRIVATE. |
| `system_control` | system | Feste Aktionen: Dunkelmodus an/aus/umschalten, Bildschirm sperren, Display schlafen, Papierkorb leeren (Rückfrage), WLAN an/aus (Rückfrage). „Nicht stören“ ist ohne Kurzbefehl nicht schaltbar – Jarvis sagt das und erklärt den Kurzbefehl-Weg. |
| `unit_convert` | math | Einheiten offline (Länge, Gewicht, Volumen, Tempo, Temperatur, Fläche, Daten, Zeit); auch im Schnellpfad („Wie viele Kilometer sind 5 Meilen?“). |
| `image_generate` | imagegen | mflux (FLUX auf MLX). Aus, bis `image_generation.enabled: true`. Läuft im eigenen Prozess mit 10 min Zeitlimit und `HF_HUB_OFFLINE=1` – Jarvis lädt nie selbst Gewichte. Ohne Gewichte kommt eine Einrichtungsanleitung (schnell ≈ 34 GB, `filipstrand/Z-Image-Turbo-mflux-4bit` ≈ 6 GB). Ausgabe `~/Jarvis/Bilder/generiert-<Zeit>.png`, danach in Vorschau geöffnet. mflux liegt in einer eigenen Umgebung `.venv-mflux` (bringt torch und OpenCV mit, rund 1,3 GB). Braucht viel Arbeitsspeicher; neben den Ollama-Modellen langsam. |
| `camera_watch_start/stop/status` | vision | Bewegungserkennung per Frame-Differenz in eigenem Prozess (`camera_watch_worker.py`): keine Gesichtserkennung, nichts wird gespeichert. Start **immer** mit Rückfrage, höchstens 60 min, Empfindlichkeit low/medium/high, höchstens eine Meldung je 60 s. Meldung über `ctx.shared["notify"]` (Bridge → `_on_watcher_notice`: Mitteilung, Panel, Stimme). Der Prozess endet mit der Zeit, mit `camera_watch_stop`, beim Beenden von Jarvis (Bridge + atexit) oder wenn Jarvis verschwindet. |

### Kleinigkeiten

- **Wetter-Rat**: „Brauche ich eine Jacke/einen Schirm?“, „Was soll ich anziehen?“ → Schnellpfad ohne LLM: Wetterbericht plus
  deterministischer Satz aus Temperatur, Regenwahrscheinlichkeit, Wind und Wettercode (`weather_tools.clothing_advice`).
- **Timer-Schnellpfad**: „Stell einen Timer auf 10 Minuten“, „Wecker in 2 Stunden“, „Timer für eine halbe Stunde“ → Erinnerung ohne LLM.
- **Selbst merken** (`memory.auto_learn`, Standard an; `jarvis_core/autolearn.py`): Nur aus Sirs eigenen Nachrichten (Bridge, vor dem
  Routing), nie aus Werkzeugausgaben. Erkannt werden ganze, nicht-fragende Sätze: „Ich heiße …“, „Mein Name ist …“,
  „Ich wohne/lebe in …“, „Ich komme aus …“, „Ich arbeite als/bei/für …“, „Ich studiere …“, „Ich mag/liebe/bevorzuge/hasse …“,
  „Ich trinke/esse/höre gern …“, „Mein Lieblings… ist …“, „Ich bin allergisch gegen …“, „Ich bin Vegetarier/Veganer“.
  **Entscheidung gegen Falschtreffer:** Sätze mit „du/dich/dir“, Nebensätzen („dass, wenn, weil …“), Befehlswörtern („mach, zeig, öffne …“),
  Fragen und Aussagen, die mit „es/das/nicht“ beginnen, werden nicht gemerkt („Ich mag nicht, dass du …“, „Ich mag es, wenn du kurz
  antwortest“, „Ich mag Kaffee, mach mir einen“). Solche Vorlieben gehören mit „Merk dir, dass …“ gespeichert. Dubletten werden übersprungen;
  Themen mit nur einem Wert (Name, Wohnort, Herkunft, Beruf, Studium, Lieblings-X) ersetzen den älteren Eintrag.
  Das Panel zeigt „Gemerkt: …“ bzw. „Gemerkt (aktualisiert): …“.
