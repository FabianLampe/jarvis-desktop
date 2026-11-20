"""Bilderzeugung (nur Mock, nie ein Modell) und Kamerabeobachtung (Bewegungserkennung, Meldungen, Stopp)."""
import dataclasses
import io
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis_core.tools import camera_watch as cw, imagegen_tools as ig
from jarvis_core.tools.registry import ALLOW, CONFIRM, InvalidArgs
from tests.tools_helpers import runtime


def execute(rt, tool, **args):
    return rt.registry.execute(rt.ctx, tool, args, 20000)


def assess(rt, tool, **args):
    return rt.registry.assess(rt.ctx, tool, args)


def configure_gen(rt, **kw):
    cfg = dataclasses.replace(rt.cfg, image_generation=dataclasses.replace(rt.cfg.image_generation, **kw))
    rt.ctx.cfg = rt.agent.cfg = cfg


# ------------------------------------------------------------ Bildgenerierung

def test_imagegen_disabled_gives_setup_guide(tmp_path):
    rt = runtime(tmp_path)
    assert rt.cfg.image_generation.enabled is False                 # ausgelieferte config: aus
    r = execute(rt, "image_generate", prompt="ein Fuchs")
    assert not r.ok and "ausgeschaltet" in r.text and "34 GB" in r.text and "image_generation.enabled" in r.text
    assert "Arbeitsspeicher" in r.text and "Ollama" in r.text


def test_imagegen_missing_weights_or_command(tmp_path, monkeypatch):
    rt = runtime(tmp_path)
    configure_gen(rt, enabled=True)
    monkeypatch.setattr(ig, "find_command", lambda ctx: None)
    assert "nicht installiert" in execute(rt, "image_generate", prompt="x").text
    monkeypatch.setattr(ig, "find_command", lambda ctx: "/bin/mflux-generate")
    monkeypatch.setattr(ig, "hf_cache", lambda: tmp_path / "leer")
    r = execute(rt, "image_generate", prompt="x")
    assert not r.ok and "noch nicht geladen" in r.text and "34 GB" in r.text


def test_imagegen_runs_offline_with_argv_prompt_and_saves(tmp_path, monkeypatch):
    rt = runtime(tmp_path)
    configure_gen(rt, enabled=True)
    cache = tmp_path / "hf"
    (cache / "models--black-forest-labs--FLUX.1-schnell").mkdir(parents=True)
    monkeypatch.setattr(ig, "hf_cache", lambda: cache)
    monkeypatch.setattr(ig, "find_command", lambda ctx: "/opt/mflux-generate")
    seen, opened = {}, []

    def fake_exec(ctx, argv, cwd):
        seen["argv"] = argv
        Path(argv[argv.index("--output") + 1]).write_bytes(b"PNG")
        return SimpleNamespace(ok=True, cancelled=False, timed_out=False, stdout="", stderr="", error=None)
    monkeypatch.setattr(ig, "_exec", fake_exec)
    monkeypatch.setattr(ig, "_open", lambda p: opened.append(p))
    evil = "--steps 999; rm -rf ~ $(id)"
    r = execute(rt, "image_generate", prompt=evil, size="querformat")
    assert r.ok and "1024×640" in r.text and (rt.ctx.workspace / "Bilder").is_dir()
    argv = seen["argv"]
    assert f"--prompt={evil}" in argv and argv[0] == "/opt/mflux-generate"       # ein argv-Element, nie geteilt
    assert argv[argv.index("--model") + 1] == "schnell" and argv[argv.index("--steps") + 1] == "4"
    assert argv[argv.index("--quantize") + 1] == "4" and argv[argv.index("--width") + 1] == "1024"
    assert opened and opened[0].name.startswith("generiert-") and opened[0].suffix == ".png"


