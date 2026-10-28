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
    "qwen_python": ".venv-qwen/bin/python",   # tts_engine: qwen (MLX, eigene venv)
    "qwen_model": None,                       # Standard: mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16
    "qwen_ref_audio": None,                   # Referenz-WAV (None ⇒ xtts_speaker_wav)
    "qwen_ref_text": "",                      # exakter Text der Referenz; leer ⇒ per Whisper erkannt
    "qwen_language": "german",
    "effects_chain": [],                      # Voicebox-Format, z. B. chorus — gilt für qwen/xtts/edge/system
    "voicebox_url": "http://127.0.0.1:17493",   # tts_engine: voicebox
    "voicebox_profile": None,         # Name oder ID des Stimmprofils (None ⇒ erstes vorhandenes)
    "voicebox_effects": "profile",    # profile = Effektkette des Voicebox-Profils mitsenden · none
    "voicebox_seed": None,            # optional: fester Seed ⇒ reproduzierbarer Klang
    "voicebox_engine": None,          # optional: Voicebox-Engine pro Anfrage
    "system_voice": None,             # Systemstimme (macOS `say`), Ausweichpfad ohne Internet
    "system_rate": None,
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
    agent: Optional[str] = None   # Modell für Werkzeugaufrufe; None ⇒ general
    quick: Optional[str] = None   # kleines Modell für Chatfragen, WÄHREND Aufgaben laufen (wartet nicht am großen)

    @property
    def agent_model(self) -> str:
        return self.agent or self.general


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
class ToolsConfig:
    """Werkzeugsteuerung: Terminal, Dateien, Gedächtnis (siehe jarvis_core/tools/)."""
    enabled: bool = True
    workspace: Optional[str] = None       # freigegebener Arbeitsordner; None ⇒ ~/Jarvis
    max_steps: int = 6                    # Obergrenze Werkzeugaufrufe pro Auftrag
    shell_timeout_s: float = 60.0         # Standard-Zeitlimit eines Befehls
    max_timeout_s: float = 600.0          # härteste Obergrenze, auch auf Wunsch
    max_output_chars: int = 6000          # je Strom (stdout/stderr) an das Modell
    confirm_ttl_s: float = 120.0          # Bestätigung verfällt danach
    data_dir: Optional[str] = None        # SQLite/Chroma; None ⇒ Plattform-Standard


@dataclass(frozen=True)
class KnowledgeConfig:
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"
    chunk_chars: int = 900
    chunk_overlap: int = 150
    top_k: int = 4


@dataclass(frozen=True)
class RemindersConfig:
    timezone: str = "Europe/Berlin"
    misfire_grace_s: int = 86400          # verspätet zugestellt, solange nicht älter


@dataclass(frozen=True)
class ResearchConfig:
    searxng_url: Optional[str] = None     # optional; sonst DuckDuckGo-HTML
    max_sources: int = 4
    max_chars_per_source: int = 3000


@dataclass(frozen=True)
class AppConfig:
    profile: Profile
    orb: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_ORB))
    voice: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_VOICE))
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    briefing: BriefingConfig = field(default_factory=BriefingConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    knowledge: KnowledgeConfig = field(default_factory=KnowledgeConfig)
    reminders: RemindersConfig = field(default_factory=RemindersConfig)
    research: ResearchConfig = field(default_factory=ResearchConfig)
    integrations: dict[str, dict[str, Any]] = field(default_factory=dict)
    ollama_host: str = "http://127.0.0.1:11434"
    keep_alive: str = "30m"
    ws_host: str = "127.0.0.1"
    ws_port: int = 8765
    window_bg: str = "dark"  # dark | transparent


class ConfigError(ValueError):
    """Ungültige config.yaml (z. B. doppelte Schlüssel)."""


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader, der doppelte Schlüssel ablehnt — YAML überschreibt sie sonst still."""


def _construct_mapping(loader: _UniqueKeyLoader, node, deep=False):
    seen: set = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise ConfigError(
                f"config.yaml: doppelter Schlüssel {key!r} (Zeile {key_node.start_mark.line + 1})")
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep)


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def load_env_file(path: str | Path | None = None) -> None:
    """Liest .env (KEY=VALUE) in os.environ; vorhandene Variablen gewinnen."""
    import os
    p = Path(path) if path else repo_root() / ".env"
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip("'\""))


_FALLBACK_PROFILES = {
    "dev": {"general": "qwen3.5:0.8b", "coder": "qwen3.5:0.8b",
            "heavy": None, "gpu_monitor": False, "num_ctx": 2048},
    "home": {"general": "qwen2.5:3b", "coder": "qwen2.5:3b",
             "heavy": "qwen2.5:7b", "gpu_monitor": True, "num_ctx": 2048},
    # Apple Silicon, Unified Memory: größeres Modell möglich, kein nvidia-smi
    "mac": {"general": "qwen2.5:14b", "coder": "qwen2.5:14b", "heavy": None,
            "gpu_monitor": False, "num_ctx": 8192},
}


def default_profile_name() -> str:
    """profile: auto ⇒ mac auf macOS, sonst home (bisheriges Windows-Verhalten)."""
    import sys
    return "mac" if sys.platform == "darwin" else "home"

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
        raw = yaml.load(Path(path).read_text(encoding="utf-8"), Loader=_UniqueKeyLoader) or {}

    profiles = {**_FALLBACK_PROFILES, **(raw.get("profiles") or {})}
    name = profile_override or raw.get("profile", "auto")
    if name == "auto":
        name = default_profile_name()
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

    t = raw.get("tools") or {}
    tools = ToolsConfig(
        enabled=bool(t.get("enabled", True)), workspace=t.get("workspace"),
        max_steps=int(t.get("max_steps", 6)),
        shell_timeout_s=float(t.get("shell_timeout_s", 60)),
        max_timeout_s=float(t.get("max_timeout_s", 600)),
        max_output_chars=int(t.get("max_output_chars", 6000)),
        confirm_ttl_s=float(t.get("confirm_ttl_s", 120)),
        data_dir=t.get("data_dir"))
    k = raw.get("knowledge") or {}
    kd = KnowledgeConfig()
    knowledge = KnowledgeConfig(
        embedding_model=str(k.get("embedding_model", kd.embedding_model)),
        chunk_chars=int(k.get("chunk_chars", kd.chunk_chars)),
        chunk_overlap=int(k.get("chunk_overlap", kd.chunk_overlap)),
        top_k=int(k.get("top_k", kd.top_k)))
    rm = raw.get("reminders") or {}
    reminders = RemindersConfig(
        timezone=str(rm.get("timezone", "Europe/Berlin")),
        misfire_grace_s=int(rm.get("misfire_grace_s", 86400)))
    rs = raw.get("research") or {}
    research = ResearchConfig(
        searxng_url=rs.get("searxng_url") or None,
        max_sources=int(rs.get("max_sources", 4)),
        max_chars_per_source=int(rs.get("max_chars_per_source", 3000)))
    integrations = {str(n): dict(v or {}) for n, v in (raw.get("integrations") or {}).items()}

    return AppConfig(
        profile=Profile(
            name=name,
            general=p["general"],
            coder=p["coder"],
            heavy=p.get("heavy"),
            gpu_monitor=bool(p.get("gpu_monitor", False)),
            num_ctx=int(p.get("num_ctx", 2048)),
            agent=p.get("agent"),
            quick=p.get("quick"),
        ),
        tools=tools, knowledge=knowledge, reminders=reminders, research=research,
        integrations=integrations,
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
