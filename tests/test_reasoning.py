"""Reasoning-Verträge: Routing, echtes HTTP-Streaming und begrenzte Werkzeugschleifen."""
import json
import threading
import time
from types import SimpleNamespace
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from jarvis_core import config
from jarvis_core.chat import ChatService
from jarvis_core.config import AppConfig, Profile, ReasoningConfig
from jarvis_core.ollama_client import (GenerationCancelled, GenerationLimit,
                                     OllamaClient, OllamaUnavailable)
from jarvis_core.router import route
from jarvis_core.reasoning import fit_tool_context
from tests.tools_helpers import CFG, FakeClient, call, run, runtime

PROFILE = Profile("test", "fast", "fast", "reasoner", False, num_ctx=8192,
                  reasoning="reasoner")


@pytest.mark.parametrize("text", ["Analysiere die Ursache", "Vergleiche zwei Lösungswege",
                                 "Warum schlägt mein Code fehl?", "Plane meine Migration",
                                 "Lege einen Ordner an und schreibe eine README",
                                 "Debug this function", "Beweise diese Aussage"])
def test_complex_requests_automatically_use_reasoner(text):
    d = route(text, False, PROFILE, ReasoningConfig())
    assert d.lane == "reasoning" and d.reasoning and not d.heavy_requested


@pytest.mark.parametrize("text", ["Hallo", "Danke", "Öffne Safari", "Wie geht es dir?"])
def test_simple_requests_stay_fast(text):
    assert route(text, False, PROFILE, ReasoningConfig()).model == "fast"


def test_on_off_and_explicit_heavy():
    assert route("Hallo", False, PROFILE, ReasoningConfig("on")).reasoning
    assert not route("Analysiere das", False, PROFILE, ReasoningConfig("off")).reasoning
    assert route("Hallo", True, PROFILE, ReasoningConfig()).lane == "heavy"
    assert not route("Analysiere das", False, replace(PROFILE, reasoning=None), ReasoningConfig()).reasoning


def test_configuration_roundtrip_and_yaml_booleans(tmp_path):
    p = tmp_path / "cfg.yaml"
    p.write_text('profile: mac\nreasoning:\n  mode: on\n  max_tokens: 1024\n  timeout_s: 60\n'
                 'profiles:\n  mac:\n    reasoning: custom-local\n')
    cfg = config.load(p)
    assert cfg.reasoning == ReasoningConfig("on", 1024, 60)
    assert cfg.profile.reasoning == "custom-local"
    p.write_text('reasoning:\n  mode: off\n')
    assert config.load(p).reasoning.mode == "off"


@pytest.mark.parametrize("kw", [{"mode": "bad"}, {"max_tokens": -1}, {"timeout_s": 0},
                                    {"timeout_s": float("nan")}, {"timeout_s": float("inf")}])
def test_invalid_reasoning_settings_rejected(kw):
    with pytest.raises(config.ConfigError):
        ReasoningConfig(**kw)


@pytest.fixture
def ollama():
    class Handler(BaseHTTPRequestHandler):
        seen = []
        metadata = {"thinking": {"values": [True, False], "default": False}}

        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.seen.append({"path": self.path, **body})
            self.send_response(200)
            self.end_headers()
            if self.path == "/api/show":
                self.wfile.write(json.dumps(self.metadata).encode())
                return
            text = body["messages"][-1].get("content", "") if body["messages"] else ""
            chunks = [({"thinking": "PRIVATE reasoning"}, False)]
            if text == "tool":
                chunks.append(({"tool_calls": [call("make_dir", path="one")]}, False))
            else:
                chunks.append(({"content": "Antwort."}, False))
            try:
                for msg, done in chunks:
                    self.wfile.write(json.dumps({"message": msg, "done": done}).encode() + b"\n")
                    self.wfile.flush()
                    if text == "slow":
                        time.sleep(0.15)
                if text != "broken":
                    self.wfile.write(json.dumps({"done": True, "eval_count": 8,
                                                "done_reason": "length" if text == "length" else "stop"}).encode() + b"\n")
            except (BrokenPipeError, ConnectionResetError):
                pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield OllamaClient(f"http://127.0.0.1:{srv.server_port}"), Handler
    srv.shutdown()
    srv.server_close()


def test_capabilities_are_discovered_and_cached(ollama):
    client, h = ollama
    assert client.thinking_control("reasoner", True) is True
    assert client.thinking_control("reasoner", False) is False
    assert len(h.seen) == 1


