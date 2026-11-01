"""Regressionen aus dem zweiten Review: echte Fehlerfälle statt nur Happy Paths."""
import json
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis_core import browser_intent, config
from jarvis_core.chat import ChatService
from jarvis_core.fillers import FillerSource
from jarvis_core.ollama_client import (GenerationCancelled, ModelMissing, OllamaClient,
                                     OllamaError, OllamaUnavailable)
from jarvis_core.tool_intent import fast_path, select_groups
from jarvis_core.tools import mac_ui_tools, remote_tools
from jarvis_core.tools.process import ProcessRunner
from jarvis_core.tools.registry import Tool, ToolError
from tests.tools_helpers import CFG, run, runtime


def call(tool_name, **args):
    return {"function": {"name": tool_name, "arguments": args}}


@pytest.mark.parametrize("content", ['{"enabled": true, "values": [1, 2]}', '["a", "b"]', ''])
def test_writing_json_or_empty_text_preserves_exact_bytes(tmp_path, content):
    rt = runtime(tmp_path, {"tool_calls": [call("write_file", path="config.json", content=content)]},
                 {"content": "Fertig."})
    ev = run(rt, "Schreibe die Datei config.json")
    assert ev[-1]["ok"], ev[-1]
    assert (rt.ctx.workspace / "config.json").read_text() == content


def test_structured_string_coercion_still_supports_array_arguments(tmp_path):
    rt = runtime(tmp_path, {"tool_calls": [call("run_command", argv='["echo", "hello"]')]},
                 {"content": "Fertig."})
    assert run(rt, "Führe den Befehl aus")[-1]["ok"]


def test_false_as_string_cannot_overwrite_existing_artifact(tmp_path):
    rt = runtime(tmp_path)
    p = rt.ctx.workspace / "a.pdf"
    p.write_bytes(b"keep")
    args = {"output": "a.pdf", "overwrite": "false"}
    assert rt.registry.assess(rt.ctx, "create_document", args).level == "deny"
    res = rt.registry.execute(rt.ctx, "create_document", args, 1000)
    assert not res.ok and p.read_bytes() == b"keep"


@pytest.mark.parametrize("message", [["not", "a", "message"], {"tool_calls": [{"bad": "shape"}]},
                                   {"tool_calls": [{"function": {"name": 3}}]}])
def test_bad_model_frames_fail_without_crashing(tmp_path, message):
    rt = runtime(tmp_path, message)
    assert not run(rt, "Zeige die Systeminfo")[-1]["ok"]


def test_invalid_json_arguments_never_become_an_empty_tool_call(tmp_path):
    rt = runtime(tmp_path, {"tool_calls": [{"function": {"name": "system_info", "arguments": "{broken"}}]},
                 {"content": "Fertig."})
    called = []
    rt.registry.register(Tool("system_info", "system", "test", {}, (), lambda c, a: called.append(1) or "ok"))
    assert not run(rt, "Zeige die Systeminfo")[-1]["ok"]
    assert not called


def test_tool_json_inside_explanation_is_not_executed(tmp_path):
    content = 'Dieses Beispiel zeigt einen Aufruf: {"name":"make_dir","arguments":{"path":"unwanted"}}'
    rt = runtime(tmp_path, {"content": content})
    run(rt, "Erkläre das Werkzeug zum Erstellen eines Ordners")
    assert not (rt.ctx.workspace / "unwanted").exists()


def test_unknown_arguments_are_rejected_instead_of_silently_ignored(tmp_path):
    rt = runtime(tmp_path)
    res = rt.registry.execute(rt.ctx, "system_info", {"invented_option": True}, 1000)
    assert not res.ok


@pytest.mark.parametrize("prompt", ["Lies die Datei config.json", "Lies meine README vor",
                                     "Öffne den Ordner Projekt", "Öffne YouTube und erstelle eine Datei"])
def test_browser_shortcut_cannot_swallow_file_or_mixed_requests(prompt):
    assert browser_intent.detect_all(prompt) == []


def test_search_phrase_keeps_conjunctions():
    actions = browser_intent.detect_all("Suche nach Forschung und Entwicklung")
    assert len(actions) == 1 and actions[0]["args"]["query"] == "Forschung und Entwicklung"
    assert browser_intent.detect_all("Suche nach LLMs und fasse die Ergebnisse zusammen") == []
    assert "research" in select_groups("Suche nach LLMs und fasse die Ergebnisse zusammen")


