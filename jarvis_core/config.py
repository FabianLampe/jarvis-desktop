"""Konfiguration mit Profilen (dev = Arbeits-PC, home = Ziel-PC aus den ADRs)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

DEFAULT_ORB: dict[str, Any] = {
    # Abgesegnete Engine-Parameter (Design-Spec 2026-06-11, Seed 5)
    "sparks": 250,
    "veins": 25,
    "rays": 4,
    "ring_brightness": 0.5,
    "bloom": 0.9,
    "speed": 0.7,
    "seed": 5,
    "size_px": 240,
    "backdrop": 0.85,  # dunkler Hof hinter dem Orb (helle Wallpaper), 0 = aus
}

DEFAULT_VOICE: dict[str, Any] = {
    # Sprach-Daemon — Wake Word akzent-robust via openWakeWord, STT fest Deutsch
    "wake_word": "hey_jarvis",        # openWakeWord-Modellname
    "wake_threshold": 0.55,           # 0–1; niedriger = empfindlicher
    "interrupt_threshold": 0.32,      # Barge-in-Schwelle, während Jarvis spricht
    "stt_model": "small",             # faster-whisper-Modell (CPU)
    "stt_language": "de",
    "stt_compute": "int8",
    "tts_engine": "edge",             # edge (schnell) | xtts (Stimm-Klon, CPU-langsam)
    "tts_voice": "de-DE-FlorianMultilingualNeural",
    "tts_rate": "-8%",                # ruhiges Sprechtempo (Film-Jarvis)
    "tts_pitch": "-3Hz",              # leicht abgesenkte Tonlage
    "robotic": 0.0,                   # 0–1: synthetischer Roboter-Anstrich der Stimme
    "robotic_carrier_hz": 55.0,       # Trägerfrequenz der Ringmodulation (tief = grollend)
    "tts_max_chars": 1200,            # längere Antworten werden gekürzt vorgelesen
    "xtts_speaker_wav": "voice_daemon/assets/jarvis_reference.wav",
    "xtts_device": "auto",            # auto | cuda | cpu (cuda: ~10× schnellere Synthese)
    "input_device": None,             # None = Standardgerät (Index/Name möglich)
    "output_device": None,
    "beep": True,                     # kurzer Ton, wenn Jarvis zuhört
    "max_utterance_s": 14.0,
    "silence_after_s": 0.9,
    "follow_up_s": 6.0,               # Gesprächsmodus: nach jeder Antwort so lange
                                      # weiterzuhören, ohne neues „Hey Jarvis" (0 = aus)
}


@dataclass(frozen=True)
class Profile:
    name: str
    general: str
    coder: str
    heavy: Optional[str]  # None ⇒ Schwerlast nicht verfügbar (dev-PC)
    gpu_monitor: bool
    num_ctx: int = 2048


@dataclass(frozen=True)
class BrowserConfig:
    enabled: bool = True
    profile_dir: str = ".chrome-jarvis"   # dauerhaftes, isoliertes Jarvis-Profil
    channel: str = "stable"
    headless: bool = False
    max_steps: int = 6                    # Obergrenze Werkzeug-Schritte pro Anfrage
    search_engine: str = "google"


@dataclass(frozen=True)
class BriefingConfig:
    github_repo: str = "FabianLampe/jarvis-desktop"
    github_issue_limit: int = 20
    gmail_enabled: bool = True
    gmail_max: int = 10
    weather_lat: float = 53.55
    weather_lon: float = 9.99
    weather_ort: str = "Hamburg"


@dataclass(frozen=True)
class AppConfig:
    profile: Profile
    orb: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_ORB))
    voice: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_VOICE))
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    briefing: BriefingConfig = field(default_factory=BriefingConfig)
    ollama_host: str = "http://127.0.0.1:11434"
    keep_alive: str = "30m"
    ws_host: str = "127.0.0.1"
    ws_port: int = 8765
    window_bg: str = "dark"  # dark | transparent


_FALLBACK_PROFILES = {
    "dev": {"general": "qwen3.5:0.8b", "coder": "qwen3.5:0.8b",
            "heavy": None, "gpu_monitor": False, "num_ctx": 2048},
    "home": {"general": "qwen2.5:3b", "coder": "qwen2.5:3b",
             "heavy": "qwen2.5:7b", "gpu_monitor": True, "num_ctx": 2048},
}

DEFAULT_BROWSER: dict[str, Any] = {
    "enabled": True,
    "profile_dir": ".chrome-jarvis",
    "channel": "stable",
    "headless": False,
    "max_steps": 6,
    "search_engine": "google",
}

DEFAULT_BRIEFING: dict[str, Any] = {
    "github_repo": "FabianLampe/jarvis-desktop",
    "github_issue_limit": 20,
    "gmail_enabled": True,
    "gmail_max": 10,
    "weather": {"lat": 53.55, "lon": 9.99, "ort": "Hamburg"},
}


def load(path: str | Path | None = None,
         profile_override: str | None = None) -> AppConfig:
    """Lädt config.yaml; fehlende Werte werden mit sinnvollen Defaults gefüllt."""
    raw: dict[str, Any] = {}
    if path is not None and Path(path).is_file():
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}

    profiles = {**_FALLBACK_PROFILES, **(raw.get("profiles") or {})}
    name = profile_override or raw.get("profile", "home")
    if name not in profiles:
        raise ValueError(f"Unbekanntes Profil {name!r} — verfügbar: {sorted(profiles)}")
    p = {**_FALLBACK_PROFILES.get(name, {}), **(profiles[name] or {})}

    orb = {**DEFAULT_ORB, **(raw.get("orb") or {})}
    voice = {**DEFAULT_VOICE, **(raw.get("voice") or {})}
    ollama = raw.get("ollama") or {}
    ws = raw.get("voice_ws") or {}

    window_bg = str(raw.get("window_bg", "dark")).lower()
    if window_bg not in ("dark", "transparent"):
        raise ValueError(f"window_bg muss dark|transparent sein, nicht {window_bg!r}")

    b = {**DEFAULT_BROWSER, **(raw.get("browser") or {})}
    browser = BrowserConfig(
        enabled=bool(b["enabled"]), profile_dir=str(b["profile_dir"]),
        channel=str(b["channel"]), headless=bool(b["headless"]),
        max_steps=int(b["max_steps"]), search_engine=str(b["search_engine"]),
    )

    br = {**DEFAULT_BRIEFING, **(raw.get("briefing") or {})}
    w = {**DEFAULT_BRIEFING["weather"], **(br.get("weather") or {})}
    briefing = BriefingConfig(
        github_repo=str(br["github_repo"]), github_issue_limit=int(br["github_issue_limit"]),
        gmail_enabled=bool(br["gmail_enabled"]), gmail_max=int(br["gmail_max"]),
        weather_lat=float(w["lat"]), weather_lon=float(w["lon"]), weather_ort=str(w["ort"]),
    )

    return AppConfig(
        profile=Profile(
            name=name,
            general=p["general"],
            coder=p["coder"],
            heavy=p.get("heavy"),
            gpu_monitor=bool(p.get("gpu_monitor", False)),
            num_ctx=int(p.get("num_ctx", 2048)),
        ),
        orb=orb,
        voice=voice,
        browser=browser,
        briefing=briefing,
        ollama_host=str(ollama.get("host", "http://127.0.0.1:11434")).rstrip("/"),
        keep_alive=str(ollama.get("keep_alive", "30m")),
        ws_host=str(ws.get("host", "127.0.0.1")),
        ws_port=int(ws.get("port", 8765)),
        window_bg=window_bg,
    )


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def default_config_path() -> Path:
    return repo_root() / "config.yaml"
