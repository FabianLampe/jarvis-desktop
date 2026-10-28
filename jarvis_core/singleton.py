"""Einzelinstanz-Schutz: ein Prozess je Name (App, Daemon). Sperre fällt beim Beenden automatisch weg."""
from __future__ import annotations

import atexit
import fcntl
import os
import sys
from pathlib import Path

_HELD = []


def acquire(name: str, lock_dir: Path | None = None) -> bool:
    """True, wenn diese Instanz die Sperre erhält; False, wenn bereits eine läuft."""
    from .platform import current
    d = lock_dir or current().data_dir() / "locks"
    d.mkdir(parents=True, exist_ok=True)
    fh = open(d / f"{name}.lock", "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return False
    fh.write(str(os.getpid())); fh.flush()
    _HELD.append(fh)                       # offen halten, sonst fällt die Sperre
    atexit.register(fh.close)
    return True


def require(name: str, label: str) -> None:
    """Beendet den Prozess mit klarer Meldung, wenn schon eine Instanz läuft."""
    if not acquire(name):
        print(f"{label} läuft bereits — nichts doppelt gestartet. "
              f"(Beenden: pkill -f {name}; danach neu starten)", file=sys.stderr)
        sys.exit(1)
