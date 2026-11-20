# Offene Punkte – was Jarvis noch können soll

Diese Liste wird bei jeder Arbeitssitzung am Projekt aktualisiert: Neues kommt dazu, Erledigtes wandert
mit Datum nach unten unter „Erledigt“. Prioritäten: **Hoch** = stört im Alltag oder blockiert etwas,
**Mittel** = deutlicher Gewinn, **Niedrig** = nett zu haben.

Stand: 2026-11-20

## Entscheidungen, die Fabian treffen muss

- [ ] **Bildgenerierung einschalten?** Programm-Umgebung `.venv-mflux` (1,3 GB) liegt bereit. Es fehlt das
      Modell: ca. 34 GB (FLUX schnell, beste Qualität) oder ca. 6 GB (Z-Image-Turbo 4-bit). Neben gemma4 wird
      der Arbeitsspeicher knapp. Danach `image_generation.enabled: true` in `config.yaml`.
- [ ] **Kurzbefehle ohne Rückfrage:** Welche Kurzbefehle dürfen direkt laufen? → `shortcuts.allowed` in
      `config.yaml` (z. B. ein Kurzbefehl „Nicht stören an“).

## Einrichtung auf dem Mac (einmalig)

- [ ] Kamerafreigabe für Terminal (Systemeinstellungen → Datenschutz & Sicherheit → Kamera)
- [ ] Bedienungshilfen für Terminal (… → Bedienungshilfen) – für „Tippe in …“ und Mac-Apps bedienen
- [ ] Automation für Musik, Notizen, System Events (macOS fragt beim ersten Mal)
- [ ] Ortungsdienste für Terminal – sonst bleibt der WLAN-Name in der Netzwerkübersicht verborgen
- [ ] Kurzbefehl für „Nicht stören“ anlegen (macOS bietet dafür keinen direkten Befehl)

## Noch nicht live getestet

- [ ] Kamerafoto und Gesichtsanalyse (`camera_look`) – wartet auf Kamerafreigabe
- [ ] Kamera beobachten (`camera_watch_start`) mit echter Bewegung und Sprachmeldung
- [ ] Kurzbefehl tatsächlich ausführen (`shortcut_run`)
- [ ] Apple Music abspielen, Suche in der echten Mediathek (`music_control`)
- [ ] Notiz anlegen (`notes_create`)
- [ ] In eine App tippen (`app_type_text`) inkl. Abbruch, wenn die App nicht nach vorne kommt
- [ ] Wächter in der laufenden App: Meldung bei Termin/Platte/Akku und per Stimme
- [ ] Bild in den Chat einfügen/hineinziehen in der echten Oberfläche

## Fähigkeiten – offen

### Hoch
- [ ] **Mail** (Gmail): Mails suchen, zusammenfassen, Entwürfe schreiben; Senden nur mit Bestätigung.
      Bisher nur Lesen fürs Tagesbriefing. *Bewusst zurückgestellt (2026-11-10).*
- [ ] **Webseiten mit JavaScript lesen:** Recherche liest solche Seiten nicht, seit der Browser-Ausweg aus
      Sicherheitsgründen entfernt wurde. Sichere Variante: Browser mit derselben Prüfung auf öffentliche
      Adressen (Anfragen an LAN/localhost abfangen, Ziel-URL nach dem Laden prüfen).
- [ ] **Mehrstufige Aufgaben schneller:** 30–50 s, weil bei mehreren Arbeitsschritten automatisch das
      Denk-Modell (qwen3.8) eingewechselt wird. Regel in `jarvis_core/router.py` (`wants_reasoning`) feiner fassen.

### Mittel
- [ ] **Nachrichten senden** (iMessage/WhatsApp) – geht derzeit nur über einen eigenen Kurzbefehl.
- [ ] **Smart Home direkt:** Home Assistant ist eingebaut, aber nicht eingerichtet (`JARVIS_HA_URL`,
      `JARVIS_HA_TOKEN`); HomeKit nur über Kurzbefehle.
- [ ] **Spotify** einrichten (`JARVIS_SPOTIFY_CLIENT_ID` + Login) – Apple Music funktioniert ohne Konto.
- [ ] **Apple Erinnerungen/Kalender-App** statt nur eigener Erinnerungen und Google Kalender.
- [ ] **Kurzbefehl-Ausgabe:** Manche Kurzbefehle liefern ihr Ergebnis nicht auf stdout; dann meldet Jarvis
      nur „ausgeführt“.
- [ ] **„Was läuft gerade?“ ist doppeldeutig** (Musik oder laufende Aufgaben) – Rückfrage oder Kontext nutzen.
- [ ] **Telegram/Discord** sind eingebaut, aber aus.

### Niedrig
- [ ] Gesten über die Kamera (bewusst weggelassen; datenschutzseitig heikel).
- [ ] Offline-Übersetzung (argostranslate) einrichten.
- [ ] Netzwerk-Wächter meldet in großen, geteilten Netzen (> 64 Geräte) bewusst nichts – zu Hause prüfen.

## Bewusst nicht

- Gesichtserkennung/Identifizierung von Personen (biometrische Daten).
- Automatische Modell-Downloads ohne Rückfrage.

## Erledigt

### 2026-11-20
- Kamerafoto mit Bild-/Gesichtsauswertung, Bilder in den Chat einfügen, Skripte schreiben + in VS Code öffnen
- Apple Kurzbefehle, in Mac-Apps tippen, Programmier-Projekte (venv, Pakete, Commit, Push)
- Apple Music, Bildgenerierung (vorbereitet, aus), Kamera beobachten (Bewegung)
- Wetter-Rat, Vorlieben selbst merken, Spotlight-Suche, Systemaktionen, Apple Notizen, Einheiten, Timer

### 2026-11-10
- Werkzeugauswahl durch das Modell, Schrittlimit 12
- Netzwerk (Überblick, Geräte, Speedtest, Ports), Rechner-Diagnose, Bildschirm ansehen
- Recherche mit Quellenbewertung, Schutz vor Prompt Injection, Wächter (Gerät/Termin/Platte/Akku)
- Kurze Antworten als Standard
