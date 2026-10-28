"""Parallele Aufgaben: gleichzeitige Läufe, Statusauskunft, gezieltes Abbrechen."""
import threading
import time

from jarvis_app.bridge import Bridge
from jarvis_core.runtime import build_runtime
from jarvis_core.tasks import TaskManager
from tests.test_bridge import FakeChat, FakeHub, FakeWindow
from tests.tools_helpers import CFG, call


class SmartClient:
    """Antwortet je nach Auftragstext (A/B), damit parallele Läufe sich nicht die Antworten klauen."""
    def chat_once(self, model, messages, **kw):
        if messages[-1]["role"] == "tool":
            return {"content": "Erledigt."}
        user = next(m["content"] for m in reversed(messages) if m["role"] == "user")
        secs = "2" if " A" in user else "1.2"
        return {"tool_calls": [call("run_command", argv=["sleep", secs], timeout_s=30)]}


def make(tmp_path):
    b = Bridge(CFG, chat=FakeChat(), hub=FakeHub(), layout_path=tmp_path / "l.json")
    b._window = FakeWindow()
    b._rt = build_runtime(CFG, SmartClient(), workspace=tmp_path / "ws", data_dir=tmp_path / "data")
    return b


def test_two_tasks_run_at_the_same_time(tmp_path):
    b = make(tmp_path)
    t0 = time.time()
    th = [threading.Thread(target=b._run_stream, args=(f"s{i}", txt, False, False))
          for i, txt in enumerate(["Führe im Terminal sleep A aus", "Führe im Terminal sleep B aus"])]
    [t.start() for t in th]
    time.sleep(0.8)
    assert len(b._tasks.active()) == 2                        # beide laufen gerade
    answer = b._tasks.describe()
    assert "2 Aufgaben gleichzeitig" in answer and "Aufgabe 1" in answer and "Aufgabe 2" in answer
    [t.join(15) for t in th]
    assert time.time() - t0 < 3.6                             # < 2 + 1.2 s nacheinander (+ Overhead)
    assert b._tasks.active() == [] and b._tasks.describe() == "Gerade arbeite ich an nichts, Sir."


def test_cancel_one_task_leaves_the_other_running(tmp_path):
    b = make(tmp_path)
    th = [threading.Thread(target=b._run_stream, args=(f"s{i}", txt, False, False))
          for i, txt in enumerate(["Führe im Terminal sleep A aus", "Führe im Terminal sleep B aus"])]
    [t.start() for t in th]
    time.sleep(0.8)
    first = sorted(b._tasks.active(), key=lambda t: t.id)[0].id
    assert b.cancel_task(first) == {"cancelled": 1}
    time.sleep(0.3)
    assert [t.id for t in b._tasks.active()] != [] and first not in [t.id for t in b._tasks.active()]
    [t.join(15) for t in th]
    statuses = {t.id: t.status for t in b._tasks._tasks.values()}
    assert statuses[first] == "cancelled" and "done" in statuses.values()


def test_voice_status_question_and_cancel_all(tmp_path):
    b = make(tmp_path)
    th = threading.Thread(target=b._run_stream, args=("s1", "Führe im Terminal sleep A aus", False, False)); th.start()
    time.sleep(0.8)
    b._run_stream("s2", "Was machst du gerade?", False, True, True)
    assert "Aufgabe 1" in b._hub.spoken[-1] and "sleep" in b._hub.spoken[-1].lower()
    b._run_stream("s3", "Brich alles ab", False, True, True)
    assert "1 Aufgabe abgebrochen" in b._hub.spoken[-1]
    th.join(10)
    b._run_stream("s4", "Was machst du gerade?", False, True, True)
    assert b._hub.spoken[-1] == "Gerade arbeite ich an nichts, Sir."


def test_confirmations_are_not_cross_cancelled(tmp_path):
    b = make(tmp_path)
    rt = b._rt
    from jarvis_core.runtime import spawn_agent
    a1, a2 = spawn_agent(rt), spawn_agent(rt)
    pa1 = rt.confirms.create("delete_path", {"path": "x"}, "LÖSCHEN x"); a1._pending_ids.add(pa1.id)
    pa2 = rt.confirms.create("delete_path", {"path": "y"}, "LÖSCHEN y"); a2._pending_ids.add(pa2.id)
    a1.cancel()
    assert pa1.event.is_set() and not pa2.event.is_set()       # nur die eigene Bestätigung verworfen


