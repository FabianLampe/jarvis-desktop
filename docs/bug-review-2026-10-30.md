# Bug- und Logikprüfung vom 30.10.2026

Ausgangsstand: `982fa75` auf `macos-jarvis`. Die Prüfung umfasst die neuen Werkzeuge,
ihre Einbindung in den Agenten sowie Chat-, Abbruch-, Routing- und Sprachlogik.

## Behobene Befunde

| Bereich | Vorher | Korrektur |
|---|---|---|
| JSON-/Textdateien | Der Agent wandelte JSON-Inhalt in Python-Objekte um; aus `true` und doppelten Anführungszeichen wurden `True` und einfache Anführungszeichen. Leere Inhalte wurden abgelehnt. | Koercion nur für tatsächlich als Array/Objekt deklarierte Argumente. Text bleibt unverändert; leere Textdateien sind erlaubt. |
| Werkzeugargumente | Falsch typisierte Werte wie `overwrite: "false"` wurden als wahr interpretiert. Unbekannte Argumente konnten still ignoriert werden. | JSON-Schema-Prüfung vor Policy und Ausführung. Unbekannte Argumente werden zurückgewiesen. Das unterstützte `list_dir.hidden` ist jetzt auch im Schema deklariert. |
| Modellantworten | Fehlerhafte Tool-Call-Strukturen führten zu Ausnahmen; kaputtes Argument-JSON wurde teilweise in einen leeren Aufruf umgewandelt. | Fehlerhafte Antworten werden ausdrücklich gemeldet und nicht ausgeführt. |
| Text-Fallback | JSON-Beispiele innerhalb erklärender Prosa konnten als auszuführender Werkzeugaufruf interpretiert werden. | Nur vollständige JSON-Aufrufe bzw. explizite Tool-Tags werden erkannt; der bekannte kleine Modellfehler mit nicht-ASCII-Präfix und schließendem Tag bleibt unterstützt. |
| Browser-Routing | „Lies die Datei …“ wurde als Lesen der Browserseite behandelt. Bei gemischten Aufträgen wurden unbekannte Teilaufgaben verworfen. „Forschung und Entwicklung“ wurde als Suchbegriff abgeschnitten. | Lokale Ziele werden nicht an den Browser-Schnellpfad geschickt. Gemischte Aufträge gehen vollständig an den Agenten; Konjunktionen in Suchbegriffen bleiben erhalten. |
| Wetter/Uhrzeit/Gedächtnis | Eine beiläufige Wetter-/Uhrzeit-Erwähnung überstimmte Schreibaufträge. „Merke dir, dass ich Wetter mag“ rief das Wetter ab. Mehrere Wettertage wurden auf einen Tag reduziert. | Nur eindeutige Einzelanfragen verwenden den Schnellpfad. Speicherbefehle haben Vorrang; mehrere Teilziele bleiben im Agentenablauf. |
| Werkzeugauswahl | „Teste“, „Überprüfe“, „Liniendiagramm“, „Balkendiagramm“ und „Histogramm“ aktivierten die benötigten Werkzeuggruppen nicht zuverlässig. Einer gemischten Wetter-/Uhrzeitanfrage fehlte die Uhr. | Deutsche Formulierungen und Uhrzeit-/Datumsbegriffe in der Gruppenauswahl ergänzt. |
| Neuer Chat / Parallelität | Verspätete Tokens und Aufgabenabschlüsse konnten einen geleerten Verlauf wieder füllen. Ein Fehler konnte die Frage eines anderen laufenden Aufrufs entfernen. | Chat-Generationen und an den jeweiligen Aufruf gebundene Nachrichten. Alte Ergebnisse werden unterdrückt; nur die eigene unbeantwortete Frage wird entfernt. |
| Abbruch | Nur der zuletzt gestartete Chat-Aufruf wurde markiert. Ein bereits abgebrochener neuer Task löschte sein Signal beim Start. Abbrüche konnten noch Bestätigungskarten erzeugen und auf deren Verfall warten. | Alle aktiven Chat-Aufrufe werden markiert. Einzelne Task-Agenten behalten vor Start gesetzte Abbrüche. Zusätzliche Prüfungen vor Schritten und beim Erzeugen einer Bestätigung. Abgebrochene Schwerlast-Aufrufe laden keine Modelle nach. |
| Sprachfüller | Nach Stopp/neuem Chat konnten alte Füllsätze folgen. Der erste Wetterabruf kurz nach Systemstart blieb aus; Wetter kam nur beim zweiten Füllsatz der gesamten Sitzung. | Füllsätze an Chat-/Stopp-Generation binden. Erstabruf auch bei kurzer Systemlaufzeit und regelmäßiger Wechsel mit gecachtem Wetter. |
| Sprachinstallation | Die konfigurierte Chorus-/Effektkette benötigte `pedalboard`, das in den Sprach-Abhängigkeiten fehlte. | Abhängigkeit in `requirements-voice.txt` ergänzt; Effektzustands-Test besteht. |
| Ollama | Ungültige JSON-/Status-/Nachrichtenformen lösten unkontrollierte Ausnahmen aus. Fehlerhafte Thinking-Metadaten wurden wie gültige Metadaten verarbeitet. Bereits abgebrochene Aufrufe öffneten noch HTTP-Anfragen. | Protokollprüfung mit verständlichen Fehlern, ungültige optionale Metadaten ignorieren, vor der Anfrage Abbruch prüfen und HTTP-Antworten schließen. |
| GitHub | `/issues` lieferte auch Pull Requests. Weiterleitungen konnten als Erfolg gelten. Nach einem Schreib-Timeout war der mögliche Erfolg unklar. | PRs aus der Issue-Liste entfernen, Folgeseiten kennzeichnen, nur HTTP 2xx als Erfolg und bei Schreib-Verbindungsfehlern ausdrücklich unklaren Status melden. |
| Mac-UI | AX-Aufrufe konnten ungebremst auf eine hängende App warten. Nach einer fehlgeschlagenen Aktion blieben Snapshot-IDs weiter gültig. | AX-Messaging-Timeout, Gesamtbudget beim Lesen und Abbruchprüfungen. Snapshots nach jedem Aktionsversuch verwerfen; fehlgeschlagene Snapshots nicht weiterverwenden. |
| Konfiguration / Workflows | Falsch geformte Host-/Server-Listen führten später zu internen Fehlern. Workflows konnten dauerhaft ungültige Argumenttypen speichern. | Verschachtelte Konfiguration beim Laden prüfen; Workflow-Schritte gegen ihre konkreten Werkzeugschemas prüfen. |
| Prozesslimits / Pfade | Nicht endliche Zeitlimits konnten die Prozessüberwachung umgehen. Geheimnisverzeichnisse mit anderer Großschreibung wurden unter Linux nicht immer gesperrt. | Nur endliche positive Prozesslimits; geschützte Pfade konservativ unabhängig von Großschreibung vergleichen. |
| Portabler Regressionstest | Ein Test setzte für `ls` bei Fehlern pauschal Rückgabecode 1 voraus; GNU/Linux liefert hier 2. | Der Test prüft weiterhin sichtbaren Fehler und fehlende Erfolgsmeldung, ohne eine falsche plattformspezifische Zahl festzuschreiben. |

