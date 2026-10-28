# Repositories für Jarvis

Recherche: 28. Oktober 2026. Grundlage: README.md, CONTEXT.md, ADRs, Konfiguration und vorhandene Module dieses Checkouts. Die verlinkten Upstream-Repositories und ausgewählte Dokumentationen wurden online geprüft. Dies ist eine Auswahl für die Integration, kein Installations- oder Leistungstest auf dem Windows-Zielrechner.

## Empfehlung

Den vorhandenen Jarvis-Kern mit Orb, Persona, Ollama-Router, Sprach-Daemon und Browser-Agent weiterverwenden. Zuerst PC-Werkzeuge, dauerhafte Erinnerungen und Dokumentenwissen ergänzen. Für diese Aufgaben passen kleine Python-Bibliotheken und klar begrenzte Werkzeuge gut zur dokumentierten GTX 1660 Ti mit 6 GB VRAM und 16 GB RAM. Diese Einschätzung ist eine Architektur-Empfehlung; die tatsächliche Leistung muss auf dem Zielrechner gemessen werden.

Ein Repository liefert jeweils Bausteine. Es macht Jarvis erst durch Anbindung, Fehlerbehandlung und geeignete Werkzeugaufrufe zu einem Assistenten mit der entsprechenden Fähigkeit.

## Vorhanden und geplant unterscheiden

Im Code vorhanden sind Chat/Persona/Modellrouting, Sprach-Daemon, Browser-Werkzeuge, Systemmonitor sowie das lesende GitHub-/Gmail-/Wetter-Briefing. In diesem Checkout sind keine entsprechenden Module für eine dauerhafte Wissensbasis, allgemeinen Kalender, Notizen/Reminder oder Smart Home erkennbar. Die Odysseus-Funktionen in CONTEXT.md beschreiben daher hier noch keine fertig integrierte Funktionalität.

Zielplattform laut Projekt: Windows 10/11 und Python 3.11. Dieser Recherchelauf fand im macOS-Checkout statt.

## 1. Die wichtigsten neuen Bausteine

