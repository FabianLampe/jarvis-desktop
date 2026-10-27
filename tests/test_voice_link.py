"""WS-Schnittstelle App ↔ Sprach-Daemon: echter Roundtrip über websockets."""

import json
import socket
import time

import pytest
from websockets.sync.client import connect

from jarvis_app.ws_server import VoiceHub


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_for(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture()
def hub_events():
    events = []
    port = free_port()
    hub = VoiceHub("127.0.0.1", port)
    hub.start(
        on_state=lambda s, a: events.append(("state", s, a)),
        on_clients_changed=lambda c: events.append(("clients", c)),
        on_utterance=lambda t: events.append(("utterance", t)),
    )
    # Auf den Server warten
    ws = None
    end = time.monotonic() + 5
    while time.monotonic() < end:
        try:
            ws = connect(f"ws://127.0.0.1:{port}", open_timeout=1)
            break
        except OSError:
            time.sleep(0.1)
    assert ws is not None, "WS-Server nicht erreichbar"
    yield hub, ws, events
    ws.close()


def test_daemon_to_app_state_and_utterance(hub_events):
    hub, ws, events = hub_events
    ws.send(json.dumps({"type": "state", "value": "listening"}))
    ws.send(json.dumps({"type": "state", "value": "speaking", "amplitude": 0.7}))
    ws.send(json.dumps({"type": "utterance", "text": "Wie spät ist es?"}))

    assert wait_for(lambda: ("utterance", "Wie spät ist es?") in events)
    assert ("state", "listen", None) in events
    assert ("state", "speak", 0.7) in events
    assert ("clients", True) in events
    assert hub.connected


def test_app_to_daemon_speak_and_push_to_talk(hub_events):
    hub, ws, events = hub_events
    assert wait_for(lambda: hub.connected)

    assert hub.say("Sehr wohl, Sir.")
    msg = json.loads(ws.recv(timeout=5))
    assert msg == {"type": "speak", "text": "Sehr wohl, Sir."}

    assert hub.push_to_talk()
    msg2 = json.loads(ws.recv(timeout=5))
    assert msg2 == {"type": "push_to_talk"}


def test_disconnect_clears_connected(hub_events):
    hub, ws, events = hub_events
    assert wait_for(lambda: hub.connected)
    ws.close()
    assert wait_for(lambda: not hub.connected)
    assert ("clients", False) in events
