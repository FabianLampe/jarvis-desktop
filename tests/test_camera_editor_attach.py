"""Kamera, Editor und Bilder aus dem Chat."""
import base64
import json
import subprocess
from types import SimpleNamespace

import pytest

from jarvis_core.runtime import build_runtime
from jarvis_core.tool_intent import select_groups
from jarvis_core.tools import editor_tools as et, vision_tools as vt
from jarvis_core.tools.registry import ALLOW, CONFIRM
from tests.tools_helpers import CFG, FakeClient, call, run, runtime

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


# ----------------------------------------------------------------- Weiche

@pytest.mark.parametrize("text,must,must_not", [
    ("Öffne meine Kamera und analysiere mein Gesicht", {"vision"}, {"system"}),
    ("Wie sehe ich heute aus?", {"vision"}, set()),
    ("Mach ein Foto mit der Webcam", {"vision"}, set()),
    ("Schreib mir ein Python-Skript das die Primzahlen bis 100 ausgibt und öffne es in VS Code",
     {"editor", "files", "terminal"}, set()),
    ("Öffne die Datei main.py in VS Code", {"editor"}, {"system"}),
    ("[Bild angehängt: Bilder/eingang-1.png] Was ist das?", {"vision"}, set()),
    ("Öffne Safari", {"system"}, {"editor", "vision"}),
])
def test_routing(text, must, must_not):
    g = select_groups(text)
    assert must <= g and not (must_not & g), g


def test_pure_code_request_still_goes_to_chat():
    assert select_groups("Schreib mir eine Python-Funktion die Primzahlen findet") == set()


# ----------------------------------------------------------------- Kamera

def _cam_runtime(tmp_path, *msgs):
    return build_runtime(CFG, FakeClient(*msgs), workspace=tmp_path / "ws", data_dir=tmp_path / "data")


def _fake_worker(result: dict, write: bool = True):
    def fake_run(argv, **kw):
        if write and result.get("ok"):
            open(argv[3], "wb").write(PNG)
        return SimpleNamespace(stdout="OpenCV: Rauschen\n" + json.dumps(result) + "\n", stderr="", returncode=0)
    return fake_run


def test_camera_photo_is_analysed_and_deleted(tmp_path, monkeypatch):
    rt = _cam_runtime(tmp_path, {"content": "Sie lächeln, Sir, und wirken ausgeschlafen."})
    monkeypatch.setattr(vt.subprocess, "run", _fake_worker({"ok": True, "width": 1280, "height": 720, "brightness": 90}))
    monkeypatch.setattr(vt, "_shrink", lambda src, dst: src)
    r = rt.registry.execute(rt.ctx, "camera_look", {"question": "Wie sehe ich aus?"}, 6000)
    assert r.ok and "lächeln" in r.text and not r.data["saved"]
    prompt = rt.agent.client.calls[0][1][0]
    assert "Wie sehe ich aus?" in prompt["content"] and "keine Identifizierung" in prompt["content"]
    assert prompt["images"]
    assert rt.ctx.tainted and rt.ctx.private
    assert not (rt.ctx.workspace / "Kamera").exists()


def test_camera_photo_can_be_saved(tmp_path, monkeypatch):
    rt = _cam_runtime(tmp_path, {"content": "Ein Schreibtisch."})
    monkeypatch.setattr(vt.subprocess, "run", _fake_worker({"ok": True, "width": 10, "height": 10, "brightness": 90}))
    monkeypatch.setattr(vt, "_shrink", lambda src, dst: src)
    r = rt.registry.execute(rt.ctx, "camera_look", {"save": True}, 6000)
    saved = list((rt.ctx.workspace / "Kamera").glob("foto-*.jpg"))
    assert r.ok and r.data["saved"] and len(saved) == 1 and "Foto gespeichert" in r.text


@pytest.mark.parametrize("error,expected", [("no_access", "Datenschutz & Sicherheit → Kamera"),
                                            ("no_frame", "kein Bild")])
def test_camera_errors_are_explained(tmp_path, monkeypatch, error, expected):
    rt = _cam_runtime(tmp_path)
    monkeypatch.setattr(vt.subprocess, "run", _fake_worker({"ok": False, "error": error}))
    r = rt.registry.execute(rt.ctx, "camera_look", {}, 6000)
    assert not r.ok and expected in r.text
    assert rt.agent.client.calls == []                     # ohne Foto kein Modellaufruf


def test_camera_timeout(tmp_path, monkeypatch):
    rt = _cam_runtime(tmp_path)

    def hang(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 20)
    monkeypatch.setattr(vt.subprocess, "run", hang)
    r = rt.registry.execute(rt.ctx, "camera_look", {}, 6000)
    assert not r.ok and "nicht rechtzeitig" in r.text


def test_foreign_content_cannot_switch_on_the_camera(tmp_path):
    rt = runtime(tmp_path)
    assert rt.registry.assess(rt.ctx, "camera_look", {}).level == ALLOW
    rt.ctx.tainted = True
    assert rt.registry.assess(rt.ctx, "camera_look", {}).level == CONFIRM


# ----------------------------------------------------------------- Editor