def test_named_thinking_levels_and_unsupported_models(ollama):
    client, h = ollama
    h.metadata = {"thinking": {"values": ["low", "medium", "high"], "default": "medium"}}
    assert client.thinking_control("levels", True) == "medium"
    assert client.thinking_control("levels", False) is None  # kein erfundenes false für always-thinking
    h.metadata = {"thinking": {"values": [False], "default": False}}
    assert client.thinking_control("plain", True) is None
    assert client.thinking_control("plain", False) is False
    h.metadata = {}
    assert client.thinking_control("old-server", True) is None


def test_thinking_stream_never_becomes_answer_text(ollama):
    client, h = ollama
    ev = list(client.chat_stream("reasoner", [{"role": "user", "content": "hi"}],
                                 think=True, num_predict=512, max_time_s=10))
    assert "PRIVATE" not in json.dumps(ev)
    assert [e["token"] for e in ev if "token" in e] == ["Antwort."]
    assert {"thinking": True} in ev and ev[-1]["done"]
    assert h.seen[-1]["think"] is True and h.seen[-1]["options"]["num_predict"] == 512


def test_tool_stream_preserves_thinking_and_calls(ollama):
    client, _ = ollama
    msg = client.chat_once("reasoner", [{"role": "user", "content": "tool"}],
                           think=True, max_time_s=10)
    assert msg["thinking"] == "PRIVATE reasoning"
    assert msg["tool_calls"][0]["function"]["name"] == "make_dir"


@pytest.mark.parametrize("stream", [False, True])
def test_token_exhaustion_is_not_success(ollama, stream):
    client, _ = ollama
    with pytest.raises(GenerationLimit, match="Tokenlimit"):
        if stream:
            list(client.chat_stream("reasoner", [{"role": "user", "content": "length"}], max_time_s=10))
        else:
            client.chat_once("reasoner", [{"role": "user", "content": "length"}], max_time_s=10)


@pytest.mark.parametrize("stream", [False, True])
def test_generation_timeout(ollama, stream):
    client, _ = ollama
    with pytest.raises(GenerationLimit, match="Zeitlimit"):
        if stream:
            list(client.chat_stream("reasoner", [{"role": "user", "content": "slow"}], max_time_s=0.05))
        else:
            client.chat_once("reasoner", [{"role": "user", "content": "slow"}], max_time_s=0.05)


def test_stream_can_be_cancelled_during_thinking(ollama):
    client, _ = ollama
    cancel = threading.Event()
    ev = client.chat_stream("reasoner", [{"role": "user", "content": "hi"}], cancel=cancel)
    assert next(ev) == {"thinking": True}
    cancel.set()
    with pytest.raises(GenerationCancelled):
        next(ev)


def test_tool_stream_without_done_is_rejected(ollama):
    client, _ = ollama
    with pytest.raises(OllamaUnavailable):
        client.chat_once("reasoner", [{"role": "user", "content": "broken"}], max_time_s=10)


def test_long_thinking_is_trimmed_before_tool_results():
    messages = [{"role": "user", "content": "Mein Ziel"},
                {"role": "assistant", "content": "", "thinking": "x" * 10000,
                 "tool_calls": [call("read_file", path="one")]},
                {"role": "tool", "content": "Wichtiges echtes Ergebnis"}]
    assert fit_tool_context(messages, [], 2048, 512)
    assert "thinking" not in messages[1] and messages[-1]["content"] == "Wichtiges echtes Ergebnis"
    messages[-1]["content"] *= 1000
    assert not fit_tool_context(messages, [], 2048, 512)


def test_chat_auto_routing_and_no_thinking_in_history(ollama):
    client, _ = ollama
    chat = ChatService(AppConfig(PROFILE), client)
    ev = list(chat.send("Analysiere das Problem", model_override="quick"))
    assert ev[0]["model"] == "reasoner"  # Schnellspur darf bewusstes Reasoning nicht überschreiben
    assert any(e["type"] == "reasoning" for e in ev)
    assert "PRIVATE" not in json.dumps(ev) + json.dumps(chat.history)
    assert chat.history[-1]["content"] == "Antwort."


def test_partial_chat_is_not_remembered_as_completed(ollama):
    client, _ = ollama
    chat = ChatService(AppConfig(PROFILE, reasoning=ReasoningConfig("on")), client)
    ev = list(chat.send("length"))
    assert any(e["type"] == "error" and "Tokenlimit" in e["message"] for e in ev)
    assert not any(e["type"] == "done" for e in ev) and not chat.history


