# ADR 0005: Automatisches lokales Reasoning

Status: angenommen, 2026-10-30 (Nutzerauftrag).

## Anlass und Entscheidung

Jarvis soll komplexe Aufgaben selbstständig durchdenken, statt die größere Modellspur nur
auf ein Schlüsselwort oder einen Schalter hin zu wählen. Diese Entscheidung erweitert
ADR 0001/0002 hinsichtlich der automatischen Modellauswahl; lokal-only bleibt bestehen.

Ein optionales `Profile.reasoning` benennt das lokale Modell ausdrücklich. Im bestehenden
Mac-Profil ist es `qwen3.6:35b`; Windows/Home und Dev bleiben unverändert. Der Router nutzt
eine nachvollziehbare deutsche/englische Heuristik (Analyse, Vergleich, Planung, Coding,
mehrere Aktionen), keinen zusätzlichen Klassifikator-Aufruf. Manuelle Schwerlast hat Vorrang.
Eine Schnellspur-Überschreibung darf eine bewusste Reasoning-Auswahl nicht ersetzen.
Die Heuristik kann Mehrdeutigkeiten falsch einstufen; `reasoning.mode` erlaubt auto/on/off.

Der Werkzeugagent nutzt dieselbe Auswahl und kann nach einem fehlgeschlagenen Werkzeug
auf den Reasoner wechseln. Direkte Werkzeugpfade bleiben modellfrei. Berechtigungen und
Bestätigungen werden ausschließlich durch die bestehende Registry geprüft.

## Ollama-Vertrag

- `/api/show` bestimmt gültige Thinking-Steuerwerte; unterstützte Werte werden pro Modell
  gecacht. Ohne verlässliche Metadaten bleibt der Modellstandard aktiv.
- `think` steht auf Request-Ebene, `num_predict` unter `options`. Named Levels sind erlaubt;
  kein pauschales `think: true` für alle Modelle.
- `message.thinking` wird im Chat nur als Statussignal behandelt. Im laufenden Tool-Dialog
  wird es mit den Assistant-Tool-Calls zurückgegeben, niemals an UI/Sprachausgabe weitergereicht.
- Begrenzte Tool-Generierungen werden intern gestreamt und erst nach einem vollständigen
  `done`-Frame als ausführbare Modellantwort zurückgegeben.
- `done_reason: length`, Stream-Abbruch und Zeitbudgetüberschreitung sind keine Erfolge.
- Das Tokenbudget umfasst Denken und Antwort zusammen. Das Zeitbudget eines Tool-Auftrags
  wird über dessen Modellaufrufe geteilt; Werkzeugzeiten/Bestätigungszeiten zählen separat.
  Zeitprüfungen erfolgen zwischen Stream-Frames plus HTTP-Lesezeitlimit; kein Echtzeit-Abbruch
  während eines stillen Lade-/Serverabschnitts wird versprochen.

Das Kontextbudget reserviert Antwortplatz. Bei Platzmangel werden zuerst Denktexte entfernt;
reichen Nutzerziel und vollständige Werkzeugergebnisse nicht mehr hinein, wird der Auftrag
mit einer Kontextlimit-Meldung beendet, statt Ergebnisse still zu verlieren.

## Abschluss und Grenzen

Der Agent-Prompt verlangt eine Prüfung aller Teilziele und bei Bedarf lesende Kontrollen.
Tatsächliche Werkzeugfehler überstimmen Modellprosa weiterhin. Identische Aufrufe sind auf
zwei Versuche begrenzt. Das Schrittbudget zählt auch gebündelte Tool-Calls, und nach dessen
Verbrauch ist nur noch ein abschließender Modellaufruf ohne Werkzeuge erlaubt.
Dies ist eine überprüfbare Agentenschleife, kein eigenes Reasoning-Training und kein Beweis
für die Korrektheit einer Modellantwort oder die vollständige Erfüllung eines beliebigen Ziels.

## Validierung

`tests/test_reasoning.py` prüft Konfiguration und Rückwärtskompatibilität, automatisches Routing,
HTTP-Capability-Abfrage, getrenntes Thinking-Streaming, Tool-Call-Erhalt, Token-/Zeit-/Kontextlimits,
Abbruch und Schleifenbegrenzung. Die bestehenden Router-, Chat-, Werkzeug-, Policy- und Bridge-Tests
decken die Integration ab. Eine reale Ollama-Instanz bzw. Apple-Silicon-Geschwindigkeit wird hier
nicht simuliert oder als gemessen ausgegeben.

Gezielter Lauf: 89 Tests bestanden (`test_reasoning`, Router, Chat, Ollama-Client,
ToolAgent, Bridge-Tools und Konfiguration). Ein zusätzlicher breiter Lauf erreichte
222 bestandene Tests und fünf Fehler, die am unveränderten Ausgangscommit
`877f918` separat reproduziert wurden: geschützte Pfade, Linux-`ls`-Rückgabecode,
Filler-Reihenfolge und zwei durch fehlendes PortAudio blockierte Sprachtests.
Vier reine Audio-Testdateien konnten wegen PortAudio nicht gesammelt werden;
zwei externe Integrationstests wurden nicht ausgeführt.
