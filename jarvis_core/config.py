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
    "speech_rate": 1.0,               # Sprechtempo (tonhöhentreu), < 1 = langsamer
    "sentence_start_rate": None,      # Satzanfang noch ruhiger (z. B. 0.82), gleitet ins Grundtempo
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
    "tts_max_chars": 12000,
    "filler_enabled": True,           # Denkpausen > 4 s mit geprüften Fakten/echtem Wetter füllen            # längere Antworten werden gekürzt vorgelesen
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
    reasoning: Optional[str] = None  # explizit gewähltes Thinking-Modell; None ⇒ bisheriges Routing

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
    router: str = "keywords"              # auto = ohne Stichworttreffer wählt das Modell die Werkzeuge
    editor: str = "Visual Studio Code"    # Standard-Editor für open_in_editor

    def __post_init__(self):
        if self.router not in ("auto", "keywords"):
            raise ConfigError("tools.router muss auto|keywords sein")


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
    max_chars_per_source: int = 4500
    only_trusted: bool = False            # true = ausschließlich etablierte Quellen lesen
    trusted_domains: tuple[str, ...] = () # eigene Ergänzungen zur eingebauten Liste
    blocked_domains: tuple[str, ...] = () # werden nie gelesen


@dataclass(frozen=True)
class NetworkConfig:
    vendor_lookup: bool = True            # Herstellerliste (IEEE-OUI) einmalig laden und lokal cachen


@dataclass(frozen=True)
class ProactiveConfig:
    """Wächter: Jarvis meldet sich von selbst (jarvis_core/proactive.py)."""
    enabled: bool = False
    interval_min: int = 15                # Netz, Platte, Akku; Termine werden jede Minute geprüft
    new_devices: bool = True
    calendar: bool = True
    calendar_lead_min: int = 15
    disk: bool = True
    disk_warn_pct: int = 90
    battery: bool = True
    battery_warn_pct: int = 15

    def __post_init__(self):
        if not 1 <= self.interval_min <= 1440 or not 1 <= self.calendar_lead_min <= 240:
            raise ConfigError("proactive: interval_min 1–1440, calendar_lead_min 1–240")
        if not 50 <= self.disk_warn_pct <= 99 or not 1 <= self.battery_warn_pct <= 50:
            raise ConfigError("proactive: disk_warn_pct 50–99, battery_warn_pct 1–50")


@dataclass(frozen=True)
class ShortcutsConfig:
    """Apple Kurzbefehle: nur Namen in `allowed` laufen ohne Rückfrage (und nur, wenn kein Fremdinhalt gelesen wurde)."""
    allowed: tuple[str, ...] = ()


@dataclass(frozen=True)
class ImageGenConfig:
    """Bilder erzeugen (mflux, Apple Silicon). Aus, bis Sir die Modellgewichte bewusst geladen hat."""
    enabled: bool = False
    model: str = "schnell"
    quantize: int = 4
    steps: int = 4
    command: Optional[str] = None         # Pfad zu mflux-generate; None ⇒ .venv-mflux, .venv, PATH

    def __post_init__(self):
        if self.quantize not in (3, 4, 5, 6, 8):
            raise ConfigError("image_generation.quantize muss 3, 4, 5, 6 oder 8 sein")
        if not 1 <= self.steps <= 50:
            raise ConfigError("image_generation.steps muss zwischen 1 und 50 liegen")
        if not self.model or self.model.startswith("-"):
            raise ConfigError("image_generation.model ungültig")


@dataclass(frozen=True)
class MemoryConfig:
    auto_learn: bool = True               # Aussagen wie „ich heiße …“ / „ich wohne in …“ selbst merken


@dataclass(frozen=True)
class ExperienceConfig:
    startup_animation: bool = True
    startup_briefing: bool = True
    music_file: Optional[str] = None
    music_volume: float = 0.28
    music_duck_volume: float = 0.07
    open_on_start: tuple[str, ...] = ()     # http(s)-Links, die nach dem Systemstart im Browser aufgehen


@dataclass(frozen=True)
class ReasoningConfig:
    mode: str = "auto"          # auto | on | off (in YAML in Anführungszeichen)
    max_tokens: int = 2048      # Denken UND Antwort zusammen, je Modellaufruf
    timeout_s: float = 120.0    # Generierungsbudget je Chat / über alle Tool-Modellaufrufe

    def __post_init__(self):
        import math
        if self.mode not in ("auto", "on", "off"):
            raise ConfigError("reasoning.mode muss auto|on|off sein")
        if not 128 <= self.max_tokens <= 32768:
            raise ConfigError("reasoning.max_tokens muss zwischen 128 und 32768 liegen")
        if not math.isfinite(self.timeout_s) or not 1 <= self.timeout_s <= 600:
            raise ConfigError("reasoning.timeout_s muss zwischen 1 und 600 liegen")


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
    experience: ExperienceConfig = field(default_factory=ExperienceConfig)
    ollama_host: str = "http://127.0.0.1:11434"
    keep_alive: str = "30m"
    ws_host: str = "127.0.0.1"
    ws_port: int = 8765
    window_bg: str = "dark"  # dark | transparent
    reasoning: ReasoningConfig = field(default_factory=ReasoningConfig)
    extensions: dict[str, Any] = field(default_factory=dict)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    proactive: ProactiveConfig = field(default_factory=ProactiveConfig)
    shortcuts: ShortcutsConfig = field(default_factory=ShortcutsConfig)
    image_generation: ImageGenConfig = field(default_factory=ImageGenConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)


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