@pytest.mark.parametrize("prompt", ["Erstelle einen PDF-Bericht zum Wetter", "Wie wird das Wetter und wie spät ist es?",
                                     "Wie spät ist es und lies meine Datei", "Wie wird das Wetter morgen und übermorgen?"])
def test_weather_and_clock_shortcuts_do_not_drop_subtasks(prompt):
    assert fast_path(prompt) is None


def test_remembering_a_fact_about_weather_is_not_a_weather_lookup():
    assert fast_path("Merke dir, dass ich Wetter mag")[0]["tool"] == "memory_save"


def test_cancelling_before_a_step_does_not_open_confirmation(tmp_path):
    rt = runtime(tmp_path)
    rt.confirms.ttl_s = .01
    rt.ctx.cancel.set()
    outcomes = []
    ev = list(rt.agent._step("delete_path", {"path": "a.txt"}, outcomes, []))
    assert not any(e["type"] == "confirm" for e in ev)
    assert outcomes[-1].get("cancelled")


def test_cancel_race_while_creating_confirmation_does_not_wait(tmp_path, monkeypatch):
    rt = runtime(tmp_path)
    rt.confirms.ttl_s = .01
    original = rt.confirms.create
    def create(*a):
        pending = original(*a)
        rt.ctx.cancel.set()
        return pending
    monkeypatch.setattr(rt.confirms, "create", create)
    outcomes = []
    ev = list(rt.agent._step("delete_path", {"path": "a.txt"}, outcomes, []))
    assert not any(e["type"] == "confirm" for e in ev) and not rt.agent._pending_ids


def test_task_cancelled_before_run_never_clears_its_cancellation(tmp_path):
    from jarvis_core.runtime import spawn_agent
    rt = runtime(tmp_path)
    agent = spawn_agent(rt)
    agent.cancel()
    ev = list(agent.run("Merke dir, dass dies nicht gespeichert werden darf"))
    assert not ev[-1]["ok"] and not (rt.ctx.data_dir / "memory.db").exists()


@pytest.mark.parametrize("failure", [None, ModelMissing("fast"), OllamaUnavailable("offline")])
def test_reset_during_generation_never_restores_old_history(failure):
    class Client:
        def chat_stream(self, *a, **kw):
            chat.reset()
            if failure:
                raise failure
            yield {"token": "old text"}
            yield {"done": True}
    cfg = config.AppConfig(config.Profile("test", "fast", "fast", None, False))
    chat = ChatService(cfg, Client())
    ev = list(chat.send("old question"))
    assert not chat.history
    assert not any(e["type"] in ("token", "done", "error") for e in ev)


def test_failure_removes_only_its_own_question():
    class Client:
        def chat_stream(self, *a, **kw):
            chat.history.append({"role": "user", "content": "new question"})
            raise OllamaUnavailable("offline")
            yield
    cfg = config.AppConfig(config.Profile("test", "fast", "fast", None, False))
    chat = ChatService(cfg, Client())
    list(chat.send("old question"))
    assert chat.history == [{"role": "user", "content": "new question"}]


def test_cancel_covers_overlapping_chat_generations():
    class Client:
        def chat_stream(self, *a, **kw):
            yield {"token": "partial"}
            yield {"done": True}
    chat = ChatService(CFG, Client())
    streams = [chat.send("Hallo A"), chat.send("Hallo B")]
    for stream in streams:
        for ev in stream:
            if ev["type"] == "token":
                break
    chat.cancel()
    for stream in streams:
        assert not any(e["type"] == "done" for e in stream)


def test_new_chat_does_not_remember_a_cancelled_task(tmp_path):
    from tests.test_bridge_tools import make
    b = make(tmp_path)
    class Client:
        def chat_once(self, *a, **kw):
            b.new_chat()
            return {"content": "late answer"}
    b._rt.agent.client = Client()
    b._run_tool_stream("old", "Erstelle eine Datei", False, False)
    assert b._chat.history == []
    assert not any("late answer" in c for c in b._window.js_calls)


