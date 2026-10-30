# jarvis-desktop

A local personal assistant for macOS (Apple Silicon) and Windows. An animated orb floats on the desktop; clicking it opens a chat panel. Requests are routed to local language models through Ollama, so nothing leaves the machine. An optional voice daemon adds a wake word, speech-to-text and speech output.

## Features

- Chat panel with model routing: a small model for quick answers and a larger one for heavy requests (see [ADR 0001](docs/adr/0001-local-model-strategy.md) and [ADR 0002](docs/adr/0002-heavy-lane-local-7b-no-cloud.md))
- Automatic local reasoning for analysis, planning, comparisons, coding and multi-step tasks; tool failures can escalate to the reasoning model (see [ADR 0005](docs/adr/0005-automatic-reasoning.md)). Thinking is kept separate from the spoken answer.
- Voice interface: wake word (openWakeWord), German speech-to-text (faster-whisper), text-to-speech (edge-tts), barge-in and follow-up conversation
- Browser control through the `chrome-devtools-mcp` server, using a separate Chrome profile
- Daily briefing: open GitHub issues, unread Gmail (read-only), weather and system status
- Orb rendered on canvas, with parameters configurable in `config.yaml`
- **Tool control** (text and voice): terminal commands and scripts, files and folders, opening apps, system info, with bounded multi-step tasks. Deletions, admin commands and writes outside the workspace are shown in the UI and need an explicit confirmation bound to that exact action
- **Memory**: facts and notes in SQLite (save / search / correct / forget)
- **Document knowledge**: MarkItDown → FastEmbed (multilingual) → Chroma, answers cite file and location
- **Reminders**: APScheduler 3.x with persistent storage, Europe/Berlin, missed ones reported on next start
- **Research with sources**, **local maths** (SymPy), **screen OCR** (Apple Vision on macOS)
- **Integrations** (each off by default): Google Calendar, Spotify, Home Assistant, Telegram, Discord

## Requirements

