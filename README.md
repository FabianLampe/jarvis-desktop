# jarvis-desktop

A local desktop assistant for Windows. An animated orb floats on the desktop; clicking it opens a chat panel. Requests are routed to local language models through Ollama, so nothing leaves the machine. An optional voice daemon adds a wake word, speech-to-text and speech output.

## Features

- Chat panel with model routing: a small model for quick answers and a larger one for heavy requests (see [ADR 0001](docs/adr/0001-local-model-strategy.md) and [ADR 0002](docs/adr/0002-heavy-lane-local-7b-no-cloud.md))
- Voice interface: wake word (openWakeWord), German speech-to-text (faster-whisper), text-to-speech (edge-tts), barge-in and follow-up conversation
- Browser control through the `chrome-devtools-mcp` server, using a separate Chrome profile
- Daily briefing: open GitHub issues, unread Gmail (read-only), weather and system status
- Orb rendered on canvas, with parameters configurable in `config.yaml`

## Requirements

- Windows 10 or 11 with the WebView2 runtime
- Python 3.11
- [Ollama](https://ollama.com) running on `127.0.0.1:11434`
  - profile `home`: `gemma3:4b` and `qwen2.5:7b`
  - profile `dev`: `qwen3.5:0.8b`
- Node.js and Chrome for browser control (optional)

## Setup

```bat
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Usage

```bat
start_jarvis.bat                  :: profile from config.yaml
start_jarvis.bat --profile dev    :: lightweight profile without the 7B model
start_jarvis.bat --debug          :: open WebView devtools
```

Controls: click the orb to toggle the chat panel, drag to move it, scroll over it to resize, right-click for the menu.

### Voice

```bat
.venv\Scripts\python.exe -m pip install -r requirements-voice.txt
.venv\Scripts\python.exe -m voice_daemon.selfcheck
start_daemon.bat
```

Say "Hey Jarvis" to start. Microphone, model, voice, sensitivity and conversation window are set under `voice:` in `config.yaml`.

### Daily briefing

Triggered by double clap, the voice command "Briefing", or the button in the panel. Optional setup for real data:

- GitHub: set a read-only token as `JARVIS_GITHUB_TOKEN` in `.env`
- Gmail: place an OAuth client in `.secrets/credentials.json`, then run `python -m jarvis_core.briefing.gmail --login`
- Weather: set the location under `briefing.weather` in `config.yaml`

## Project layout

```
jarvis_app/     desktop front end (pywebview), JS bridge, WebSocket server for the daemon
jarvis_core/    UI-independent core: config, persona, router, Ollama client, chat, system monitor,
                browser agent and tools, briefing
voice_daemon/   wake word, speech-to-text, speech output, WebSocket link
config.yaml     profiles (dev, home), orb parameters, voice and browser settings
docs/           architecture decisions (ADRs) and design specs
tests/          pytest suite
```

The app and the voice daemon communicate over a local WebSocket (`ws://127.0.0.1:8765`).

## Development

```bat
.venv\Scripts\python.exe -m pytest tests\ -q
```

The UI can be viewed without the app by opening `jarvis_app/ui/preview.html` in a browser (mocked bridge).

## Documentation

- [CONTEXT.md](CONTEXT.md): domain terms and project state
- [docs/adr](docs/adr): architecture decision records
- [docs/superpowers/specs](docs/superpowers/specs): design specs
