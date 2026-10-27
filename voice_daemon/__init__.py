"""voice_daemon — Ohren und Stimme von J.A.R.V.I.S. (eigenständiger Prozess).

Always-on Wake Word („Hey Jarvis", openWakeWord auf CPU) → deutsche STT
(faster-whisper, language=de) → WebSocket zur Desktop-App → Antwort als
deutsche TTS (edge-tts) mit Live-Amplituden für den Orb.
"""
