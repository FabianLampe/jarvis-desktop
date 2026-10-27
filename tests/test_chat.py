"""ChatService: Ereignis-Strom, Schwerlast-Swap-Reihenfolge, Fehlerverhalten."""

import pytest

from jarvis_core.chat import ChatService
from jarvis_core.config import AppConfig, Profile
from jarvis_core.ollama_client import ModelMissing, OllamaUnavailable

HOME = Profile(name="home", general="g3", coder="c3", heavy="h7", gpu_monitor=False)


def make_cfg(profile=HOME):
    return AppConfig(profile=profile)


class FakeClient:
    """Zeichnet Aufrufe auf; Verhalten pro Test konfigurierbar."""

    def __init__(self, tokens=("Sehr ", "wohl."), fail=None, fail_after=0):
        self.calls: list[tuple] = []
        self.tokens = tokens
        self.fail = fail
        self.fail_after = fail_after

    def chat_stream(self, model, messages, *, num_ctx, keep_alive):
        self.calls.append(("chat", model, keep_alive))
        if self.fail and self.fail_after == 0:
            raise self.fail
        for i, t in enumerate(self.tokens):
            yield {"token": t}
            if self.fail and i + 1 >= self.fail_after:
                raise self.fail
        yield {"done": True, "tps": 87.0, "eval_count": 12}

    def unload(self, model):
        self.calls.append(("unload", model))
        return True

    def preload(self, model, keep_alive="30m"):
        self.calls.append(("preload", model, keep_alive))
        return True


def types(events):
    return [e["type"] for e in events]


def test_normal_flow_events_and_history():
    svc = ChatService(make_cfg(), client=FakeClient())
    events = list(svc.send("Wie ist der Status?"))
    assert types(events) == ["route", "state", "state", "token", "token", "done", "state"]
    assert events[0]["lane"] == "general"
    assert events[1]["value"] == "think" and events[2]["value"] == "speak"
    assert events[-2]["tps"] == 87.0
    assert events[-1]["value"] == "idle"
    assert svc.history[-1] == {"role": "assistant", "content": "Sehr wohl."}


def test_heavy_swap_order():
    client = FakeClient()
    svc = ChatService(make_cfg(), client=client)
    events = list(svc.send("egal", force_7b=True))
    assert client.calls == [
        ("unload", "g3"),
        ("unload", "c3"),
        ("chat", "h7", 0),          # 7B selbst nicht resident halten
        ("preload", "g3", "30m"),   # 3B-Paar zurückladen (ADR 0002)
        ("preload", "c3", "30m"),
    ]
    phases = [e["phase"] for e in events if e["type"] == "swap"]
    assert phases == ["unload_pair", "restore_pair"]


def test_unavailable_before_first_token_is_fatal():
    svc = ChatService(make_cfg(), client=FakeClient(fail=OllamaUnavailable("down")))
    events = list(svc.send("Hallo"))
    err = next(e for e in events if e["type"] == "error")
    assert err["fatal"] and "Mainframe" in err["message"]
    assert events[-1] == {"type": "state", "value": "idle"}
    assert svc.history == []  # unbeantwortete Frage nicht behalten


def test_stream_abort_keeps_partial_answer():
    svc = ChatService(make_cfg(), client=FakeClient(
        tokens=("Teil ", "eins "), fail=OllamaUnavailable("riss"), fail_after=2))
    events = list(svc.send("Hallo"))
    err = next(e for e in events if e["type"] == "error")
    assert not err["fatal"] and "abgerissen" in err["message"]
    assert svc.history[-1]["role"] == "assistant"
    assert svc.history[-1]["content"] == "Teil eins "


def test_missing_model_message_contains_pull_command():
    svc = ChatService(make_cfg(), client=FakeClient(fail=ModelMissing("qwen2.5:7b")))
    events = list(svc.send("egal", force_7b=True))
    err = next(e for e in events if e["type"] == "error")
    assert "ollama pull qwen2.5:7b" in err["message"] and err["fatal"]


def test_dev_profile_routes_heavy_request_to_general():
    dev = Profile(name="dev", general="g08", coder="g08", heavy=None, gpu_monitor=False)
    client = FakeClient()
    svc = ChatService(make_cfg(dev), client=client)
    events = list(svc.send("Schwerlast bitte", force_7b=True))
    assert events[0]["lane"] == "general" and events[0]["heavy_unavailable"]
    # kein Swap auf dem dev-PC
    assert [c[0] for c in client.calls] == ["chat"]
    # identisches general/coder-Modell wird nur einmal angefasst
    assert client.calls[0][1] == "g08"
