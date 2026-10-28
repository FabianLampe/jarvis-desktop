"""J.A.R.V.I.S. Desktop-App — Einstieg.

Rahmenloses, transparentes Always-on-top-Fenster: nur der Orb ist sichtbar,
Klick öffnet das Chat-Panel. Start:  python -m jarvis_app.main [--profile dev|home]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from jarvis_core import config as core_config

log = logging.getLogger("jarvis.app")

WEBVIEW2_HINT = (
    "Microsoft Edge WebView2 Runtime wurde nicht gefunden.\n\n"
    "J.A.R.V.I.S. benötigt sie für die Oberfläche. Installation:\n"
    "1. https://developer.microsoft.com/microsoft-edge/webview2/ öffnen\n"
    "2. „Evergreen Bootstrapper“ herunterladen und ausführen\n"
    "3. J.A.R.V.I.S. erneut starten."
)


def webview2_installed() -> bool:
    """Start-Check laut Spec: verständliche Anleitung statt Absturz."""
    if sys.platform != "win32":
        return True
    import winreg

    clients_key = r"\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    candidates = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node" + clients_key),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE" + clients_key),
        (winreg.HKEY_CURRENT_USER, r"Software" + clients_key),
    ]
    for root, key in candidates:
        try:
            with winreg.OpenKey(root, key) as k:
                if winreg.QueryValueEx(k, "pv")[0] not in ("", "0.0.0.0"):
                    return True
        except OSError:
            continue
    return False


def _fail_webview2() -> None:
    log.error(WEBVIEW2_HINT)
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, WEBVIEW2_HINT,
                                         "J.A.R.V.I.S. — Voraussetzung fehlt", 0x10)
    sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="J.A.R.V.I.S. Desktop-App")
    parser.add_argument("--config", default=str(core_config.default_config_path()))
    parser.add_argument("--profile", choices=("dev", "home", "mac"),
                        help="Konfig-Profil überschreiben (Standard: auto)")
    parser.add_argument("--debug", action="store_true",
                        help="WebView-Devtools aktivieren")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s", datefmt="%H:%M:%S")

    core_config.load_env_file()
    from jarvis_core.platform import current as _cur
    _logdir = _cur().data_dir() / "logs"; _logdir.mkdir(exist_ok=True)
    _fh = logging.FileHandler(_logdir / "app.log", encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s: %(message)s", "%H:%M:%S"))
    logging.getLogger().addHandler(_fh)
    from jarvis_core import singleton
    singleton.require("jarvis_app", "J.A.R.V.I.S.")
    cfg = core_config.load(args.config, profile_override=args.profile)

    if not webview2_installed():
        _fail_webview2()

    import webview
    from .bridge import Bridge, PANEL_MIN_H, _zone

    bridge = Bridge(cfg)
    layout = bridge.load_layout()
    zone = _zone(layout["orb_px"])

    # Startposition: gespeichertes Orb-Zentrum, sonst rechts oben mit Abstand
    screen = webview.screens[0]
    if layout["cx"] is not None:
        x = max(0, min(screen.width - zone, layout["cx"] - zone // 2))
        y = max(0, min(screen.height - zone, layout["cy"] - zone // 2))
    else:
        x, y = screen.width - zone - 60, 80

    window = webview.create_window(
        title="J.A.R.V.I.S.",
        url=str(Path(__file__).parent / "ui" / "index.html"),
        js_api=bridge,
        width=zone, height=zone, x=x, y=y,
        frameless=True, easy_drag=False, transparent=True, on_top=True,
        resizable=False, shadow=False,
    )
    from jarvis_core.platform import current
    from typing import Any, cast
    gui = cast(Any, current().gui)            # cocoa (macOS) · edgechromium (Windows)
    webview.start(bridge.on_started, (window,), gui=gui, debug=args.debug, http_server=True)


if __name__ == "__main__":
    main()