def test_imagegen_offline_env_and_timeout(tmp_path, monkeypatch):
    rt = runtime(tmp_path)
    captured = {}

    class Runner:
        def run(self, argv, cwd, **kw):
            captured.update(kw)
            return SimpleNamespace(ok=False, cancelled=False, timed_out=True, stdout="", stderr="", error=None)
    monkeypatch.setattr(ig, "_runner", lambda ctx: Runner())
    ig._exec(rt.ctx, ["x"], tmp_path)
    assert captured["env"] == {"HF_HUB_OFFLINE": "1"} and captured["timeout_s"] == ig.TIMEOUT_S
    configure_gen(rt, enabled=True)
    monkeypatch.setattr(ig, "find_command", lambda ctx: "/opt/m")
    monkeypatch.setattr(ig, "_weights_present", lambda m: True)
    monkeypatch.setattr(ig, "_exec", lambda ctx, argv, cwd: SimpleNamespace(ok=False, cancelled=False, timed_out=True,
                                                                           stdout="", stderr="", error=None))
    r = execute(rt, "image_generate", prompt="x")
    assert not r.ok and "Zeitlimit" in r.text and "Arbeitsspeicher" in r.text


def test_imagegen_failure_from_offline_cache_points_to_setup(tmp_path, monkeypatch):
    rt = runtime(tmp_path)
    configure_gen(rt, enabled=True)
    monkeypatch.setattr(ig, "find_command", lambda ctx: "/opt/m")
    monkeypatch.setattr(ig, "_weights_present", lambda m: True)
    monkeypatch.setattr(ig, "_exec", lambda ctx, argv, cwd: SimpleNamespace(
        ok=False, cancelled=False, timed_out=False, stdout="", stderr="LocalEntryNotFoundError: not in cache", error=None))
    r = execute(rt, "image_generate", prompt="x")
    assert not r.ok and "Einrichtung" in r.text


def test_imagegen_size_validation_and_policy(tmp_path):
    rt = runtime(tmp_path)
    assert ig.parse_size("1024x640") == (1024, 640) and ig.parse_size("") == ig.DEFAULT_SIZE
    for bad in ("5000x5000", "100x100", "770x770", "abc"):
        with pytest.raises(InvalidArgs):
            ig.parse_size(bad)
    assert assess(rt, "image_generate", prompt="x").level == ALLOW
    rt.ctx.tainted = True
    assert assess(rt, "image_generate", prompt="x").level == CONFIRM


def test_imagegen_routing():
    from jarvis_core.tool_intent import select_groups
    g = select_groups("Erzeuge ein Bild von einem Fuchs im Schnee")
    assert "imagegen" in g and "vision" not in g
    assert select_groups("Was siehst du auf dem Bild") == {"vision"}


# ------------------------------------------------------- Bewegungserkennung

def test_motion_detector_ignores_static_and_light_changes_but_sees_movement():
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from jarvis_core.tools.camera_watch_worker import MotionDetector, WARMUP_FRAMES
    rng = np.random.default_rng(1)
    base = np.full((240, 320), 90, dtype=np.uint8)
    det = MotionDetector(0.03)
    for _ in range(WARMUP_FRAMES + 8):                                   # ruhiges Bild mit etwas Rauschen
        noisy = np.clip(base + rng.integers(-3, 4, base.shape), 0, 255).astype(np.uint8)
        assert det.feed(noisy) is False
    bright = np.full((240, 320), 210, dtype=np.uint8)                     # Belichtungssprung ≠ Bewegung
    assert det.feed(bright) is False and det.feed(bright) is False
    for _ in range(5):
        det.feed(bright)
    moved = bright.copy()
    moved[40:200, 80:200] = 20                                            # jemand tritt ins Bild
    hits = [det.feed(moved), det.feed(moved), det.feed(moved)]
    assert any(hits)


# ---------------------------------------------------------- Kamera-Dienst

class FakeProc:
    def __init__(self, lines):
        self.stdout = io.StringIO("".join(json.dumps(l) + "\n" for l in lines))
        self.terminated = False
        self._alive = True

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self.terminated, self._alive = True, False

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self._alive = False


