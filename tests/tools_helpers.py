"""Gemeinsame Test-Helfer für die Werkzeugsteuerung."""
import threading
from pathlib import Path

from jarvis_core import config
from jarvis_core.runtime import build_runtime

CFG = config.load(str(Path(__file__).parent.parent / "config.yaml"), "mac")


class FakeClient:
    """Liefert vorbereitete Modellantworten (message-Dicts) der Reihe nach."""
    def __init__(self, *messages):
        self.messages = list(messages)
        self.calls = []

    def chat_once(self, model, messages, **kw):
        self.calls.append((model, messages, kw))
        return self.messages.pop(0) if self.messages else {"content": "ok"}


def call(name, **args):
    return {"function": {"name": name, "arguments": args}}


def runtime(tmp_path, *messages, interactive=True):
    return build_runtime(CFG, FakeClient(*messages), interactive=interactive,
                         workspace=tmp_path / "ws", data_dir=tmp_path / "data")


def run(rt, text, answer=None):
    """Führt einen Auftrag aus; `answer(ev)` entscheidet über Bestätigungen (True/False)."""
    events = []
    for ev in rt.agent.run(text):
        events.append(ev)
        if ev["type"] == "confirm" and answer is not None:
            threading.Thread(target=rt.confirms.resolve, args=(ev["id"], answer(ev)), daemon=True).start()
    return events
