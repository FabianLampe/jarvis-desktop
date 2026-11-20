# Jarvis Desktop – Hinweise für die Arbeit am Projekt

- **Offene Punkte pflegen:** `docs/OFFENE-PUNKTE.md` ist die Liste dessen, was Jarvis noch können soll.
  Bei JEDER Arbeitssitzung am Ende aktualisieren: neue Wünsche und gefundene Lücken eintragen,
  Erledigtes mit Datum unter „Erledigt“ verschieben, „Stand:“ aktualisieren.
- Tests: `caffeinate -i .venv/bin/python -m pytest -q -p no:cacheprovider` (caffeinate, sonst schläft der Mac ein).
- Neue Werkzeuge: Muster in `docs/werkzeug-erweiterungen.md`; Policy gehört in `assess`, fremder Text durch
  `wrap_untrusted`/`clean`, AppleScript nur über `jarvis_core/tools/mac_script.py` mit Werten als argv.
- Antworten von Jarvis sind standardmäßig kurz (Persona + Agent-Regeln); Texte deutsch, Anrede „Sir“.