def make_watch(lines, notices, clock):
    procs = []

    def spawn(argv):
        procs.append((argv, FakeProc(lines)))
        procs[-1][1]._alive = True
        return procs[-1][1]
    return cw.CameraWatch(notices.append, spawn=spawn, clock=clock, wall=lambda: "14:30"), procs


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_watch_notifies_at_most_once_per_minute(monkeypatch):
    notices, clock = [], Clock()
    watch, procs = make_watch([{"event": "started"}], notices, clock)
    monkeypatch.setattr(cw.atexit, "register", lambda f: None)
    assert "10 Minuten" in watch.start(10, "high")
    argv = procs[0][0]
    assert argv[-2:] == ["600", "high"] and argv[1] == "-I"
    watch._thread.join(2)
    for dt in (0, 5, 59, 61, 62):
        clock.t = 1000.0 + dt
        watch._on_motion()
    assert notices == ["Bewegung im Kamerabild, Sir (14:30 Uhr).", "Bewegung im Kamerabild, Sir (14:30 Uhr)."]
    assert watch.status()["motion_events"] == 5


def test_watch_start_reports_missing_camera_access(monkeypatch):
    monkeypatch.setattr(cw.atexit, "register", lambda f: None)
    watch, procs = make_watch([{"event": "error", "error": "no_access"}], [], Clock())
    with pytest.raises(cw.ToolError, match="Kamera"):
        watch.start(5)
    assert procs[0][1].terminated


def test_watch_stop_terminates_process_and_status(monkeypatch):
    monkeypatch.setattr(cw.atexit, "register", lambda f: None)
    watch, procs = make_watch([{"event": "started"}], [], Clock())
    watch.start(5)
    assert watch.running and watch.status()["minutes_left"] == 5
    assert watch.stop() and procs[0][1].terminated and not watch.running
    assert watch.stop() is False


def test_watch_registers_atexit_cleanup(monkeypatch):
    registered = []
    monkeypatch.setattr(cw.atexit, "register", lambda f: registered.append(f))
    watch, _ = make_watch([{"event": "started"}], [], Clock())
    watch.start(5)
    assert registered == [watch.stop]


def test_watch_tools_confirm_limit_and_notify_route(tmp_path, monkeypatch):
    rt = runtime(tmp_path)
    a = assess(rt, "camera_watch_start", minutes=20)
    assert a.level == CONFIRM and "20 MINUTEN" in a.summary and "kein Bild" in a.summary
    for bad in (0, 61, 600):
        with pytest.raises(InvalidArgs):
            rt.registry.assess(rt.ctx, "camera_watch_start", {"minutes": bad})
    assert assess(rt, "camera_watch_start", minutes=5, sensitivity="extreme").level == "deny"   # Schema: nur low/medium/high
    # Meldungen laufen über ctx.shared["notify"] (Oberfläche), sonst Systemmitteilung
    got = []
    rt.ctx.shared["notify"] = got.append
    svc = cw.get_watch(rt.ctx)
    svc._notify("hallo")
    assert got == ["hallo"]
    assert "Es läuft keine" in execute(rt, "camera_watch_status").text
    assert execute(rt, "camera_watch_stop").ok


def test_worker_has_no_face_recognition_or_image_storage():
    src = Path(cw.WORKER).read_text()
    for forbidden in ("imwrite", "CascadeClassifier", "face", "dnn", "write_bytes", "open(", "save("):
        assert forbidden not in src.replace("Gesichtserkennung", "").replace("keine Gesichts", "")


def test_imagegen_disabled_never_spawns_anything(tmp_path, monkeypatch):
    rt = runtime(tmp_path)
    assert rt.cfg.image_generation.enabled is False
    monkeypatch.setattr(ig, "_exec", lambda *a, **k: pytest.fail("Prozess gestartet"))
    monkeypatch.setattr(ig, "_runner", lambda ctx: pytest.fail("Runner benutzt"))
    monkeypatch.setattr(ig, "_open", lambda p: pytest.fail("open aufgerufen"))
    r = execute(rt, "image_generate", prompt="ein Fuchs")
    assert not r.ok and not (rt.ctx.workspace / "Bilder").exists()
