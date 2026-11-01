"""Pfadauflösung und -prüfung für Dateiwerkzeuge (Leerzeichen, ~, Symlinks)."""
from __future__ import annotations

from pathlib import Path

from .registry import ToolContext, ToolError


def resolve(ctx: ToolContext, raw: str, must_exist: bool = False) -> Path:
    """Löst einen vom Modell gelieferten Pfad auf. Relative Pfade gelten im Arbeitsordner.
    Folgt Symlinks, damit ein Link aus dem Arbeitsordner heraus nicht als „innen" gilt."""
    if not isinstance(raw, str) or not raw.strip():
        raise ToolError("Kein Pfad angegeben")
    if "\x00" in raw:
        raise ToolError("Ungültiger Pfad")
    p = Path(raw.strip()).expanduser()
    if not p.is_absolute():
        p = ctx.workspace / p
    p = p.resolve(strict=False)
    if is_protected(ctx, p):
        raise ToolError(f"Zugriff verweigert: {p} ist ein geschützter Pfad")
    if must_exist and not p.exists():
        raise ToolError(f"Nicht gefunden: {p}")
    return p


def inside(base: Path, p: Path) -> bool:
    try:
        p.relative_to(base)
        return True
    except ValueError:
        return False


def in_workspace(ctx: ToolContext, p: Path) -> bool:
    return inside(ctx.workspace, p)


def _fold(p: Path) -> str:
    """APFS/NTFS sind standardmäßig case-insensitiv: ~/.SSH ist ~/.ssh."""
    import os
    s = os.path.normpath(str(p))
    # Geheimnisverzeichnisse auch unter Linux gegen Schreibweisen wie ~/.SSH sperren.
    return s.casefold()


def is_protected(ctx: ToolContext, p: Path) -> bool:
    import os
    from ..config import repo_root
    blocked = [*ctx.platform.protected_paths(), repo_root() / ".secrets", repo_root() / ".env",
               ctx.data_dir]
    target = _fold(Path(p).resolve(strict=False))
    for b in blocked:
        root = _fold(b.resolve(strict=False))
        if target == root or target.startswith(root.rstrip(os.sep) + os.sep):
            return True
    return False