def test_task_labels_and_progress_not_truncated(tmp_path):
    b = make(tmp_path)
    (tmp_path / "ws").mkdir(exist_ok=True)
    tm = TaskManager(lambda: None)
    t = tm.begin("Test"); tm.update(t, current="Ich lese Datei")
    assert tm.snapshot()[0]["current"] == "Ich lese Datei"


def test_weather_fast_path_and_affirmation(tmp_path, monkeypatch):
    from jarvis_core.tool_intent import fast_path, select_groups
    assert fast_path("Wie wird das Wetter heute?") == [{"tool": "weather", "args": {"when": "heute"}}]
    assert fast_path("Wie wird das Wetter morgen in Berlin?")[0]["args"] == {"when": "morgen", "place": "Berlin"}
    assert "weather" in select_groups("Brauche ich heute einen Schirm?")
    from jarvis_app.bridge import _AFFIRM
    for yes in ("Mach das", "Ja bitte", "mach das bitte", "ja, mach das", "Gerne", "Okay", "tu es"):
        assert _AFFIRM.match(yes), yes
    for no in ("Mach das Licht aus", "Wie spät ist es", "Ja, aber erst morgen"):
        assert not _AFFIRM.match(no), no

    b = make(tmp_path)
    called = []
    import jarvis_core.tools.weather_tools as wt
    monkeypatch.setattr(wt, "weather", lambda ctx, a: (called.append(a) or wt.ToolResult(True, "Hamburg, heute: Regen, 9 bis 12 Grad.")))
    b._rt.registry.get("weather").__class__  # Tool ist frozen → Handler über Registry ersetzen
    from dataclasses import replace
    b._rt.registry.register(replace(b._rt.registry.get("weather"), handler=wt.weather))
    b._chat.history = [{"role": "user", "content": "Wie wird das Wetter heute?"},
                       {"role": "assistant", "content": "Ich habe keinen Zugriff, aber ich kann es für Sie einholen."}]
    b._run_stream("s1", "Mach das", False, True, True)          # Sprache: „Mach das“
    assert called == [{"when": "heute"}] and "Hamburg" in b._hub.spoken[-1]


def test_time_and_date_need_no_model(tmp_path):
    from jarvis_core.tool_intent import fast_path
    assert fast_path("Wie spät ist es?") == [{"tool": "clock", "args": {"what": "time"}}]
    assert fast_path("Welcher Tag ist heute?") == [{"tool": "clock", "args": {"what": "date"}}]
    b = make(tmp_path)
    b._run_stream("s1", "Wie spät ist es?", False, True, True)        # SmartClient wird nie gefragt
    assert b._hub.spoken[-1].startswith("Es ist ") and "Uhr" in b._hub.spoken[-1]


def test_quick_lane_used_only_while_a_task_runs(tmp_path):
    b = make(tmp_path)
    assert CFG.profile.quick == "qwen2.5:7b" and b._quick_model() is None      # nichts läuft ⇒ normales Modell
    th = threading.Thread(target=b._run_stream, args=("s1", "Führe im Terminal sleep A aus", False, False)); th.start()
    time.sleep(0.8)
    assert b._quick_model() == "qwen2.5:7b"                                    # Aufgabe läuft ⇒ Schnellspur
    th.join(10)
    assert b._quick_model() is None


def test_chat_send_uses_override_model(tmp_path):
    from jarvis_core.chat import ChatService
    seen = []

    class C:
        last_usage = None
        def chat_stream(self, model, messages, **kw):
            seen.append(model); yield {"token": "ok"}; yield {"done": True, "tps": 1, "eval_count": 1}
    chat = ChatService(CFG, client=C())
    evs = list(chat.send("Erzähl mir einen Witz", model_override="qwen2.5:7b"))
    assert seen == ["qwen2.5:7b"] and next(e for e in evs if e["type"] == "route")["model"] == "qwen2.5:7b"
    list(chat.send("Erzähl mir einen Witz")); assert seen[-1] == CFG.profile.general
