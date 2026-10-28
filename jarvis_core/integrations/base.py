"""Gemeinsame Basis der einzeln aktivierbaren Integrationen.

Jede Integration meldet einen Einrichtungsstatus (disabled | needs_setup | ready | error)
mit verständlicher Anleitung. Es gibt keine Beispieldaten: ohne Zugangsdaten schlägt ein
Werkzeug mit der Einrichtungsanleitung fehl, statt etwas vorzutäuschen."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..config import repo_root
from ..tools.registry import ToolError


def secrets_dir() -> Path:
    return repo_root() / ".secrets"


@dataclass
class Status:
    state: str                        # disabled | needs_setup | ready | error
    message: str
    missing: list[str] = field(default_factory=list)

    def line(self, name: str) -> str:
        extra = f" (fehlt: {', '.join(self.missing)})" if self.missing else ""
        return f"{name}: {self.state} — {self.message}{extra}"


class Integration:
    name = ""
    label = ""
    env: tuple[str, ...] = ()
    setup_hint = ""

    def __init__(self, settings: Optional[dict[str, Any]] = None):
        self.settings = settings or {}

    @property
    def enabled(self) -> bool:
        return bool(self.settings.get("enabled", False))

    def missing(self) -> list[str]:
        return [v for v in self.env if not os.environ.get(v)]

    def local_setup_status(self) -> Optional[Status]:
        """Zusätzliche Dateien/Logins prüfen. None ⇒ vollständig."""
        return None

    def probe(self) -> Status:
        """Echter, leichter Verbindungstest (nur bei vollständiger Einrichtung)."""
        return Status("ready", "Zugangsdaten vorhanden")

    def status(self, probe: bool = True) -> Status:
        if not self.enabled:
            return Status("disabled", f"abgeschaltet — in config.yaml integrations.{self.name}.enabled: true setzen")
        if (miss := self.missing()):
            return Status("needs_setup", self.setup_hint, miss)
        if (st := self.local_setup_status()) is not None:
            return st
        if not probe:
            return Status("ready", "konfiguriert (nicht geprüft)")
        try:
            return self.probe()
        except Exception as exc:  # noqa: BLE001
            return Status("error", f"{type(exc).__name__}: {exc}"[:200])

    def require_ready(self) -> None:
        st = self.status(probe=False)
        if st.state != "ready":
            raise ToolError(f"{self.label} ist nicht einsatzbereit — {st.line(self.label)}")
