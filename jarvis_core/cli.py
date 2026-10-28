"""Textlauf ohne Oberfläche:  python -m jarvis_core.cli "Erstelle einen Ordner …"

Praktisch zum Testen der Werkzeugsteuerung. Bestätigungen werden im Terminal abgefragt."""
from __future__ import annotations

import argparse
import sys
import threading

from . import config as core_config
from .runtime import build_runtime


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Jarvis ohne GUI")
    ap.add_argument("text", nargs="+")
    ap.add_argument("--config", default=str(core_config.default_config_path()))
    ap.add_argument("--profile")
    ap.add_argument("--model", help="Agent-Modell überschreiben")
    ap.add_argument("--yes", action="store_true", help="Bestätigungen automatisch ablehnen: nein; ja: --yes")
    args = ap.parse_args(argv)
    core_config.load_env_file()
    cfg = core_config.load(args.config, args.profile)
    if args.model:
        from dataclasses import replace
        cfg = replace(cfg, profile=replace(cfg.profile, agent=args.model))
    rt = build_runtime(cfg)
    text = " ".join(args.text)
    ok = True
    for ev in rt.agent.run(text):
        t = ev["type"]
        if t == "action":
            print(f"  ▸ {ev['text']}")
        elif t == "result":
            print(f"    {'✓' if ev['ok'] else '✗'} {ev['tool']}: {ev['text'][:300].replace(chr(10), ' ⏎ ')}")
        elif t == "confirm":
            print(f"  ❓ BESTÄTIGUNG: {ev['summary']}")
            ans = "j" if args.yes else input("     ausführen? [j/N] ").strip().lower()
            threading.Thread(target=rt.confirms.resolve, args=(ev["id"], ans in ("j", "ja", "y")), daemon=True).start()
        elif t == "done":
            ok = ev["ok"]
            print(f"\nJarvis: {ev['text']}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
