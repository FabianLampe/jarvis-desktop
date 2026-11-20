"""Bilder erzeugen (lokal, Apple Silicon): mflux = FLUX-Modelle auf MLX.

Standardmäßig AUS (config image_generation.enabled). Die Modellgewichte sind groß und werden NIE automatisch
geladen: Der Prozess läuft mit HF_HUB_OFFLINE=1, fehlen die Gewichte, gibt es eine Einrichtungsanleitung statt
eines stillen Riesen-Downloads. Die Erzeugung läuft in einem eigenen Prozess mit Zeitlimit — Arbeitsspeicher ist
mit den Ollama-Modellen knapp, ein Absturz darf Jarvis nicht mitreißen. Der Prompt kommt als `--prompt=…`
(ein argv-Element, nie über eine Shell)."""
from __future__ import annotations

import re
import shutil
import sys
import time
from pathlib import Path
from typing import Optional

from ..config import repo_root
from .registry import ALLOW, CONFIRM, Assessment, InvalidArgs, Tool, ToolContext, ToolError, ToolResult
from .shell_tools import _runner

TIMEOUT_S = 600.0
MAX_PROMPT = 1000
SIZES = {"klein": (512, 512), "mittel": (768, 768), "gross": (1024, 1024), "groß": (1024, 1024),
         "quadrat": (768, 768), "hochformat": (640, 1024), "querformat": (1024, 640)}
DEFAULT_SIZE = (768, 768)

# Bekannte Kurznamen → Name des Hugging-Face-Cache-Ordners (Vorprüfung auf vorhandene Gewichte) und Downloadgröße.
KNOWN = {
    "schnell": ("models--black-forest-labs--FLUX.1-schnell", "etwa 34 GB"),
    "dev": ("models--black-forest-labs--FLUX.1-dev", "etwa 34 GB, zusätzlich Zustimmung auf huggingface.co"),
    "z-image-turbo": ("models--Tongyi-MAI--Z-Image-Turbo", "etwa 25 GB"),
    "filipstrand/Z-Image-Turbo-mflux-4bit": ("models--filipstrand--Z-Image-Turbo-mflux-4bit", "etwa 6 GB"),
}


def hf_cache() -> Path:
    import os
    base = os.environ.get("HF_HUB_CACHE") or (Path(os.environ.get("HF_HOME") or Path.home() / ".cache" / "huggingface") / "hub")
    return Path(base)


def find_command(ctx: ToolContext) -> Optional[str]:
    cfg = ctx.cfg.image_generation
    candidates = [cfg.command] if cfg.command else []
    candidates += [str(repo_root() / ".venv-mflux" / "bin" / "mflux-generate"),
                   str(Path(sys.executable).parent / "mflux-generate")]
    for c in candidates:
        if c and Path(c).expanduser().is_file():
            return str(Path(c).expanduser())
    return shutil.which("mflux-generate")


def setup_help(ctx: ToolContext, reason: str) -> str:
    cfg = ctx.cfg.image_generation
    size = KNOWN.get(cfg.model, (None, "mehrere GB"))[1]
    return (f"{reason} Einrichtung (einmalig): 1) Umgebung: python3 -m venv .venv-mflux && "
            f".venv-mflux/bin/pip install mflux (rund 1,3 GB). 2) Modell „{cfg.model}“ laden — Download {size}, "
            f"zum Beispiel mit: .venv-mflux/bin/mflux-generate --model {cfg.model} --quantize {cfg.quantize} "
            f"--steps {cfg.steps} --prompt \"Test\" (lädt beim ersten Lauf). Ein kleinerer Einstieg: model "
            "filipstrand/Z-Image-Turbo-mflux-4bit (etwa 6 GB). 3) In config.yaml image_generation.enabled: true setzen. "
            "Hinweis: Die Erzeugung braucht viel Arbeitsspeicher und läuft neben den Ollama-Modellen langsam; "
            "am besten währenddessen nichts anderes Großes starten.")


def parse_size(raw) -> tuple[int, int]:
    text = str(raw or "").strip().casefold()
    if not text:
        return DEFAULT_SIZE
    if text in SIZES:
        return SIZES[text]
    m = re.fullmatch(r"(\d{3,4})\s*[x×]\s*(\d{3,4})", text)
    if not m:
        raise InvalidArgs("size: z. B. 768x768, 1024x640 oder klein, mittel, groß, hochformat, querformat.")
    w, h = int(m.group(1)), int(m.group(2))
    if not (256 <= w <= 1024 and 256 <= h <= 1024) or w % 16 or h % 16:
        raise InvalidArgs("Die Bildgröße muss zwischen 256 und 1024 Pixeln liegen und durch 16 teilbar sein.")
    return w, h