def test_off_disables_supported_thinking_without_automatic_escalation(ollama):
    client, h = ollama
    chat = ChatService(AppConfig(PROFILE, reasoning=ReasoningConfig("off")), client)
    ev = list(chat.send("Analysiere das Problem"))
    assert ev[0]["model"] == "fast"
    request = next(r for r in h.seen if r["path"] == "/api/chat" and r["messages"])
    assert request["think"] is False


def test_reasoning_context_is_preserved_only_inside_tool_loop(tmp_path):
    rt = runtime(tmp_path, {"thinking": "PRIVATE", "tool_calls": [call("make_dir", path="one")]},
                 {"content": "Ordner steht."})
    ev = run(rt, "Erstelle einen Ordner und prüfe ihn")
    client = rt.agent.client
    assert client.calls[0][0] == CFG.profile.reasoning
    assert next(m for m in client.calls[1][1] if m.get("tool_calls"))["thinking"] == "PRIVATE"
    assert "PRIVATE" not in json.dumps(ev) and ev[-1]["ok"]


def test_tool_failure_escalates_and_keeps_failure_evidence(tmp_path):
    rt = runtime(tmp_path, {"tool_calls": [call("read_file", path="missing.txt")]},
                 {"content": "Alles erledigt."})
    ev = run(rt, "Lies die Datei missing.txt")
    assert rt.agent.client.calls[0][0] == CFG.profile.agent_model
    assert rt.agent.client.calls[1][0] == CFG.profile.reasoning
    assert not ev[-1]["ok"]


def test_step_limit_counts_calls_in_one_model_message(tmp_path):
    rt = runtime(tmp_path, {"tool_calls": [call("make_dir", path="one"), call("make_dir", path="two")]})
    rt.agent.cfg = replace(rt.cfg, tools=replace(rt.cfg.tools, max_steps=1))
    ev = run(rt, "Erstelle einen Ordner")
    assert (rt.ctx.workspace / "one").exists() and not (rt.ctx.workspace / "two").exists()
    assert not ev[-1]["ok"] and "Schrittlimit" in ev[-1]["text"]


def test_final_summary_after_last_allowed_call(tmp_path):
    rt = runtime(tmp_path, {"tool_calls": [call("make_dir", path="one")]}, {"content": "Ordner steht."})
    rt.agent.cfg = replace(rt.cfg, tools=replace(rt.cfg.tools, max_steps=1))
    ev = run(rt, "Erstelle einen Ordner")
    assert ev[-1]["ok"] and rt.agent.client.calls[-1][2]["tools"] is None


def test_identical_action_loop_is_stopped(tmp_path):
    msg = {"tool_calls": [call("make_dir", path="one")]}
    rt = runtime(tmp_path, msg, msg, msg)
    ev = run(rt, "Erstelle einen Ordner")
    assert not ev[-1]["ok"] and "Wiederholungslimit" in ev[-1]["text"]


def test_cancel_during_generation_never_executes_returned_action(tmp_path):
    rt = runtime(tmp_path)

    class CancellingClient(FakeClient):
        def chat_once(self, *a, **kw):
            rt.agent.cancel()
            return {"tool_calls": [call("make_dir", path="should-not-exist")]}

    rt.agent.client = CancellingClient()
    ev = run(rt, "Erstelle einen Ordner")
    assert not (rt.ctx.workspace / "should-not-exist").exists()
    assert not ev[-1]["ok"] and "Abgebrochen" in ev[-1]["text"]


def test_tool_generation_budget_is_shared_across_calls(tmp_path, monkeypatch):
    rt = runtime(tmp_path, {"tool_calls": [call("make_dir", path="one")]},
                 {"tool_calls": [call("make_dir", path="two")]},
                 {"tool_calls": [call("make_dir", path="three")]})
    clock = iter([0, 65, 65, 130])
    monkeypatch.setattr("jarvis_core.toolagent.time", SimpleNamespace(monotonic=lambda: next(clock)))
    ev = run(rt, "Erstelle einen Ordner")
    assert len(rt.agent.client.calls) == 2
    assert rt.agent.client.calls[1][2]["max_time_s"] == 55
    assert not ev[-1]["ok"] and "Zeitlimit" in ev[-1]["text"]
    assert not (rt.ctx.workspace / "three").exists()
