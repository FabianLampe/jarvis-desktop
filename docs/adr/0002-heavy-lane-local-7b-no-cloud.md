# Schwerlast-Spur: lokales 7B on-demand, keine Cloud

**Status:** accepted

Für Aufgaben, die das schnelle 3B-Paar (siehe [ADR 0001](0001-local-model-strategy.md)) nicht gut kann
— **Deep Research, hartes Coding/Reasoning** — wird **lokal ein 7B-Modell** genutzt (`qwen2.5:7b`,
optional zusätzlich `qwen2.5-coder:7b`). Da zwei 3B + ein 7B nicht zusammen in die 6 GB passen, wird
das **3B-Paar für die Dauer der schweren Aufgabe ausgelagert**, das 7B geladen, danach das Paar
wieder zurückgeladen. **Cloud/API wird bewusst ganz ausgeschlossen** — auch nicht als abgeschalteter
Reserve-Pfad —, weil Null-Kosten und vollständige lokale Datenhaltung Vorrang haben.

## Konsequenzen

- Schwere Aufgaben haben ~5 s Vorlauf (Auslagern + Laden) und laufen mit ~17 Tok/s (7B mit CPU-Offload).
- Während einer schweren Aufgabe ist die schnelle Spur unterbrochen (3B-Paar nicht resident).
- Qualität ist auf **lokales 7B-Niveau** gedeckelt — keine Frontier-Modell-Qualität für Research/Coding.
- Umkehrbar: Ein API-Provider ließe sich später ergänzen, falls sich die Anforderung ändert.
- `qwen2.5-coder:7b` ist noch **nicht installiert** (zusätzlicher ~4,7-GB-Download), falls hartes Coding Priorität bekommt.
