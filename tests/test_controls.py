"""Mute, Kontextfenster, neuer Chat, Echo-Schutz, Einzelinstanz."""
import asyncio
import threading
import time

import numpy as np

from jarvis_app.bridge import Bridge
from jarvis_app.ws_server import VoiceHub
from jarvis_core import singleton
from jarvis_core.chat import ChatService
from tests.test_bridge import FakeChat, FakeHub, FakeWindow
from tests.tools_helpers import CFG
from voice_daemon.commands import is_own_echo
from voice_daemon.ears import Ears
from voice_daemon.link import Link


def free_port():
    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def test_mute_roundtrip_hub_to_daemon_and_back():
    port = free_port()
    hub = VoiceHub("127.0.0.1", port)
    confirmed = []
    hub.start(on_state=lambda *a: None, on_clients_changed=lambda c: None, on_muted=confirmed.append)
    time.sleep(0.5)
    hub.set_muted(True)                                    # schon vor der Verbindung gemerkt
    link = Link(f"ws://127.0.0.1:{port}")
    got = []
    link.on_mute = lambda m: (got.append(m), link.send_muted(m))
    threading.Thread(target=lambda: asyncio.run(link.run()), daemon=True).start()
    for _ in range(60):
        if got and confirmed: break
        time.sleep(0.1)
    assert got == [True] and confirmed == [True] and hub.muted


def test_ears_mute_closes_listening():
    ears = Ears({"wake_word": "hey_jarvis", "wake_threshold": 0.5, "max_utterance_s": 5, "silence_after_s": 1,
                 "clap_enabled": False}, on_listen_start=lambda: None, on_audio=lambda *a: None)
    assert not ears.muted
    ears.trigger_listen(); ears.set_muted(True)
    assert ears.muted and not ears._listen_now.is_set()
    ears.set_muted(False); assert not ears.muted


def test_ears_quiet_tail_after_speaking():
    ears = Ears({"wake_word": "hey_jarvis", "wake_threshold": 0.5, "max_utterance_s": 5, "silence_after_s": 1,
                 "echo_tail_s": 0.5, "clap_enabled": True}, on_listen_start=lambda: None, on_audio=lambda *a: None)
    ears._q.put(np.zeros(1280, dtype=np.int16))
    ears.suppress(True); ears.suppress(False)
    assert ears._q.empty() and ears._quiet_until > time.monotonic()      # Puffer geleert, Nachlauf aktiv


def test_own_voice_is_recognised_as_echo():
    said = ["Es ist Mittwoch, 28. Oktober 2026. Arbeitsspeicher 54 Prozent.", "Guten Abend, Sir."]
    assert is_own_echo("Es ist Mittwoch, 28. Oktober 2017. Arbeitsspeicher, 54%.", said)
    assert is_own_echo("Arbeitsspeicher 54 Prozent", said)
    assert not is_own_echo("Öffne bitte Safari", said)
    assert not is_own_echo("Wie spät ist es", said)
    assert not is_own_echo("Ja", said)


def test_single_instance_lock(tmp_path):
    assert singleton.acquire("t-app", tmp_path)
    import subprocess, sys
    code = ("from pathlib import Path; from jarvis_core import singleton; "
            f"import sys; sys.exit(0 if singleton.acquire('t-app', Path(r'{tmp_path}')) else 7)")
    assert subprocess.run([sys.executable, "-c", code], cwd=".").returncode == 7      # zweite Instanz abgewiesen


def test_context_info_and_trim():
    class C:                                              # Ollama-Client mit gemessener Nutzung
        last_usage = {"prompt": 3000, "completion": 400}
    chat = ChatService(CFG, client=C())
    assert chat.context_info()["source"] == "geschätzt" and chat.context_info()["messages"] == 0
    chat.history += [{"role": "user", "content": "x" * 100}, {"role": "assistant", "content": "y" * 100}]
    info = chat.context_info()
    assert info["used"] == 3400 and info["total"] == CFG.profile.num_ctx and info["source"] == "gemessen"
    chat.history = [{"role": "user", "content": "wort " * 800} for _ in range(20)]
    removed = chat._trim()
    assert removed > 0 and sum(chat.est_tokens(m["content"]) for m in chat.history) <= CFG.profile.num_ctx * 0.7


def test_bridge_new_chat_mute_and_status(tmp_path):
    hub = FakeHub(); hub.set_muted = lambda v: True
    chat = ChatService(CFG, client=type("C", (), {"last_usage": None, "ping": lambda s: True})())
    b = Bridge(CFG, chat=chat, hub=hub, layout_path=tmp_path / "l.json")
    b._window = FakeWindow()
    chat.history.append({"role": "user", "content": "hallo"})
    assert b.get_status()["context"]["messages"] == 1
    assert b.set_mute(True)["muted"] is True and b.get_status()["muted"] is True
    r = b.new_chat()
    assert r["ok"] and chat.history == [] and r["context"]["messages"] == 0
    assert b.set_mute(False)["muted"] is False