def _exec(ctx: ToolContext, argv: list[str], cwd: Path):
    """Einziger Prozessstart (Tests ersetzen ihn). Offline-Modus: es wird nie heruntergeladen."""
    return _runner(ctx).run(argv, cwd, env={"HF_HUB_OFFLINE": "1"}, timeout_s=TIMEOUT_S, max_chars=4000, cancel=ctx.cancel)


def _open(path: Path) -> None:
    import subprocess
    try:
        subprocess.run(["open", str(path)], check=False, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


def _weights_present(model: str) -> bool:
    entry = KNOWN.get(model)
    if entry is None:
        if "/" in model and not model.startswith(("/", ".", "~")):
            entry = ("models--" + model.replace("/", "--"), "")
        else:
            return True                  # unbekannter Name oder lokaler Pfad: der Offline-Lauf entscheidet
    return (hf_cache() / entry[0]).is_dir()


def image_generate(ctx: ToolContext, a: dict) -> ToolResult:
    cfg = ctx.cfg.image_generation
    if not cfg.enabled:
        raise ToolError(setup_help(ctx, "Die Bilderzeugung ist ausgeschaltet."))
    prompt = str(a.get("prompt", "")).strip()
    if not prompt or len(prompt) > MAX_PROMPT:
        raise InvalidArgs(f"Der Bildwunsch muss zwischen 1 und {MAX_PROMPT} Zeichen lang sein.")
    width, height = parse_size(a.get("size"))
    command = find_command(ctx)
    if command is None:
        raise ToolError(setup_help(ctx, "Das Programm mflux-generate ist nicht installiert."))
    if not _weights_present(cfg.model):
        raise ToolError(setup_help(ctx, f"Die Modellgewichte für „{cfg.model}“ sind noch nicht geladen."))
    folder = ctx.workspace / "Bilder"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / time.strftime("generiert-%Y%m%d-%H%M%S.png")
    argv = [command, "--model", cfg.model, "--quantize", str(cfg.quantize), "--steps", str(cfg.steps),
            "--width", str(width), "--height", str(height), f"--prompt={prompt}", "--output", str(target)]
    res = _exec(ctx, argv, folder)
    if res.cancelled:
        raise ToolError("Die Bilderzeugung wurde abgebrochen.")
    if res.timed_out:
        raise ToolError("Die Bilderzeugung hat zu lange gedauert (Zeitlimit 10 Minuten) und wurde beendet — "
                        "vermutlich ist der Arbeitsspeicher knapp; ein kleineres Bild oder weniger offene Programme helfen.")
    if not res.ok or not target.exists() or target.stat().st_size == 0:
        tail = (res.stderr or res.stdout or res.error or "").strip().splitlines()
        last = (tail[-1] if tail else "keine Fehlermeldung")[:200]
        if re.search(r"offline|cache|not found|LocalEntry|connect", " ".join(tail), re.I):
            raise ToolError(setup_help(ctx, "Die Modellgewichte fehlen oder sind unvollständig."))
        raise ToolError("Die Bilderzeugung ist fehlgeschlagen: " + re.sub(r"[^\w .,:;/()\-]", "", last))
    if a.get("open", True):
        _open(target)
    return ToolResult(True, f"Bild erzeugt ({width}×{height}): {target}", {"path": str(target), "sources": []})


def _assess(ctx: ToolContext, a: dict) -> Assessment:
    parse_size(a.get("size"))
    if ctx.tainted:
        return Assessment(CONFIRM, "BILD ERZEUGEN (nach Lesen fremder Inhalte; belegt Minuten lang Rechner und Speicher): "
                                   + str(a.get("prompt", ""))[:120])
    return Assessment(ALLOW)


def tools() -> list[Tool]:
    S = {"type": "string"}
    return [Tool("image_generate", "imagegen",
                 "Erzeugt ein Bild aus einer Beschreibung (lokal, mflux/FLUX) und speichert es in ~/Jarvis/Bilder. "
                 "Dauert etwa ein bis zwei Minuten. prompt am besten englisch und konkret. size und open sind optional "
                 "(Standard 768x768, Bild wird geöffnet) — nicht nachfragen. size: z. B. 768x768, klein, groß, "
                 "hochformat, querformat. open=false öffnet das Bild nicht.",
                 {"prompt": S, "size": S, "open": {"type": "boolean"}}, ("prompt",), image_generate, _assess,
                 announce=lambda a: "Ich erzeuge das Bild, Sir. Das dauert einen Moment.")]
