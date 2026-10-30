"""Umgebungsdiagnose:  python -m jarvis_core.doctor

Erkennt System, Python, Node, Ollama und prüft, ob die Modelle des aktiven Profils
installiert sind. Keine Annahmen über GPU/Modelle eines anderen Rechners."""
from __future__ import annotations

import importlib.util
import shutil
import sys

import requests

from . import config as core_config
from .platform import current


def suggest_profile(ram_gb: float, arm_mac: bool) -> str:
    """Grobe Modellempfehlung nach Arbeitsspeicher (Unified Memory auf Apple Silicon)."""
    if arm_mac and ram_gb >= 32:
        return "Modelle bis ~14B resident, 30B+ als Schwerlast (Profil mac)"
    if ram_gb >= 16:
        return "7–8B-Modell als Allrounder"
    return "3–4B-Modell"


def installed_models(host: str) -> list[str] | None:
    try:
        r = requests.get(host.rstrip("/") + "/api/tags", timeout=3)
        return [m["name"] for m in r.json().get("models", [])] if r.ok else None
    except requests.RequestException:
        return None


def main() -> int:
    core_config.load_env_file()
    cfg = core_config.load(core_config.default_config_path())
    pf = current()
    info = pf.system_info()
    problems = 0
    print(f"J.A.R.V.I.S. — Umgebung ({pf.name})\n" + "=" * 46)
    for k in ("os_version", "macos", "chip", "arch", "cpu_cores", "ram_gb", "ram_free_gb", "gpu", "python", "node", "ollama"):
        if info.get(k) is not None:
            print(f"  {k:12} {info[k]}")
    if sys.version_info < (3, 11):
        print("  ✗ Python ≥ 3.11 nötig"); problems += 1
    if not info.get("node"):
        print("  ○ Node.js fehlt — Browser-Steuerung (npx chrome-devtools-mcp) nicht nutzbar")
    chrome = [c for c in pf.chrome_candidates() if shutil.which(c) or __import__("os").path.exists(c)]
    print(f"  chrome       {'gefunden' if chrome else 'nicht gefunden (Browser-Steuerung)'}")
    print(f"  Empfehlung   {suggest_profile(info['ram_gb'], pf.name == 'macos' and info['arch'] == 'arm64')}")
    print(f"\nProfil: {cfg.profile.name}")
    have = installed_models(cfg.ollama_host)
    if have is None:
        print(f"  ✗ Ollama unter {cfg.ollama_host} nicht erreichbar (App starten / `ollama serve`)"); problems += 1
    else:
        wanted = {cfg.profile.general, cfg.profile.coder, cfg.profile.agent_model, cfg.profile.heavy,
                  cfg.profile.quick, cfg.profile.reasoning} - {None}
        for m in sorted(wanted):
            ok = any(h == m or h.split(":")[0] == m for h in have)
            print(f"  {'✓' if ok else '✗'} {m}" + ("" if ok else f"   → ollama pull {m}"))
            problems += 0 if ok else 1
    vb = cfg.voice.get("voicebox_url", "http://127.0.0.1:17493")
    try:
        r = requests.get(vb.rstrip("/") + "/profiles", timeout=2)
        n = len(r.json()) if r.ok and isinstance(r.json(), list) else "?"
        print(f"\nVoicebox: erreichbar unter {vb} (Profile: {n}); aktiv nur mit voice.tts_engine: voicebox")
    except requests.RequestException:
        print(f"\nVoicebox: nicht erreichbar unter {vb} (optional; tts_engine: voicebox)")
    print("\nPython-Pakete:")
    for mod, label in (("webview", "pywebview"), ("chromadb", "chromadb"), ("fastembed", "fastembed"),
                       ("markitdown", "markitdown"), ("apscheduler", "apscheduler"), ("sympy", "sympy"),
                       ("trafilatura", "trafilatura"), ("faster_whisper", "faster-whisper (Sprache)"),
                       ("openwakeword", "openwakeword (Sprache)"), ("sounddevice", "sounddevice (Sprache)")):
        print(f"  {'✓' if importlib.util.find_spec(mod) else '✗'} {label}")
    for name, hint in pf.permission_hints().items():
        print(f"  ℹ {name}: {hint}")
    print(f"\nArbeitsordner: ~/Jarvis · Daten: {pf.data_dir()}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
