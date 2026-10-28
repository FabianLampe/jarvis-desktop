"""Smoke-Test der Bridge-JSON-Verträge (ohne echtes Fenster/Ollama)."""

import json
import time

from jarvis_core.config import AppConfig, Profile
from jarvis_app.bridge import Bridge

HOME = Profile(name="home", general="g3", coder="c3", heavy="h7", gpu_monitor=False)


class FakeChat:
    def __init__(self):
        self.history = []

    def send(self, text, force_7b=False, model_override=None):
        yield {"type": "route", "lane": "general", "model": "g3", "reason": "Test",
               "heavy_requested": False, "heavy_unavailable": False}
        yield {"type": "state", "value": "think"}
        yield {"type": "state", "value": "speak"}
        yield {"type": "token", "content": 'Zeile mit "Anführungszeichen"\nund Umbruch'}
        yield {"type": "done", "tps": 87.0, "text": "…"}
        yield {"type": "state", "value": "idle"}

    def reset(self):
        self.history.clear()

    def remember_exchange(self, user, assistant):
        self.history.extend([{"role": "user", "content": user}, {"role": "assistant", "content": assistant}])

    def context_info(self):
        return {"used": 0, "total": 8192, "pct": 0, "messages": len(self.history), "source": "geschätzt", "model": "g3"}


class FakeWindow:
    def __init__(self):
        self.js_calls: list[str] = []

    def evaluate_js(self, script):
        self.js_calls.append(script)


class FakeHub:
    def __init__(self):
        self.spoken: list[str] = []
        self.connected = False

    def start(self, **kwargs):
        pass

    def say(self, text):
        self.spoken.append(text)
        return True

    def push_to_talk(self):
        return False

    def stop_speaking(self):
        return False


def make_bridge(tmp_path):
    cfg = AppConfig(profile=HOME)
    b = Bridge(cfg, chat=FakeChat(), hub=FakeHub(),
               layout_path=tmp_path / "layout.json")
    b._window = FakeWindow()
    return b


def wait_for(pred, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


def test_request_briefing_renders_and_speaks(tmp_path):
    b = make_bridge(tmp_path)

    class FakeBriefing:
        def collect(self):
            return {"datetime": "heute", "weather": None, "mails": [], "issues": [],
                    "system": {"cpu": 1, "ram": 2, "vram": None}, "repo": "a/b"}

    b._briefing = FakeBriefing()
    b.request_briefing()
    assert wait_for(lambda: any("jarvis.onBriefing" in c for c in b._window.js_calls))
    # ohne Daemon: Skript geht als Token ins Panel
    assert wait_for(lambda: any("onToken" in c for c in b._window.js_calls))


def test_browser_intent_routes_to_agent(tmp_path):
    b = make_bridge(tmp_path)
    actions = []

    class FakeAgent:
        def run(self, text):
            actions.append(text)
            yield {"type": "action", "text": "Ich öffne YouTube, Sir."}
            yield {"type": "done", "text": "Ich öffne YouTube, Sir."}

    b._browser_agent = FakeAgent()
    b._daemon_utterance("Öffne YouTube")
    assert wait_for(lambda: actions == ["Öffne YouTube"])
    assert wait_for(lambda: bool(b._hub.spoken))


def test_non_browser_message_uses_chat(tmp_path):
    b = make_bridge(tmp_path)
    called = []

    class FakeAgent:
        def run(self, text):
            called.append(text)
            yield {"type": "done", "text": "x"}

    b._browser_agent = FakeAgent()
    b.send_message("Wie spät ist es?", False)
    assert wait_for(lambda: any('onState("idle"' in c for c in b._window.js_calls))
    assert called == []  # kein Browser-Intent → ChatService, nicht der Agent


def test_send_message_streams_contract_calls(tmp_path):
    b = make_bridge(tmp_path)
    res = b.send_message("Status?", False)
    sid = res["stream_id"]
    assert sid == "s1"

    calls = b._window.js_calls
    # onState("idle") ist das letzte Ereignis des Streams
    assert wait_for(lambda: any('onState("idle"' in c for c in calls))
    joined = "\n".join(calls)
    assert f'jarvis.onToken("{sid}"' in joined
    assert 'jarvis.onState("think"' in joined and 'jarvis.onState("idle"' in joined
    # Token-Inhalt ist JSON-escaped (kein rohes Newline im Skript)
    token_call = next(c for c in calls if "onToken" in c)
    assert "\n" not in token_call and "Anf\\u00fchrungszeichen" in token_call


def test_empty_message_is_ignored(tmp_path):
    b = make_bridge(tmp_path)
    assert b.send_message("   ", False) == {"stream_id": None}
    assert b._window.js_calls == []


def test_status_and_config_are_json_serializable(tmp_path):
    b = make_bridge(tmp_path)
    status = b.get_status()
    config = b.get_config()
    json.dumps(status), json.dumps(config)
    assert status["heavy"] == "BEREIT" and status["heavy_available"] is True
    assert config["heavy_available"] is True
    assert config["orb"]["seed"] == 5 and config["orb_px"] == 240


def test_voice_utterance_shows_user_line_and_speaks_back(tmp_path):
    b = make_bridge(tmp_path)
    b._daemon_utterance("Wie ist der Status?")
    assert wait_for(lambda: b._hub.spoken)
    assert b._hub.spoken == ["…"]  # done-Text aus FakeChat wird vorgelesen
    joined = "\n".join(b._window.js_calls)
    assert 'jarvis.onUserMessage("Wie ist der Status?")' in joined


def test_typed_message_is_not_spoken(tmp_path):
    b = make_bridge(tmp_path)
    b.send_message("Status?", False)
    assert wait_for(lambda: any('"done"' in c for c in b._window.js_calls))
    assert b._hub.spoken == []


def test_layout_roundtrip(tmp_path):
    b = make_bridge(tmp_path)

    class W(FakeWindow):
        x, y, width, height = 100, 200, 360, 360

    b._window = W()
    b._write_layout()
    fresh = Bridge(AppConfig(profile=HOME), chat=FakeChat(),
                   layout_path=tmp_path / "layout.json")
    layout = fresh.load_layout()
    assert layout["cx"] == 100 + 360 // 2 and layout["cy"] == 200 + 360 // 2
    assert layout["orb_px"] == 240
