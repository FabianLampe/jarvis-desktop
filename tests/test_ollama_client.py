"""OllamaClient gegen einen gemockten HTTP-Server (Streaming, 404, Verbindungsfehler)."""

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from jarvis_core.ollama_client import ModelMissing, OllamaClient, OllamaUnavailable


class FakeOllama(BaseHTTPRequestHandler):
    requests_seen: list[dict] = []
    known_models = {"qwen2.5:3b", "qwen2.5-coder:3b", "qwen2.5:7b"}

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).requests_seen.append({"path": self.path, **body})

        if self.path != "/api/chat":
            self.send_error(404)
            return
        if body["model"] not in self.known_models:
            self.send_error(404)
            return

        # Nicht-streamende Anfrage (chat_once): eine JSON-Antwort
        if body.get("stream") is False:
            last = body["messages"][-1]["content"] if body.get("messages") else ""
            if body.get("tools") and last == "TOOLCALL":
                msg = {"role": "assistant", "content": "",
                       "tool_calls": [{"function": {"name": "open_url",
                                       "arguments": {"url": "https://x.de"}}}]}
            else:
                msg = {"role": "assistant", "content": "Sehr wohl, Sir."}
            self._json({"message": msg, "done": True})
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        if not body.get("messages"):  # Load/Unload-Aufruf
            self.wfile.write(json.dumps({"done": True}).encode() + b"\n")
            return
        for tok in ("Sehr ", "wohl, ", "Sir."):
            self.wfile.write(json.dumps(
                {"message": {"role": "assistant", "content": tok}, "done": False}
            ).encode() + b"\n")
        self.wfile.write(json.dumps(
            {"message": {"role": "assistant", "content": ""}, "done": True,
             "eval_count": 30, "eval_duration": 1_000_000_000}
        ).encode() + b"\n")

    def _json(self, obj):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(obj).encode() + b"\n")

    def log_message(self, *_):  # Testlog still halten
        pass


@pytest.fixture()
def server():
    FakeOllama.requests_seen = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_chat_stream_tokens_and_tps(server):
    client = OllamaClient(server)
    events = list(client.chat_stream(
        "qwen2.5:3b", [{"role": "user", "content": "Status?"}]))
    tokens = [e["token"] for e in events if "token" in e]
    assert "".join(tokens) == "Sehr wohl, Sir."
    done = events[-1]
    assert done["done"] is True and done["tps"] == pytest.approx(30.0)


def test_chat_once_returns_tool_calls(server):
    client = OllamaClient(server)
    msg = client.chat_once("qwen2.5:3b", [{"role": "user", "content": "TOOLCALL"}],
                           tools=[{"type": "function", "function": {"name": "open_url",
                                   "parameters": {"type": "object", "properties": {}}}}])
    assert msg["tool_calls"][0]["function"]["name"] == "open_url"


def test_chat_once_plain_text(server):
    client = OllamaClient(server)
    msg = client.chat_once("qwen2.5:3b", [{"role": "user", "content": "Hallo"}])
    assert isinstance(msg.get("content", ""), str) and msg["content"]


def test_missing_model_raises(server):
    client = OllamaClient(server)
    with pytest.raises(ModelMissing) as ei:
        list(client.chat_stream("nicht-da:1b", [{"role": "user", "content": "?"}]))
    assert ei.value.model == "nicht-da:1b"


def test_unreachable_server_raises():
    # Reservierten, sofort wieder geschlossenen Port verwenden
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    client = OllamaClient(f"http://127.0.0.1:{port}", connect_timeout=0.3)
    with pytest.raises(OllamaUnavailable):
        list(client.chat_stream("qwen2.5:3b", [{"role": "user", "content": "?"}]))


def test_unload_and_preload_payloads(server):
    client = OllamaClient(server)
    assert client.unload("qwen2.5:3b")
    assert client.preload("qwen2.5:7b", keep_alive="30m")
    unload_req = FakeOllama.requests_seen[0]
    preload_req = FakeOllama.requests_seen[1]
    assert unload_req["keep_alive"] == 0 and unload_req["messages"] == []
    assert preload_req["keep_alive"] == "30m" and preload_req["messages"] == []
