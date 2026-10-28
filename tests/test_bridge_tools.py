"""Bridge ↔ Werkzeugsteuerung: Aktionen, Bestätigungskarte, Ablehnung, Abbruch (ohne Fenster)."""
import threading
import time

from jarvis_app.bridge import Bridge
from jarvis_core import config
from jarvis_core.runtime import build_runtime
from tests.test_bridge import FakeChat, FakeHub, FakeWindow
from tests.tools_helpers import CFG, FakeClient, call


def make(tmp_path, *messages):
    b = Bridge(CFG, chat=FakeChat(), hub=FakeHub(), layout_path=tmp_path / "l.json")
    b._window = FakeWindow()
    b._rt = build_runtime(CFG, FakeClient(*messages), workspace=tmp_path / "ws", data_dir=tmp_path / "data")
    b._chat.remember_exchange = lambda u, a: b._chat.history.extend([u, a])
    return b


def wait_for(cond, t=5):
    end = time.time() + t
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_tool_task_streams_actions_and_result(tmp_path):
    b = make(tmp_path, {"tool_calls": [call("make_dir", path="Projekt")]}, {"content": "Ordner steht, Sir."})
    b._run_stream("s1", "Lege einen Ordner Projekt an", False, False)
    js = "\n".join(b._window.js_calls)
    assert (tmp_path / "ws" / "Projekt").is_dir()
    assert "jarvis.onAction" in js and "Ordner steht" in js and '"type": "done"' in js


def test_confirmation_card_and_decline_via_bridge(tmp_path):
    b = make(tmp_path, {"tool_calls": [call("delete_path", path="a.txt")]}, {"content": "x"})
    (tmp_path / "ws" / "a.txt").write_text("x")
    t = threading.Thread(target=b._run_stream, args=("s1", "Lösche die Datei a.txt", False, False))
    t.start()
    assert wait_for(lambda: any("jarvis.onConfirm" in c for c in b._window.js_calls))
    card = next(c for c in b._window.js_calls if "jarvis.onConfirm" in c)
    import json, re
    ev = json.loads(re.search(r"onConfirm\((.*)\)\s*$", card).group(1))
    assert "LÖSCHEN" in ev["summary"] and "a.txt" in ev["summary"]
    cid = ev["id"]
    assert b.confirm_action("falsche-id", True) == {"ok": False}
    assert b.confirm_action(cid, False) == {"ok": True}
    t.join(5)
    assert (tmp_path / "ws" / "a.txt").exists()
    assert "Vom Nutzer abgelehnt" in "\n".join(b._window.js_calls)


def test_cancel_button(tmp_path):
    b = make(tmp_path, {"tool_calls": [call("run_command", argv=["sleep", "30"], timeout_s=60)]}, {"content": "x"})
    t = threading.Thread(target=b._run_stream, args=("s1", "Führe im Terminal sleep aus", False, False))
    t0 = time.time(); t.start(); time.sleep(0.8)
    assert b.cancel() == {"ok": True}
    t.join(8)
    assert not t.is_alive() and time.time() - t0 < 8