def _proactive(p: dict) -> "ProactiveConfig":
    d = ProactiveConfig()
    return ProactiveConfig(
        enabled=bool(p.get("enabled", d.enabled)), interval_min=int(p.get("interval_min", d.interval_min)),
        new_devices=bool(p.get("new_devices", d.new_devices)), calendar=bool(p.get("calendar", d.calendar)),
        calendar_lead_min=int(p.get("calendar_lead_min", d.calendar_lead_min)),
        disk=bool(p.get("disk", d.disk)), disk_warn_pct=int(p.get("disk_warn_pct", d.disk_warn_pct)),
        battery=bool(p.get("battery", d.battery)), battery_warn_pct=int(p.get("battery_warn_pct", d.battery_warn_pct)))


def _shortcuts(raw) -> "ShortcutsConfig":
    raw = raw or {}
    allowed = raw.get("allowed") or ()
    if isinstance(allowed, str) or not isinstance(allowed, (list, tuple)):
        raise ConfigError("shortcuts.allowed muss eine Liste von Kurzbefehl-Namen sein")
    return ShortcutsConfig(allowed=tuple(str(n) for n in allowed if str(n).strip()))


def _image_generation(raw) -> "ImageGenConfig":
    raw = raw or {}
    d = ImageGenConfig()
    return ImageGenConfig(
        enabled=bool(raw.get("enabled", d.enabled)), model=str(raw.get("model", d.model)),
        quantize=int(raw.get("quantize", d.quantize)), steps=int(raw.get("steps", d.steps)),
        command=raw.get("command") or None)


def _domains(raw) -> tuple[str, ...]:
    """Domainliste aus YAML: klein geschrieben, ohne Schema, „www.“ und Pfad."""
    out = []
    for d in raw or ():
        d = str(d).strip().lower().split("://")[-1].split("/")[0].removeprefix("www.")
        if d:
            out.append(d)
    return tuple(out)


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
        data_dir=t.get("data_dir"), router=str(t.get("router", "keywords")).lower(),
        editor=str(t.get("editor") or "Visual Studio Code"))
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
        max_chars_per_source=int(rs.get("max_chars_per_source", 4500)),
        only_trusted=bool(rs.get("only_trusted", False)),
        trusted_domains=_domains(rs.get("trusted_domains")),
        blocked_domains=_domains(rs.get("blocked_domains")))
    integrations = {str(n): dict(v or {}) for n, v in (raw.get("integrations") or {}).items()}
    xp = raw.get("experience") or {}
    experience = ExperienceConfig(
        startup_animation=bool(xp.get("startup_animation", True)),
        startup_briefing=bool(xp.get("startup_briefing", True)),
        music_file=xp.get("music_file") or None,
        music_volume=max(0.0, min(1.0, float(xp.get("music_volume", 0.28)))),
        music_duck_volume=max(0.0, min(1.0, float(xp.get("music_duck_volume", 0.07)))),
        open_on_start=tuple(str(u) for u in (xp.get("open_on_start") or ())
                            if str(u).startswith(("https://", "http://"))))
    rr = raw.get("reasoning") or {}
    mode = rr.get("mode", "auto")
    # YAML 1.1 liest unquoted on/off als bool; beide Schreibweisen zulassen.
    mode = ("on" if mode else "off") if isinstance(mode, bool) else str(mode).lower()
    reasoning = ReasoningConfig(mode=mode, max_tokens=int(rr.get("max_tokens", 2048)),
                                timeout_s=float(rr.get("timeout_s", 120)))
    extensions = raw.get("extensions") or {}
    if not isinstance(extensions, dict):
        raise ConfigError("extensions muss eine YAML-Zuordnung sein")
    for key in ("ssh", "mcp", "mac_ui"):
        section = extensions.get(key) or {}
        if not isinstance(section, dict) or not isinstance(section.get("enabled", False), bool):
            raise ConfigError(f"extensions.{key}: Zuordnung mit enabled: true|false erwartet")
        entries_key = {"ssh": "hosts", "mcp": "servers"}.get(key)
        if entries_key:
            entries = section.get(entries_key, {})
            if (not isinstance(entries, dict) or any(not isinstance(n, str) or not n for n in entries)
                    or any(not isinstance(v, dict) for v in entries.values())):
                raise ConfigError(f"extensions.{key}.{entries_key} muss eine Zuordnung von Namen zu Einstellungen sein")
            if key == "mcp":
                for server in entries.values():
                    if (not isinstance(server.get("command", ""), str) or
                            not isinstance(server.get("args", []), list) or
                            not all(isinstance(v, str) for v in server.get("args", [])) or
                            not isinstance(server.get("env_names", []), list) or
                            not all(isinstance(v, str) for v in server.get("env_names", []))):
                        raise ConfigError("MCP command muss Text sein, args/env_names müssen Stringlisten sein")

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
            reasoning=p.get("reasoning"),
        ),
        tools=tools, knowledge=knowledge, reminders=reminders, research=research,
        integrations=integrations, experience=experience,
        reasoning=reasoning,
        extensions=extensions,
        proactive=_proactive(raw.get("proactive") or {}),
        shortcuts=_shortcuts(raw.get("shortcuts")),
        image_generation=_image_generation(raw.get("image_generation")),
        memory=MemoryConfig(auto_learn=bool((raw.get("memory") or {}).get("auto_learn", True))),
        network=NetworkConfig(vendor_lookup=bool((raw.get("network") or {}).get("vendor_lookup", True))),
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
