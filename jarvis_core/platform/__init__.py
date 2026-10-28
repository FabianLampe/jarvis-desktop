"""Plattformauswahl: current() liefert den Adapter für das laufende System."""

from __future__ import annotations

import sys
from functools import lru_cache

from .base import PlatformAdapter, PlatformError

__all__ = ["PlatformAdapter", "PlatformError", "current", "for_name"]


def for_name(name: str) -> PlatformAdapter:
    if name == "macos":
        from .mac import MacAdapter
        return MacAdapter()
    if name == "windows":
        from .windows import WindowsAdapter
        return WindowsAdapter()
    return PlatformAdapter()


@lru_cache(maxsize=1)
def current() -> PlatformAdapter:
    return for_name({"darwin": "macos", "win32": "windows"}.get(sys.platform, "other"))