def test_file_read_uses_file_tools_in_the_bridge(tmp_path):
    from tests.test_bridge_tools import make
    b = make(tmp_path, {"tool_calls": [call("read_file", path="a.txt")]}, {"content": "Dateiinhalt stimmt."})
    class UnexpectedBrowser:
        def run(self, *a):
            pytest.fail("Lokale Dateianfrage wurde an den Browser geschickt")
            yield
    b._browser_agent = UnexpectedBrowser()
    (tmp_path / "ws/a.txt").write_text("file data")
    b._run_stream("s", "Lies die Datei a.txt", False)
    assert "Dateiinhalt stimmt" in "\n".join(b._window.js_calls)


def test_weather_and_file_subtasks_both_execute_in_the_bridge(tmp_path):
    from tests.test_bridge_tools import make
    b = make(tmp_path, {"tool_calls": [call("weather", when="heute"), call("write_file", path="wetter.txt", content="Sonne")]},
             {"content": "Wetter gespeichert."})
    b._rt.registry.register(Tool("weather", "weather", "test", {"when": {"type": "string"}}, (), lambda c, a: "Sonne"))
    b._run_stream("s", "Schreibe das Wetter in die Datei wetter.txt", False)
    assert (tmp_path / "ws/wetter.txt").read_text() == "Sonne"


@pytest.mark.parametrize("raw", [b"not-json", b"[]", b'{"message":{"content":42}}', b'{"done":"false"}'])
def test_ollama_protocol_errors_are_explicit(raw, monkeypatch):
    client = OllamaClient()
    response = SimpleNamespace(iter_lines=lambda **kw: iter([raw]), close=lambda: None)
    monkeypatch.setattr(client, "_post", lambda *a, **kw: response)
    with pytest.raises(OllamaError):
        list(client.chat_stream("test", []))


def test_cancelled_ollama_call_never_opens_a_request(monkeypatch):
    client = OllamaClient()
    cancel = threading.Event()
    cancel.set()
    monkeypatch.setattr(client, "_post", lambda *a, **kw: pytest.fail("cancelled request was sent"))
    with pytest.raises(GenerationCancelled):
        list(client.chat_stream("model", [], cancel=cancel))
    with pytest.raises(GenerationCancelled):
        client.chat_once("model", [], cancel=cancel)


def test_weather_filler_works_shortly_after_boot_and_keeps_rotating(monkeypatch):
    import jarvis_core.fillers as fillers
    monkeypatch.setattr(fillers, "time", SimpleNamespace(monotonic=lambda: 30))
    calls = []
    source = FillerSource(weather=lambda: calls.append(1) or "Hamburg, 12 Grad", seed=1)
    lines = [source.next("Recherche") for _ in range(4)]
    assert "Hamburg" in lines[1] and "Hamburg" in lines[3] and calls == [1]


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), -1])
def test_invalid_process_timeout_does_not_spawn(tmp_path, monkeypatch, timeout):
    rt = runtime(tmp_path)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: pytest.fail("unbounded process was started"))
    res = ProcessRunner(rt.ctx.platform).run(["echo", "x"], rt.ctx.workspace, timeout_s=timeout)
    assert not res.ok and "Zeitlimit" in res.error


def test_github_issues_exclude_pull_requests_and_report_pagination(tmp_path, monkeypatch):
    rt = runtime(tmp_path)
    response = SimpleNamespace(status_code=200, ok=True, links={"next": {}}, json=lambda:
        [{"number": 1, "title": "PR", "pull_request": {}}, {"number": 2, "title": "Issue"}])
    response.links["next"] = {"url": "https://example.invalid/page2"}
    monkeypatch.setattr(remote_tools.requests, "request", lambda *a, **kw: response)
    res = rt.registry.execute(rt.ctx, "github", {"action": "issues"}, 5000)
    assert res.ok and [i["number"] for i in res.data["items"]] == [2] and res.data["truncated"]


def test_github_redirect_is_not_success_and_write_timeout_is_uncertain(tmp_path, monkeypatch):
    rt = runtime(tmp_path)
    response = SimpleNamespace(status_code=302, ok=True, json=lambda: {"number": 1})
    monkeypatch.setattr(remote_tools.requests, "request", lambda *a, **kw: response)
    assert not rt.registry.execute(rt.ctx, "github", {"action": "issues"}, 1000).ok
    monkeypatch.setenv("JARVIS_GITHUB_TOKEN", "test")
    def timeout(*a, **kw):
        raise remote_tools.requests.Timeout()
    monkeypatch.setattr(remote_tools.requests, "request", timeout)
    res = rt.registry.execute(rt.ctx, "github", {"action": "create_issue", "title": "X"}, 1000)
    assert not res.ok and "Status" in res.text and "unklar" in res.text