| Fähigkeit | Repository | Einsatz in Jarvis | Betrieb und Grenze |
|---|---|---|---|
| Windows-Apps und Fenster bedienen | [pywinauto/pywinauto](https://github.com/pywinauto/pywinauto) | Programme starten, Fenster finden, Bedienelemente lesen und bedienen; direkter Python-Adapter | Lokal; Win32 und UI Automation. Anwendungsspezifische Abläufe müssen implementiert werden. |
| Allgemeine Windows-Steuerung über MCP | [CursorTouch/Windows-MCP](https://github.com/CursorTouch/Windows-MCP) | Apps, Maus/Tastatur, Desktopzustand, Dateien und PowerShell über einen Werkzeugserver | Lokal; aktuell Python **3.14+**, deshalb separater Prozess mit eigener Umgebung. Alternative zu einer breiten eigenen Windows-Werkzeugsammlung. |
| Lautstärke und Stummschaltung | [AndreMiras/pycaw](https://github.com/AndreMiras/pycaw) | Direkte Windows-Audio-Werkzeuge | Lokal; dokumentierte Python-Unterstützung 3.10–3.14. |
| Dauerhaftes Dokumentenwissen | [chroma-core/chroma](https://github.com/chroma-core/chroma) | Lokaler Vektorspeicher für Textabschnitte und ihre Quellen | Lokal nutzbar; ausdrücklich lokale persistente Speicherung statt Chroma Cloud wählen. Kein fertiges persönliches Gedächtnis. |
| Ähnlichkeitssuche für deutsche Texte | [qdrant/fastembed](https://github.com/qdrant/fastembed) | Text in Suchvektoren umwandeln und an Chroma übergeben | ONNX auf CPU möglich; ein mehrsprachiges Modell wählen. Das englische Standardmodell ist für deutsche Inhalte keine gute Voreinstellung. |
| PDF-, Word-, Excel- und PowerPoint-Inhalte lesen | [microsoft/markitdown](https://github.com/microsoft/markitdown) | Dokumente in Text/Markdown für Zusammenfassung und Wissenssuche umwandeln | Python 3.10–3.14; passende Formatextras auswählen. Grundlegende Textextraktion lokal; optionale Cloud-/Visionfunktionen getrennt behandeln. Kein Dokumenteneditor. |
| Erinnerungen und geplante Abläufe | [agronholm/apscheduler](https://github.com/agronholm/apscheduler) | Einmalige Reminder, wiederkehrende Briefings, zeitgesteuerte Prüfungen | Lokal; stabile 3.x-Version gezielt auswählen. Upstream bezeichnet 4.0 aktuell als Vorabversion. Persistenz und Verhalten nach PC-Neustart festlegen. |
| Texte auf dem Bildschirm lesen | [madmaze/pytesseract](https://github.com/madmaze/pytesseract) | Screenshots und Scanbilder mit OCR auslesen | Lokal auf CPU; benötigt zusätzlich Tesseract und passende Sprachdaten, z. B. Deutsch. Liefert Text, kein allgemeines Bildverständnis. |
| Mathematisch rechnen | [sympy/sympy](https://github.com/sympy/sympy) | Gleichungen, Ableitungen, Integrale und symbolische Berechnungen als Werkzeuge | Lokal; ergänzt das Sprachmodell. Ersetzt nicht Wolframs gesamte Wissensdatenbank. |
| Offline übersetzen | [argosopentech/argos-translate](https://github.com/argosopentech/argos-translate) | Übersetzungswerkzeug für installierte Sprachpaare | Lokal nach Modell-Download; Qualität und Windows-Abhängigkeiten mit Beispielen prüfen. Keine Zusage gleicher Qualität wie DeepL. |

Quellen für die Integrationsdetails: [pywinauto README](https://github.com/pywinauto/pywinauto#readme), [Windows-MCP Voraussetzungen](https://github.com/CursorTouch/Windows-MCP#prerequisites), [pycaw](https://github.com/AndreMiras/pycaw#readme), [Chroma README](https://github.com/chroma-core/chroma#readme), [FastEmbed README](https://github.com/qdrant/fastembed#readme), [MarkItDown README](https://github.com/microsoft/markitdown#readme), [APScheduler README](https://github.com/agronholm/apscheduler#readme), [pytesseract Installation](https://github.com/madmaze/pytesseract#installation), [SymPy](https://github.com/sympy/sympy), [Argos Translate](https://github.com/argosopentech/argos-translate).

## 2. Websuche und Deep Research

- [searxng/searxng](https://github.com/searxng/searxng): selbst gehostete Metasuche als strukturierte Suchquelle. Suchanfragen gehen trotzdem an externe Suchdienste. Für den bisherigen Ansatz ohne Docker unter Windows ist das ein zusätzlicher Betriebsaufwand; zunächst den vorhandenen Browser-Suchpfad verwenden, SearXNG bei Bedarf als separaten Dienst ergänzen.
- [adbar/trafilatura](https://github.com/adbar/trafilatura): Artikeltext und Metadaten aus Webseiten extrahieren. Als lesender Python-Baustein für Recherche geeignet; für dynamische oder eingeloggte Seiten bleibt die bestehende Browser-Steuerung relevant.
- [odysseus-dev/odysseus](https://github.com/odysseus-dev/odysseus): größte funktionale Überschneidung mit eurer ursprünglichen Vision. Dokumentiert Agenten, mehrstufige Recherche, Memory, Dokumenteneditor, E-Mail, Kalender, Notizen und geplante Aufgaben. Für ein mögliches Backend oder als Architekturvorbild untersuchen; Integration und Ressourcenbedarf sind dadurch noch nicht nachgewiesen.

Für einen eigenen Research-Ablauf reichen Suche und Textextraktion allein nicht: Suchfragen planen, Quellen lesen, Belege mit URL speichern und erst dann einen Bericht erzeugen. Qualität und Schrittzahl werden durch das lokale Modell begrenzt. Ein großes Agentenframework beseitigt diese Grenze nicht automatisch.

**Abweichung zur bisherigen Planung:** Das aktuelle Odysseus-Repository liegt unter `odysseus-dev/odysseus` und weist **AGPL-3.0-or-later** aus. Eure ADR 0003 nennt MIT. Für eine Übernahme zählt die Lizenz des konkret ausgewählten Commits; eine historische MIT-Version wurde hier nicht nachgewiesen. Quelle: [aktuelles Odysseus README](https://github.com/odysseus-dev/odysseus#readme).

## 3. Produktivität, Medien und Geräte

| Fähigkeit | Repository | Empfehlung und Voraussetzung |
|---|---|---|
| Google Calendar und Gmail | [googleapis/google-api-python-client](https://github.com/googleapis/google-api-python-client) | Bereits für Gmail verwendet. Für Calendar erweitern; OAuth und die jeweils nötigen Berechtigungen erforderlich. Kommunikation mit Google bleibt online. |
| CalDAV-Kalender | [python-caldav/caldav](https://github.com/python-caldav/caldav) | Python-Client zum Lesen und Ändern von Kalendern und Terminen; benötigt einen CalDAV-Server. Lokaler oder externer Betrieb hängt vom Server ab. |
| Spotify | [spotipy-dev/spotipy](https://github.com/spotipy-dev/spotipy) | Python-Zugang zur Spotify Web API; benötigt App-Zugangsdaten und für persönliche Aktionen OAuth. Verfügbarkeit einzelner Funktionen und Kontovoraussetzungen beim Einbau prüfen. |
| Smart Home | [home-assistant/core](https://github.com/home-assistant/core) | Beste Anlaufstelle für Geräte, Zustände und Automationen. Home Assistant separat betreiben und die offizielle MCP-Server-Integration anbinden. Ob Geräte vollständig lokal arbeiten, hängt von ihrer Integration ab. |
| Telegram | [python-telegram-bot/python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) | Optionaler Jarvis-Bot auf dem Handy mit Telegram Bot API und Bot-Token. Kein universeller Zugriff auf das private Telegram-Konto. |
| Discord | [Rapptz/discord.py](https://github.com/Rapptz/discord.py) | Optionaler Discord-Bot mit Bot-Token und passenden Rechten. Kommunikation mit Discord online. |
| Steam-Daten | [ValvePython/steam](https://github.com/ValvePython/steam) | Kandidat für Steam-WebAPI und Steam-spezifische Daten. README enthält ältere Python-Angaben; aktuelle Kompatibilität gesondert prüfen. Für bloßes Starten eines Spiels zunächst einen kleinen lokalen Start-Adapter vorsehen. |
| WhatsApp | [wwebjs/whatsapp-web.js](https://github.com/wwebjs/whatsapp-web.js) | Nur als nachrangigen Kandidaten führen: separate Node-/Browser-Anbindung, inoffiziell. Upstream weist auf mögliches Blockieren des Kontos hin. |
| DeepL ausdrücklich gewünscht | [DeepL/deepl-python](https://github.com/DeepL/deepl-python) | Offizieller API-Client; benötigt DeepL-API-Zugang und übermittelt Übersetzungstexte an den Dienst. Optionaler Online-Pfad. |
| Wolfram ausdrücklich gewünscht | [jaraco/wolframalpha](https://github.com/jaraco/wolframalpha) | Python-Client für WolframAlpha; Online-Zugang mit App-ID erforderlich. Für lokale symbolische Mathematik zuerst SymPy. |
| Webcam und Gesichtserkennung | [opencv/opencv_zoo](https://github.com/opencv/opencv_zoo) | Kandidat: YuNet für Gesichtserkennung im Bild und SFace für Identitätsvergleich, angebunden mit [OpenCV](https://github.com/opencv/opencv). CPU-Betrieb auf dem Zielrechner messen. Kein allgemeines Verständnis des Bildschirms. |

Quellen mit weiterführenden Details: [offizielle Home-Assistant-MCP-Integration](https://www.home-assistant.io/integrations/mcp_server/), [SFace-Beispiel und Modelllizenz](https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface), [WhatsApp-Projektangaben](https://github.com/wwebjs/whatsapp-web.js#readme). Die übrigen verlinkten Repositories dokumentieren ihre jeweiligen Schnittstellen im README.

Portfolio ist in CONTEXT.md noch zu ungenau beschrieben, um einen bestimmten Datenanbieter zu empfehlen: lokale Bestände auswerten, Livekurse abrufen und Broker-Anbindung sind unterschiedliche Aufgaben.

## 4. Bereits passende Komponenten weiterverwenden

| Vorhandene Fähigkeit | Repository | Einordnung |
|---|---|---|
| Lokale Sprachmodelle | [ollama/ollama](https://github.com/ollama/ollama) | Bestehenden lokalen Client und Router beibehalten. |
| Wake Word | [dscripka/openWakeWord](https://github.com/dscripka/openWakeWord) | Bereits im Sprach-Daemon eingebunden. |
| Spracherkennung | [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) | Bestehender CPU-/int8-Pfad passt zum knappen VRAM-Budget. |
| Geklonte Stimme | [idiap/coqui-ai-TTS](https://github.com/idiap/coqui-ai-TTS) | Upstream zum verwendeten `coqui-tts`-Paket. Code- und Modelllizenzen getrennt betrachten; XTTS-Ressourcenverbrauch im Zusammenspiel mit dem LLM messen. |
| Browser | [ChromeDevTools/chrome-devtools-mcp](https://github.com/ChromeDevTools/chrome-devtools-mcp) | Bereits angebunden; für bestehende Browser-Aufgaben weiterverwenden. |
| Werkzeuganbindung | [modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk) | Bereits verwendete Grundlage für MCP-Clients; weitere Server erfordern noch eine allgemeine Integration in Jarvis. |
| Systemmonitor | [giampaolo/psutil](https://github.com/giampaolo/psutil) | Bereits vorhanden; proaktive Alerts als zusätzliche Logik ergänzen. |
| Online-Sprachausgabe | [rany2/edge-tts](https://github.com/rany2/edge-tts) | Bestehender Ausweichpfad; verwendet Microsofts Online-TTS-Dienst. |

Als zusätzlicher Offline-Sprachpfad ist [OHF-Voice/piper1-gpl](https://github.com/OHF-Voice/piper1-gpl) interessant: lokale neuronale Sprachausgabe, Python-API und Windows-Buildpfad. CPU-Eignung und gewünschte deutsche Stimme testen. Piper ist kein gleichwertiger Ersatz für eure spezifisch geklonte XTTS-Stimme. Das aktuelle Repository weist GPL-3.0 aus; Stimmen haben eigene Modellbedingungen.

## 5. Reihenfolge für die Umsetzung mit Claude

1. **PC-Werkzeuge:** pywinauto plus pycaw für klar definierte Befehle; bestehendes psutil für Zustand und Alerts. Alternativ Windows-MCP als separaten Server evaluieren. Dateien und einfache Programmstarts können zunächst mit Python-Bordmitteln umgesetzt werden.
2. **Gedächtnis und Dokumente:** Notizen/Präferenzen zunächst strukturiert in SQLite speichern. MarkItDown für Dokumenttext; FastEmbed mit mehrsprachigem Modell plus Chroma für inhaltliche Suche. Zu jedem Treffer Datei und Fundstelle speichern. Der Vektorspeicher ersetzt nicht das strukturierte Gedächtnis.
3. **Erinnerungen:** APScheduler mit dauerhafter Ablage, Zeitzone Europe/Berlin und definiertem Verhalten für verpasste Termine. Einen ausgeschalteten PC kann der Scheduler allein nicht aufwecken.
4. **Recherche:** vorhandene Browser-Suche plus Trafilatura und Quellenprotokoll. SearXNG erst ergänzen, wenn sich der zusätzliche Dienst lohnt.
5. **Kalender und Geräte:** vorhandenen Google-Client erweitern; danach Home Assistant und bei Bedarf Spotify oder Telegram.
6. **Zusatzfähigkeiten:** OCR, Mathematik, Offline-Übersetzung und Gesichtserkennung einzeln anbinden, ohne weitere große Modelle dauerhaft auf der GPU zu laden.

Die Integrationsschicht muss erweitert werden: Der aktuelle BrowserAgent ruft eine feste Liste von Methoden am Browser-Objekt auf. Neue PC-, Kalender- und Memory-Werkzeuge werden durch bloßes Installieren eines MCP-Servers noch nicht aufrufbar. Ein gemeinsamer Werkzeug-Dispatcher mit kleinen, verständlichen Schemas passt als nächster Architekturbaustein.

## 6. Punkte für eine spätere Integrationsentscheidung

- Exakte Paketversionen und Python-Anforderungen pro Adapter festhalten; inkompatible Dienste in eigenen Prozessen ausführen.
- Nur benötigte Werkzeuge je Anfrage bereitstellen. Das hält die Werkzeugauswahl und den knappen Modellkontext überschaubar.
- Lokale Modellinferenz von Online-Integrationen unterscheiden: Websuche, Google, Spotify und Messenger benötigen ihre externen Dienste.
- Text aus Webseiten und Dokumenten als Inhalt behandeln; daraus keine neuen Werkzeugberechtigungen ableiten.
- Bei Weitergabe Code-Lizenz, Modell-Lizenz und externe Dienstbedingungen getrennt prüfen. Die Recherche ersetzt keine Prüfung einer konkreten Veröffentlichung.

Es wurden ausschließlich diese Recherchenotizen hinzugefügt. Keine Repositories geklont, Pakete installiert oder Jarvis-Funktionen verändert.
