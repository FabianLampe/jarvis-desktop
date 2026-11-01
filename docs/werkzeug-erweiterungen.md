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
