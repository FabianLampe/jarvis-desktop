# Odysseus forken und nativ unter Python 3.11 betreiben (kein Docker)

**Status:** accepted

Basis ist ein **Fork von Odysseus** (MIT-Lizenz). Obwohl Odysseus Docker-/Linux-first dokumentiert ist,
existiert ein offizieller **nativer Windows-Pfad** (`launch-windows.ps1`, `update_windows.bat`,
venv + `pip install -r requirements.txt` + `uvicorn`), und die **Kern-Abhängigkeiten sind leicht**
(FastAPI, `fastembed`/ONNX statt torch, `chromadb-client`, `mcp`, `caldav`, `bcrypt`, `pyotp` …) —
kein vLLM, Playwright oder torch im Kern. **Python 3.11** ist installiert und die von Odysseus
getestete Version. Daher: **forken, native venv mit 3.11, kein Docker.** Angepasst werden:
Modell-Routing (3B-Paar + 7B), Jarvis-Persona, Sprache, GUI-Branding.

## Konsequenzen

- Wir erben Mehrbenutzer-/Auth-/2FA-/Session-Funktionen, die für **Einzelnutzer** unnötig sind —
  werden deaktiviert/ignoriert, nicht herausgerissen (Merge-Fähigkeit erhalten).
- Der vLLM-Pfad bleibt ungenutzt; LLM läuft über **Ollama** (siehe ADR 0001/0002).
- **Fork-Pflege**: Upstream-Updates müssen mit unseren Änderungen gemergt werden → Anpassungen
  möglichst additiv/abgegrenzt halten.
- **Whisper-STT** über `requirements-optional.txt` vorhanden; **TTS + Wake Word** müssen wir ergänzen.
- Zielort des Forks: **F:** (NVMe), venv mit `py -3.11`.
