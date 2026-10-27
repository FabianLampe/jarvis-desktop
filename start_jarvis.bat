@echo off
rem J.A.R.V.I.S. starten (venv erforderlich: py -3.11 -m venv .venv + pip install -r requirements.txt)
cd /d "%~dp0"
".venv\Scripts\python.exe" -m jarvis_app.main %*
