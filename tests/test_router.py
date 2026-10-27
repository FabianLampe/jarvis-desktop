from jarvis_core.config import Profile
from jarvis_core.router import route

HOME = Profile(name="home", general="qwen2.5:3b", coder="qwen2.5-coder:3b",
               heavy="qwen2.5:7b", gpu_monitor=True)
DEV = Profile(name="dev", general="qwen3.5:0.8b", coder="qwen3.5:0.8b",
              heavy=None, gpu_monitor=False)


def test_force_7b_routes_heavy():
    d = route("Wie spät ist es?", force_7b=True, profile=HOME)
    assert d.lane == "heavy" and d.model == "qwen2.5:7b" and d.heavy_requested


def test_heavy_keyword_routes_heavy():
    for text in ("Schwerlast: analysiere das Dokument",
                 "Mach bitte eine tiefe Recherche zu GaN-Ladegeräten"):
        d = route(text, force_7b=False, profile=HOME)
        assert d.lane == "heavy", text


def test_code_block_routes_coder():
    d = route("Was macht das hier?\n```python\nprint(1)\n```", False, HOME)
    assert d.lane == "coder" and d.model == "qwen2.5-coder:3b"


def test_code_words_route_coder():
    for text in ("Schreib mir eine Funktion, die CSV einliest",
                 "Da ist ein Bug in meinem Skript",
                 "TypeError: 'NoneType' object is not iterable — was tun?",
                 "def parse(line): fertig machen bitte"):
        assert route(text, False, HOME).lane == "coder", text


def test_plain_chat_routes_general():
    for text in ("Wie wird das Wetter morgen?",
                 "Erzähl mir einen Witz",
                 "Was steht heute an?"):
        assert route(text, False, HOME).lane == "general", text


def test_no_false_positive_on_compound_words():
    # "Barcode" / "decodieren" enthalten 'code' nur als Teilwort
    assert route("Kannst du den Barcode auf dem Foto lesen?", False, HOME).lane == "general"
    assert route("Was heißt decodieren auf Englisch?", False, HOME).lane == "general"


def test_dev_profile_without_heavy_falls_back():
    d = route("Schwerlast: bitte gründlich!", force_7b=True, profile=DEV)
    assert d.lane == "general" and d.heavy_unavailable and d.heavy_requested

    d2 = route("Schwerlast: fixe den Bug im Code", force_7b=True, profile=DEV)
    assert d2.lane == "coder" and d2.heavy_unavailable