- macOS 13+ (developed and verified on macOS 26, Apple Silicon, 48 GB) **or** Windows 10/11 with the WebView2 runtime
- Python 3.11 (Windows) · 3.11–3.13 (macOS; 3.12 verified)
- [Ollama](https://ollama.com) running on `127.0.0.1:11434`
  - profile `mac` (auto-selected on macOS): `qwen2.5:14b` (chat, coder, tool agent), `qwen3.6:35b` (heavy lane)
  - profile `home` (Windows, GTX 1660 Ti): `qwen2.5:3b` and `qwen2.5:7b`
  - profile `dev`: `qwen3.5:0.8b`
  - pick models that fit your machine: `./start_jarvis_mac.sh --check` lists what is missing
- Node.js and Chrome for browser control (optional)

## Setup

macOS:

```sh
./start_jarvis_mac.sh --setup      # creates .venv, installs requirements (+ voice), runs the check
```

Windows:

```bat
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Everything is installed into `.venv` only; no system upgrades. First use of document search downloads the embedding model (~1 GB, once).

## Usage

macOS:

```sh
./start_jarvis_mac.sh               # desktop app (profile auto = mac)
./start_jarvis_mac.sh --daemon      # voice daemon (second terminal)
./start_jarvis_mac.sh --cli "Lege einen Ordner Test an"   # no GUI, confirmations asked in the terminal
./start_jarvis_mac.sh --check       # environment + model check
```

Windows:

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

## Panel controls

- **MIC** (panel header): mutes the microphone. The voice daemon closes the audio stream (the macOS recording indicator goes off), so Jarvis hears nothing, no wake word, no clap, no recording. Click again to listen. The state survives a daemon restart. Jarvis can still speak (reminders, replies to typed input).
- **NEU**: starts a new chat — cancels running actions and speech, clears the history and resets the context meter. Voice: "Neuer Chat".
- **KONTEXT** (status bar + bar): tokens used of the model's context window (`num_ctx`), measured from Ollama (or estimated before the first answer). Yellow from 70 %, red from 90 %; Jarvis warns at 85 %. History is trimmed automatically at ~70 % so Ollama never silently cuts the start.
- **■**: stops all running actions and speech.
- **Parallel tasks**: every tool job runs as its own task, several at once (commands, research, documents … — LLM text generation itself is served one request at a time by Ollama). A task list above the chat shows each running task with its current step and a ✕ to cancel just that one. Ask "Was machst du gerade?" for an honest status; "Brich Aufgabe 2 ab" / "Brich alles ab" cancels. For long voice-started tasks Jarvis says what he is still doing every ~30 s, and prefixes announcements with "Aufgabe N" when several run.
- **Weather**: "Wie wird das Wetter heute / morgen / in Berlin?" (Open-Meteo, no key, home location from `briefing.weather`).
- **Follow-ups**: "Ja", "Mach das", "Bitte" after an offer runs the previous request instead of being ignored.

Only one app and one daemon can run: a second start exits with a message (lock files in the data folder).
Jarvis ignores its own voice coming back through the speakers (echo filter, 0.8 s quiet tail after speaking, queued speech so briefing lines are not heard between each other). The double-clap trigger is off by default (`clap_enabled`).
Logs: `~/Library/Application Support/Jarvis/logs/{app,daemon}.log`.

### Automatic reasoning

The `mac` profile uses `qwen3.6:35b` automatically for complex chat/tool requests. Install it once if missing:

```sh
ollama pull qwen3.6:35b
./start_jarvis_mac.sh --check
```

Restart Jarvis after changing the configuration. No special wording or Schwerlast toggle is needed: try
“Analysiere, warum mein Python-Skript langsam wird” or “Erstelle einen Projektordner, schreibe eine README und prüfe den Inhalt”.
Short chat stays on the fast model; existing direct commands (apps, weather, exact maths, saving a fact) still bypass the LLM.

```yaml
reasoning:
  mode: "auto"       # auto | on | off
  max_tokens: 2048    # thinking + final answer together, per generation
  timeout_s: 120     # generation time budget (shared across a tool task's model calls)
```

`on` selects the reasoning model for every LLM-based chat/tool request; `off` disables automatic escalation.
The manual Schwerlast selection still works. Profiles without `reasoning:` keep their previous model routing;
`home` and `dev` do not automatically load the Mac's large model. To choose another local model, set
`profiles.<profile>.reasoning` to its Ollama name and install it yourself. The checker includes that model.

Jarvis discovers supported `think` controls through `/api/show`, including boolean controls and named levels
(prefers `medium`). Unsupported settings are omitted. Older servers without metadata use the model default;
models that always think cannot necessarily disable thinking. Missing models produce the `ollama pull` instruction,
rather than silently substituting another model. The panel shows “Jarvis denkt nach …”; reasoning text is never
sent to speech or saved as the final chat answer. In tool tasks it is retained in the active model dialog and
discarded first if context space runs short; tool results are not silently removed.

Token exhaustion, context exhaustion and repeated identical tool calls are reported explicitly. `tools.max_steps`
counts individual tool attempts, including several calls in one model reply, with one final model call allowed
only for a summary. The prompt asks the model to verify each subgoal against actual tool results; this is not
a formal proof of goal completion. Existing action confirmations and untrusted-content rules still apply.
Generation time is checked as stream chunks arrive and also caps HTTP read timeouts; cancellation closes the
stream at the next chunk/read failure. Model loading or a silent server may delay that feedback. Tool runtimes
and time spent waiting for confirmation have their own limits. More reasoning costs time and is not a guarantee
of correctness. The integration is tested against a local HTTP stand-in, not a real Ollama/Mac installation.

## macOS permissions

Only enable what a feature needs. macOS asks on first use; grant it to the app that starts Jarvis (Terminal / iTerm).

| Permission | Needed for | Where |
|---|---|---|
| Microphone | voice daemon (wake word, push-to-talk) | Privacy & Security → Microphone |
| Screen & System Audio Recording | `screen_ocr` only | Privacy & Security → Screen & System Audio Recording |
| Notifications | reminder banners (`osascript` notifications) | System Settings → Notifications |
| Accessibility | **not used** (no UI scripting) | – |
| Automation | **not used** unless you add AppleScript control of other apps | – |

Admin commands (`sudo …`) are shown for confirmation and then run through the native macOS administrator dialog.

## Tools and safety

- **Workspace**: `~/Jarvis` (override with `tools.workspace` or `JARVIS_WORKSPACE`). Reads work anywhere except protected paths (`~/.ssh`, Keychains, `.secrets`, `.env`, Jarvis data). Writes inside the workspace run directly.
- **Needs confirmation** (card in the panel showing the exact action): deleting, `rm`/`mv`/`kill`/`sudo`, inline code (`python -c`), `git push/reset…`, writes outside the workspace, explicit shell syntax (`run_shell`), killing processes, creating calendar events, risky smart-home domains (locks, covers…).
- **Untrusted content**: web pages, documents, files and command output are marked as data. After reading any of them in a task, commands, memory/reminder changes, outgoing messages and smart-home actions need confirmation too, so a poisoned page cannot grant permissions.
- **Honest results**: success is reported only when the tool result says so; failures, timeouts and cancellations are reported as such. Commands run without a shell (argument lists), with timeout (default 60 s, max 600 s), output limits and a stop button (■).
- **Telegram** (if enabled) only accepts your own chat and can never confirm risky actions.

## Memory, documents, reminders

- Say "Merk dir, dass …", "Was weißt du über …", "Vergiss …" (forgetting asks for confirmation).
- "Lies das Dokument Vertrag.pdf in meine Wissensbasis ein" then "Was steht in meinen Dokumenten zur Kündigungsfrist?" → answer plus `file — page/slide, section, lines`.
- "Erinnere mich morgen um 9 Uhr an …", "jeden Montag um 8 Uhr …". **Reminders are only delivered while Jarvis is running and the Mac is awake**; anything that came due while it was off is announced as *missed* on the next start. Jarvis cannot wake the Mac.
- Data lives in `~/Library/Application Support/Jarvis` (`memory.db`, `reminders.db`, `scheduler.sqlite`, `chroma/`, `models/`).

## Integrations

Set `integrations.<name>.enabled: true` in `config.yaml` and put credentials in `.env` (never committed). Ask Jarvis "Welche Integrationen sind eingerichtet?" for the status.

| Integration | Credentials | Notes |
|---|---|---|
| Google Calendar | OAuth desktop client → `.secrets/credentials.json`, then `python -m jarvis_core.integrations.services calendar-login` | read upcoming events, create events (confirmed) |
| Spotify | `JARVIS_SPOTIFY_CLIENT_ID`, redirect `http://127.0.0.1:8888/callback`, then `… services spotify-login` | playback control needs Premium + active device |
| Home Assistant | `JARVIS_HA_URL`, `JARVIS_HA_TOKEN` | states + service calls |
| Telegram | `JARVIS_TELEGRAM_TOKEN`, `JARVIS_TELEGRAM_CHAT_ID` | send + receive from your own chat |
| Discord | `JARVIS_DISCORD_TOKEN`, `JARVIS_DISCORD_CHANNEL_ID` | send only |

Optional modules: SearXNG (`research.searxng_url`), offline translation (`pip install argostranslate`), face recognition (deliberately **not implemented**, see ADR 0004). WhatsApp is not integrated (see ADR 0004).

## Voice on macOS

`./start_jarvis_mac.sh --daemon`. `tts_engine: auto` uses edge-tts (online) on the Mac. Alternatives: `voicebox` = local [Voicebox](https://github.com/jamiepine/voicebox) server for a cloned voice (see below), `system` = built-in `say` (offline, clearly different from the film voice), `xtts` = cloned voice. XTTS was measured on Apple Silicon: CPU real-time factor ≈ 0.6 (faster than real time), MPS ≈ 2.2 (slower), so keep `xtts_device: cpu`. Install with `./start_jarvis_mac.sh --xtts`; create the reference from your recording with `./start_jarvis_mac.sh --voice-ref "Jarvis voice.mp3"` → `voice_daemon/assets/jarvis_reference.wav` (film material, not in the repo; without it Jarvis falls back to edge-tts, then to `say`). **Voicebox** (MIT, local voice-cloning studio with REST API): install and start the Voicebox app, create a voice profile from your reference recording there, then set in `config.yaml`:

```yaml
voice:
  tts_engine: "voicebox"
  voicebox_profile: "Jarvis"      # profile name or id (empty = first profile)
  voicebox_engine: null           # optional engine choice inside Voicebox
```

Jarvis calls `POST http://127.0.0.1:17493/generate/stream` sentence by sentence (so the orb and captions stay in sync). If Voicebox is not running or errors, it falls back to edge-tts and then to `say`. `./start_jarvis_mac.sh --check` reports whether the server is reachable. German quality depends on the engine you pick in Voicebox (its page does not list German explicitly) — listen to it in the Voicebox app first.
Status: the adapter is tested against a local stand-in server that mimics the documented endpoints; it has **not** been run against a real Voicebox install.

If port 8765 is taken by another program, the app automatically uses the next free port (up to +9) and the daemon finds it.

## Project layout

```
jarvis_app/     desktop front end (pywebview), JS bridge, WebSocket server for the daemon
jarvis_core/    UI-independent core: config, persona, router, Ollama client, chat, system monitor,
                platform/ (mac + windows adapters), tools/ (registry, files, shell, system, research, …),
                toolagent.py (multi-step tool control), memory/ (SQLite, documents, reminders),
                integrations/, browser agent, briefing, cli.py, doctor.py
voice_daemon/   wake word, speech-to-text, speech output, WebSocket link
config.yaml     profiles (dev, home), orb parameters, voice and browser settings
docs/           architecture decisions (ADRs) and design specs
tests/          pytest suite
```

The app and the voice daemon communicate over a local WebSocket (`ws://127.0.0.1:8765`).

## Development

```bat
.venv\Scripts\python.exe -m pytest tests\ -q       :: Windows
.venv/bin/python -m pytest tests -q                  # macOS
```

`pytest -m "not integration"` skips the tests that start the real Chrome DevTools MCP server.

The UI can be viewed without the app by opening `jarvis_app/ui/preview.html` in a browser (mocked bridge).

## Documentation

- [CONTEXT.md](CONTEXT.md): domain terms and project state
- [docs/adr](docs/adr): architecture decision records
- [docs/superpowers/specs](docs/superpowers/specs): design specs