def test_open_in_vs_code(tmp_path, monkeypatch):
    rt = runtime(tmp_path)
    f = rt.ctx.workspace / "Code" / "prim.py"
    f.parent.mkdir(parents=True)
    f.write_text("print(2)\n", encoding="utf-8")
    ran = []
    monkeypatch.setattr(et, "find_app", lambda name: name if name == "Visual Studio Code" else None)
    monkeypatch.setattr(et.sys, "platform", "darwin")
    monkeypatch.setattr(et.ProcessRunner, "run", lambda self, argv, cwd, **kw: ran.append(argv) or SimpleNamespace(
        ok=True, stderr="", error=""))
    r = rt.registry.execute(rt.ctx, "open_in_editor", {"path": "Code/prim.py"}, 6000)
    assert r.ok and ran == [["open", "-a", "Visual Studio Code", str(f.resolve())]]
    r = rt.registry.execute(rt.ctx, "open_in_editor", {"path": "Code/prim.py", "editor": "Cursor"}, 6000)
    assert not r.ok and "nicht gefunden" in r.text
    assert not rt.registry.execute(rt.ctx, "open_in_editor", {"path": "fehlt.py"}, 6000).ok


def test_write_test_and_open_script_flow(tmp_path, monkeypatch):
    """„Schreib ein Skript und öffne es in VS Code“: schreiben → ausführen → öffnen, alles ohne Rückfrage."""
    monkeypatch.setattr(et, "find_app", lambda name: name)
    monkeypatch.setattr(et.sys, "platform", "darwin")
    opened = []
    real_run = et.ProcessRunner.run
    monkeypatch.setattr(et.ProcessRunner, "run", lambda self, argv, cwd, **kw: opened.append(argv) or SimpleNamespace(
        ok=True, stderr="", error="") if argv[0] == "open" else real_run(self, argv, cwd, **kw))
    code = "print([p for p in range(2, 30) if all(p % d for d in range(2, p))])\n"
    rt = runtime(tmp_path,
                 {"content": "", "tool_calls": [call("write_file", path="Code/prim.py", content=code)]},
                 {"content": "", "tool_calls": [call("run_script", path="Code/prim.py")]},
                 {"content": "", "tool_calls": [call("open_in_editor", path="Code/prim.py")]},
                 {"content": "Das Skript läuft und ist in VS Code geöffnet, Sir."})
    events = run(rt, "Schreib mir ein Python-Skript für Primzahlen und öffne es in VS Code", answer=lambda ev: False)
    assert not [e for e in events if e["type"] == "confirm"]
    results = [e for e in events if e["type"] == "result"]
    assert [e["tool"] for e in results] == ["write_file", "run_script", "open_in_editor"]
    assert all(e["ok"] for e in results) and "[2, 3, 5, 7" in results[1]["text"]
    assert opened and opened[0][-1].endswith("Code/prim.py") and events[-1]["ok"]


@pytest.mark.parametrize("editor", ["Terminal", "Script Editor", "../../bin/sh", "iTerm"])
def test_model_cannot_pick_arbitrary_apps(tmp_path, monkeypatch, editor):
    rt = runtime(tmp_path)
    (rt.ctx.workspace / "x.command").write_text("echo pwned", encoding="utf-8")
    monkeypatch.setattr(et, "find_app", lambda name: name)
    ran = []
    monkeypatch.setattr(et.ProcessRunner, "run", lambda self, argv, cwd, **kw: ran.append(argv))
    r = rt.registry.execute(rt.ctx, "open_in_editor", {"path": "x.command", "editor": editor}, 6000)
    assert not r.ok and "Unbekannter Editor" in r.text and ran == []


def test_find_app_rejects_paths():
    assert et.find_app("../../System/Applications/Utilities/Terminal") is None


# ----------------------------------------------------- Bild aus dem Chat

@pytest.fixture
def bridge(tmp_path):
    from jarvis_app.bridge import Bridge
    from tests.test_bridge import FakeHub
    b = Bridge(CFG, hub=FakeHub(), layout_path=tmp_path / "l.json")
    b._rt = build_runtime(CFG, FakeClient(), workspace=tmp_path / "ws", data_dir=tmp_path / "data")
    return b


def test_attached_image_is_saved_in_workspace(bridge, tmp_path):
    r = bridge.attach_image("data:image/png;base64," + base64.b64encode(PNG).decode(), "x.png")
    assert r["ok"] and r["rel"].startswith("Bilder/eingang-") and r["rel"].endswith(".png")
    assert (tmp_path / "ws" / r["rel"]).read_bytes() == PNG
    r2 = bridge.attach_image("data:image/png;base64," + base64.b64encode(PNG).decode(), "x.png")
    assert r2["ok"] and r2["rel"] != r["rel"]                          # gleiche Sekunde: kein Überschreiben


@pytest.mark.parametrize("url", ["", "data:text/html;base64,PGgxPg==", "data:image/png;base64,###",
                                 "data:image/png;base64," + base64.b64encode(b"#!/bin/sh\nrm -rf ~").decode()])
def test_non_images_are_refused(bridge, tmp_path, url):
    assert not bridge.attach_image(url)["ok"]
    assert not (tmp_path / "ws" / "Bilder").exists() or not any((tmp_path / "ws" / "Bilder").iterdir())


def test_attached_image_message_reaches_image_look(tmp_path, monkeypatch):
    monkeypatch.setattr(vt, "_shrink", lambda src, dst: src)
    rt = runtime(tmp_path,
                 {"content": "", "tool_calls": [call("image_look", path="Bilder/eingang-1.png", question="Was ist das?")]},
                 {"content": "Ein Diagramm."},       # Antwort des Bildmodells
                 {"content": "Ein Balkendiagramm, Sir."})
    (rt.ctx.workspace / "Bilder").mkdir()
    (rt.ctx.workspace / "Bilder" / "eingang-1.png").write_bytes(PNG)
    rt.ctx.services["llm"] = rt.agent.client
    done = run(rt, "Was ist das? [Bild angehängt: Bilder/eingang-1.png]")[-1]
    assert done["ok"] and "Balkendiagramm" in done["text"]
