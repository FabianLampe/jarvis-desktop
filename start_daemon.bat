@echo off
rem J.A.R.V.I.S. Sprach-Daemon starten (benoetigt: pip install -r requirements-voice.txt)
cd /d "%~dp0"
".venv\Scripts\python.exe" -m voice_daemon.main %*
