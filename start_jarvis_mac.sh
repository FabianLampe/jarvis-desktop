#!/usr/bin/env bash
# J.A.R.V.I.S. unter macOS — Einrichtung, Diagnose, App, Sprach-Daemon, Textmodus.
#
#   ./start_jarvis_mac.sh --setup      venv anlegen + Abhängigkeiten (nur in .venv, keine System-Upgrades)
#   ./start_jarvis_mac.sh --xtts       XTTS-Stimmklon installieren (coqui-tts, torch 2.8.0) in .venv
#   ./start_jarvis_mac.sh --qwen       Qwen3-TTS (MLX) in eigener .venv-qwen installieren
#   ./start_jarvis_mac.sh --voice-ref "Jarvis voice.mp3"   Referenz-WAV für die Jarvis-Stimme erzeugen
#   ./start_jarvis_mac.sh --mic        Mikrofon testen (Berechtigung, Pegel)
#   ./start_jarvis_mac.sh --check      Umgebung und Modelle prüfen
#   ./start_jarvis_mac.sh              Desktop-App starten
#   ./start_jarvis_mac.sh --daemon     Sprach-Daemon starten (zweites Terminal)
#   ./start_jarvis_mac.sh --cli "Text" Auftrag ohne Oberfläche ausführen
#   weitere Argumente (--profile mac, --debug) gehen an die App
set -euo pipefail
cd "$(dirname "$0")"
VENV=".venv"
PY="$VENV/bin/python"

find_python() {
  for c in python3.12 python3.13 python3.11 python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if (3,11) <= sys.version_info[:2] < (3,14) else 1)'; then
      command -v "$c"; return 0
    fi
  done
  return 1
}

case "${1:-}" in
  --setup)
    BASE="$(find_python)" || { echo "Python 3.11–3.13 nicht gefunden (z. B. 'brew install python@3.12')."; exit 1; }
    echo "Python: $BASE"
    [ -x "$PY" ] || "$BASE" -m venv "$VENV"
    "$PY" -m pip install --upgrade pip
    "$PY" -m pip install -r requirements.txt -r requirements-voice.txt
    [ -f .env ] || printf '# Zugangsdaten für Integrationen (nie einchecken) — siehe README\n' > .env
    echo; "$PY" -m jarvis_core.doctor || true
    ;;
  --xtts)
    "$PY" -m pip install coqui-tts "transformers<5" "torch==2.8.0" "torchaudio==2.8.0"
    echo "XTTS installiert. In config.yaml: voice.tts_engine: xtts, voice.xtts_device: cpu"
    ;;
  --qwen)
    BASE="$(find_python)" || { echo "Python 3.11–3.13 nicht gefunden."; exit 1; }
    [ -x .venv-qwen/bin/python ] || "$BASE" -m venv .venv-qwen
    .venv-qwen/bin/python -m pip install mlx-audio soundfile numpy scipy
    echo "Qwen3-TTS (MLX) installiert. Das Modell (~4 GB) lädt beim ersten Sprechen."
    ;;
  --voice-ref) shift; exec "$PY" -m voice_daemon.prepare_voice "$@" ;;
  --mic)    exec "$PY" -m voice_daemon.miccheck ;;
  --check)  exec "$PY" -m jarvis_core.doctor ;;
  --daemon) shift; exec "$PY" -m voice_daemon.main "$@" ;;
  --cli)    shift; exec "$PY" -m jarvis_core.cli "$@" ;;
  *)
    [ -x "$PY" ] || { echo "Zuerst: ./start_jarvis_mac.sh --setup"; exit 1; }
    exec "$PY" -m jarvis_app.main "$@"
    ;;
esac
