from jarvis_core.browser_intent import detect, detect_all


def test_open_known_site():
    d = detect("Öffne YouTube")
    assert d == {"tool": "open_url", "args": {"url": "https://www.youtube.com"}}


def test_open_explicit_url():
    d = detect("geh zu github.com")
    assert d == {"tool": "open_url", "args": {"url": "https://github.com"}}


def test_search_google():
    d = detect("such nach Wetter in Hamburg")
    assert d == {"tool": "search", "args": {"query": "Wetter in Hamburg", "engine": "google"}}


def test_search_youtube():
    d = detect("suche auf YouTube nach Iron Man Trailer")
    assert d == {"tool": "search", "args": {"query": "Iron Man Trailer", "engine": "youtube"}}


def test_navigation_verbs():
    assert detect("geh zurück") == {"tool": "navigate", "args": {"action": "back"}}
    assert detect("lade neu") == {"tool": "navigate", "args": {"action": "reload"}}


def test_read_page():
    assert detect("lies die Seite vor") == {"tool": "read_page", "args": {}}


def test_scroll():
    assert detect("scroll runter") == {"tool": "scroll", "args": {"direction": "down"}}


def test_play_on_youtube():
    assert detect("spiel Bohemian Rhapsody auf YouTube ab") == \
        {"tool": "play_youtube", "args": {"query": "Bohemian Rhapsody"}}
    assert detect("spiele Iron Man Theme ab") == \
        {"tool": "play_youtube", "args": {"query": "Iron Man Theme"}}
    assert detect("play Daft Punk on YouTube") == \
        {"tool": "play_youtube", "args": {"query": "Daft Punk"}}


def test_play_does_not_swallow_normal_search():
    # „such nach X" bleibt eine Suche, kein Play
    assert detect("such nach Daft Punk")["tool"] == "search"


def test_non_browser_returns_none():
    assert detect("Wie spät ist es?") is None
    assert detect("Erzähl mir einen Witz") is None


def test_open_unknown_target_becomes_search_open():
    d = detect("öffne die Seite vom Bundesministerium für Wirtschaft")
    assert d["tool"] == "search_open"
    assert "Bundesministerium" in d["args"]["query"]


def test_detect_all_compound_open_and_read():
    actions = detect_all("geh zu github.com und lies mir die Seite vor")
    assert [a["tool"] for a in actions] == ["open_url", "read_page"]


def test_detect_all_single_is_list():
    actions = detect_all("Öffne YouTube")
    assert actions == [{"tool": "open_url", "args": {"url": "https://www.youtube.com"}}]


def test_detect_all_non_browser_empty():
    assert detect_all("Erzähl mir einen Witz") == []
