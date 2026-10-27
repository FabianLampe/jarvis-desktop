# Lokale Modell-Strategie: zwei dauerhaft geladene 3B-Spezialisten statt 7B-Tausch

**Status:** accepted

Auf der Ziel-Hardware (GTX 1660 Ti, **6 GB VRAM**) wurde gemessen: ein 7B-Modell passt nie ganz in den VRAM (Windows belegt ~0,8 GB), lagert immer ~11 % auf die CPU aus und bleibt bei **~17 Tok/s**; zwei 7B gleichzeitig sind unmöglich, ein Modellwechsel kostet 4–14 s. Zwei **3B-Modelle** (`qwen2.5:3b` als Allrounder/Router + `qwen2.5-coder:3b` fürs Programmieren) passen dagegen **gemeinsam zu 100 % auf die GPU** (5,4/6 GB belegt), laufen mit **86,9 Tok/s** und bleiben mit `OLLAMA_MAX_LOADED_MODELS=2` beide resident — dadurch wechselt Jarvis die Aufgabe **ohne jede Ladepause**.

Entschieden: **Zwei dauerhaft geladene 3B-Spezialisten**, ausgewählt durch einen leichtgewichtigen Router. Damit wird „pro Aufgabe ein anderes Modell" sofortig statt mit Sekundenpause eingelöst — der ursprüngliche Wunsch (mehrere 7B lokal parallel) ist auf dieser GPU physisch nicht realisierbar.

## Betrachtete Alternativen

- **7B-Spezialist pro Aufgabe, getauscht** — verworfen: 17 Tok/s *und* 4–14 s Pause bei jedem Themenwechsel.
- **Ein einziger 7B-Allrounder** — verworfen: gibt „anderes Modell pro Aufgabe" auf und bleibt bei 17 Tok/s.
- **API-Hybrid** — verworfen wegen Kosten; Cloud später komplett ausgeschlossen (siehe [ADR 0002](0002-heavy-lane-local-7b-no-cloud.md)).

## Konsequenzen

- Lokale Qualität ist auf 3B-Niveau gedeckelt — für **hartes Coding / Deep Research** reicht das oft nicht; dafür existiert eine separate „Schwerlast-Spur" (eigene ADR folgt).
- VRAM-Decke: **zwei Modelle bei je 2048 Kontext** (~559 MB Rest). Ein drittes Modell oder größerer Kontext sprengt die 6 GB → erzwingt wieder CPU-Offload.
- Modelle gehören auf **F: (NVMe)**, nicht C: (SATA) — Kaltstart fällt von ~14 s auf ~3–5 s.
- Gesetzte System-Defaults: `OLLAMA_MAX_LOADED_MODELS=2`, `OLLAMA_KEEP_ALIVE=30m`.