@pytest.mark.parametrize("body", ["ssh:\n    hosts: []", "mcp:\n    servers: []",
                                     "mcp:\n    servers:\n      x:\n        env_names: TOKEN"])
def test_nested_extension_config_is_validated(tmp_path, body):
    p = tmp_path / "c.yaml"
    p.write_text("extensions:\n  " + body + "\n")
    with pytest.raises(config.ConfigError):
        config.load(p)


def test_workflow_cannot_save_invalid_child_argument_types(tmp_path):
    rt = runtime(tmp_path)
    res = rt.registry.execute(rt.ctx, "workflow_save", {"name": "bad", "steps": [
        {"tool": "create_document", "args": {"output": "x.pdf", "overwrite": "false"}}]}, 1000)
    assert not res.ok and not (rt.ctx.data_dir / "workflows/bad.json").exists()


def test_mac_ui_cancellation_is_checked_before_ax_call():
    ui = mac_ui_tools.MacUI.__new__(mac_ui_tools.MacUI)
    ui.cancel = threading.Event()
    ui.cancel.set()
    ui.ax = SimpleNamespace(AXUIElementCopyAttributeValue=lambda *a: pytest.fail("AX called after cancel"))
    with pytest.raises(ToolError, match="abgebrochen"):
        ui.attr(1, "AXRole")


def test_failed_mac_action_invalidates_snapshot():
    ui = mac_ui_tools.MacUI.__new__(mac_ui_tools.MacUI)
    ui.app = SimpleNamespace(localizedName=lambda: "Test")
    ui.elements, ui.at = {"x": 1}, time.monotonic()
    ui.attr = lambda *a: "AXButton"
    ui.ax = SimpleNamespace(AXUIElementPerformAction=lambda *a: -25204)
    with pytest.raises(ToolError):
        ui.action({"action": "click", "id": "x"})
    assert not ui.elements and ui.at == 0


@pytest.mark.parametrize("action", ["new_chat", "cancel"])
def test_filler_is_not_spoken_after_reset_or_stop(tmp_path, action):
    from tests.test_bridge_tools import make
    b = make(tmp_path)
    b.FILLER_SILENCE_S = b.FILLER_GAP_S = 0
    def next_line(*a):
        getattr(b, action)()
        return "stale filler"
    b._fillers = SimpleNamespace(next=next_line)
    b._filler_loop("x", threading.Event())
    assert b._hub.spoken == []


@pytest.mark.parametrize("metadata", [[], {"thinking": "enabled"}, {"thinking": {"values": "medium"}},
                                      {"capabilities": None}])
def test_invalid_thinking_metadata_uses_model_default(metadata, monkeypatch):
    class Response:
        ok = True
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def json(self): return metadata
    client = OllamaClient()
    monkeypatch.setattr(client._session, "post", lambda *a, **kw: Response())
    assert client.thinking_control("test", True) is None


def test_cancelled_heavy_chat_does_not_reload_models():
    loaded = []
    class Client:
        def unload(self, model): pass
        def preload(self, model, keep_alive): loaded.append(model)
        def chat_stream(self, *a, **kw):
            chat.reset()
            yield {"token": "old"}
            yield {"done": True}
    cfg = config.AppConfig(config.Profile("test", "fast", "fast", "heavy", False))
    chat = ChatService(cfg, Client())
    list(chat.send("x", force_7b=True))
    assert loaded == [] and chat.history == []


@pytest.mark.parametrize("prompt,group", [("Teste mein Projekt", "testing"), ("Testen", "testing"),
    ("Überprüfe mein Projekt", "testing"), ("Erstelle ein Liniendiagramm", "analysis"),
    ("Erstelle ein Balkendiagramm", "analysis"), ("Zeige ein Histogramm", "analysis"),
    ("Wie wird das Wetter und wie spät ist es", "system")])
def test_common_german_phrasings_expose_the_required_tools(prompt, group):
    assert group in select_groups(prompt)
