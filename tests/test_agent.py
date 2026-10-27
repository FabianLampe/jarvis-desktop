from jarvis_core.agent import BrowserAgent
from jarvis_core.config import AppConfig, Profile, BrowserConfig

HOME = Profile(name="home", general="g3", coder="g3", heavy="h7", gpu_monitor=False)


class FakeBrowser:
    def __init__(self):
        self.calls = []

    def ensure_started(self):
        self.calls.append(("start",))

    def open_url(self, url):
        self.calls.append(("open_url", url)); return "geöffnet: " + url

    def search(self, query, engine="google"):
        self.calls.append(("search", query, engine)); return "Treffer"

    def read_page(self):
        self.calls.append(("read_page",)); return "Seiteninhalt: Hallo Sir."


def types(events):
    return [e["type"] for e in events]


def test_fastpath_open_emits_action_and_done():
    cfg = AppConfig(profile=HOME, browser=BrowserConfig(enabled=True))
    br = FakeBrowser()
    agent = BrowserAgent(cfg, browser=br, client=None)
    events = list(agent.run("Öffne YouTube"))
    assert ("open_url", "https://www.youtube.com") in br.calls
    assert "action" in types(events) and types(events)[-1] == "done"
    spoken = " ".join(e.get("text", "") for e in events if e["type"] in ("action", "done"))
    assert "YouTube" in spoken or "öffne" in spoken.lower()


def test_fastpath_read_returns_content_for_tts():
    cfg = AppConfig(profile=HOME)
    br = FakeBrowser()
    agent = BrowserAgent(cfg, browser=br, client=None)
    events = list(agent.run("lies die Seite vor"))
    done = [e for e in events if e["type"] == "done"][0]
    assert "Hallo Sir" in done["text"]


class FakeClient:
    """Liefert erst einen Tool-Call, dann eine Abschlussantwort."""
    def __init__(self):
        self.n = 0

    def chat_once(self, model, messages, *, tools=None, num_ctx=2048, keep_alive="30m"):
        self.n += 1
        if self.n == 1:
            return {"role": "assistant", "content": "",
                    "tool_calls": [{"function": {"name": "search",
                                    "arguments": {"query": "Wetter", "engine": "google"}}}]}
        return {"role": "assistant", "content": "Hier ist das Wetter, Sir."}


def test_llm_toolcall_loop_executes_and_finishes():
    cfg = AppConfig(profile=HOME)
    br = FakeBrowser()
    agent = BrowserAgent(cfg, browser=br, client=FakeClient())
    events = list(agent.run("Wie wird das Wetter, schau mal nach"))
    assert any(c[0] == "search" for c in br.calls)
    assert types(events)[-1] == "done"
    assert "Wetter" in [e for e in events if e["type"] == "done"][0]["text"]


def test_step_limit_terminates():
    cfg = AppConfig(profile=HOME, browser=BrowserConfig(max_steps=2))
    br = FakeBrowser()
    br.scroll = lambda direction: "scrolled"

    class Loop:
        def chat_once(self, *a, **k):
            return {"tool_calls": [{"function": {"name": "scroll",
                                    "arguments": {"direction": "down"}}}]}

    agent = BrowserAgent(cfg, browser=br, client=Loop())
    events = list(agent.run("mach irgendwas endlos"))
    assert types(events)[-1] == "done"


def test_disabled_browser_reports_done():
    cfg = AppConfig(profile=HOME, browser=BrowserConfig(enabled=False))
    agent = BrowserAgent(cfg, browser=FakeBrowser(), client=None)
    events = list(agent.run("Öffne YouTube"))
    assert types(events) == ["done"] and "abgeschaltet" in events[0]["text"]