## Nachweis

- 63 neue Regressionstests in `tests/test_bug_review.py`.
- Gegen den unveränderten Ausgangsstand: **60 fehlgeschlagen, 3 bestanden**. Das sind Testvarianten,
  keine Behauptung von 60 unabhängigen Bugs. Die drei bereits bestehenden Verträge bleiben erhalten.
- Gegen den korrigierten Stand: **63 bestanden**.
- Gesamte lokal ausführbare Suite, einschließlich der Sprachtests: **362 bestanden, 2 externe Browser-Integrationstests ausgenommen**.
- Python-Kompilierung, JavaScript-Syntax und `git diff --check` ohne Fehler.

Die Audio-Abhängigkeiten wurden für diese Prüfung in einer isolierten Umgebung ergänzt.
PortAudio wurde lokal für den Testprozess bereitgestellt; keine echten Mikrofon-/Lautsprecheraktionen
oder Benutzerkonten waren Teil der Tests. Die Browser-Routing-Regression wird durch einen
Fehler-Stand-in abgesichert, damit die Gegenprobe keinen Browser starten muss.

Der AX-Timeout wird auf das systemweite Accessibility-Objekt gesetzt, weil Apple nur dafür
die globale Wirkung innerhalb des eigenen Prozesses dokumentiert:
[AXUIElementSetMessagingTimeout](https://developer.apple.com/documentation/applicationservices/1459345-axuielementsetmessagingtimeout).

## Aktualisieren und Grenzen

```sh
git pull --ff-only origin macos-jarvis
.venv/bin/python -m pip install -r requirements.txt -r requirements-voice.txt
./start_jarvis_mac.sh --check
```

App und Sprach-Daemon danach neu starten. Die Werkzeuganzahl bleibt 82; die Korrekturen
betreffen vorhandenes Verhalten. Mac-Accessibility, ein echter Ollama-Server und eigene SSH-Hosts
sind weiterhin nicht auf realer Zielhardware aus dieser Umgebung verifiziert. Die zwei mit
`integration` markierten Browserfälle benötigen Node/Chrome und wurden nicht gestartet.
Die Prüfung ist keine Garantie, dass das Projekt keine weiteren Fehler enthält.
