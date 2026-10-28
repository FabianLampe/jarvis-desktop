# macOS-Unterstützung, gemeinsame Werkzeugsteuerung, Odysseus nur als Referenz

**Status:** accepted (2026-10-28)

## Entscheidungen

1. **Plattformadapter.** `jarvis_core/platform/` definiert eine Schnittstelle (`PlatformAdapter`) mit getrennten Adaptern
   `mac.py` und `windows.py`: Programmstart, Benachrichtigung, Screenshot/OCR, Systemstimme, Zwischenablage, Lautstärke,
   Prozessgruppen/Abbruch, GPU-Status, Datenordner. Der Rest des Codes kennt nur die Schnittstelle. Windows-Bibliotheken
   (pywinauto, pycaw) werden auf dem Mac nicht installiert und sind auch unter Windows (noch) nicht eingebunden.
2. **Eine Werkzeugsteuerung** (`tools/registry.py`, `toolagent.py`) für Browser, Terminal, Dateien, Gedächtnis, Dokumente,
   Erinnerungen, Recherche, Mathematik, OCR und Integrationen. Der alte `BrowserAgent` bleibt als deterministischer
   Schnellpfad; seine Werkzeuge stehen zusätzlich in der Registry.
3. **Richtlinie in Code, nicht im Prompt.** Jede Aktion wird als `allow | confirm | deny` bewertet. Bestätigungen sind an
   *genau eine* Aktion gebunden (Zufalls-ID, Argumente werden gespeichert, einmalig, verfallen nach 120 s). Fremdinhalt
   (Webseiten, Dokumente, Dateien, Befehlsausgaben) setzt ein Taint-Flag: danach brauchen Befehle, Skripte,
   Gedächtnis-/Erinnerungsänderungen, ausgehende Nachrichten und Smart-Home-Aktionen eine Bestätigung.
4. **Erfolg nur aus Ergebnissen.** Der Abschlusstext wird aus den Werkzeugresultaten gebaut; Fehlschläge überstimmen die
   Prosa des Modells; „habe erstellt …“ ohne Werkzeugaufruf wird korrigiert.
5. **Lokal bleibt lokal.** Chat, Werkzeugwahl, Embeddings (FastEmbed/ONNX), Dokumentensuche (Chroma), OCR (Apple Vision),
   Mathematik (SymPy) laufen ohne Cloud-LLM. Online sind nur ausdrücklich genutzte Dienste: Websuche/Recherche,
   edge-tts, Google/Spotify/Telegram/Discord/Home Assistant, einmalige Modelldownloads.
6. **Python.** macOS: 3.12 (3.11–3.13 unterstützt); Windows bleibt bei 3.11.
7. **APScheduler 3.x** (nicht die 4.0-Vorabversion), JobStore in SQLite. Erinnerungen laufen nur, solange Jarvis läuft
   und der Mac wach ist; Verpasstes wird beim Start als „verpasst“ gemeldet.

## Odysseus

Als Architekturvorbild genutzt (Agent-Schleife, Memory, Cron), **nicht** geforkt: Das aktuelle Upstream
(`odysseus-dev/odysseus`) weist laut Recherche AGPL-3.0-or-later aus, ADR 0003 nennt MIT. Ein Fork würde Copyleft-Pflichten
auslösen und ein großes Backend ohne klaren Integrationsvorteil gegenüber dem vorhandenen kleinen Kern bringen. Es wurde
kein Odysseus-Code übernommen. ADR 0003 gilt damit für die macOS-Linie als überholt.

## WhatsApp (bewertet, nicht eingebaut)

Nur inoffizielle Wege (z. B. whatsapp-web.js, Browser-Sitzung des privaten Kontos) existieren für Privatkonten; sie
verstoßen gegen die Nutzungsbedingungen und können zur Sperrung führen. Die offizielle WhatsApp Business Platform braucht
ein Business-Konto, Telefonnummer, Webhook-Server aus dem Internet und Vorlagen — unverhältnismäßig für einen lokalen
Einzelassistenten. **Empfehlung: Telegram als mobiler Kanal.**

## Optionale Module

- **SearXNG:** `research.searxng_url` setzen; ohne wird DuckDuckGo (HTML) genutzt. Kein Docker-Zwang.
- **Offline-Übersetzung:** Werkzeug `translate` ist implementiert, braucht `pip install argostranslate` + Sprachpaket
  (nicht installiert/getestet).
- **Gesichtserkennung:** bewusst **nicht implementiert** (biometrische Daten; Einwilligung, Speicherung, Fehlerraten sind
  vorab zu klären). Kandidat bleibt OpenCV YuNet/SFace.
