import json
from jarvis_core.tools.schemas import TOOLS, TOOL_NAMES


def test_tools_are_valid_ollama_format():
    for t in TOOLS:
        assert t["type"] == "function"
        fn = t["function"]
        assert isinstance(fn["name"], str) and fn["name"]
        assert isinstance(fn["description"], str) and fn["description"]
        assert fn["parameters"]["type"] == "object"
        json.dumps(t)  # serialisierbar


def test_expected_tools_present():
    assert TOOL_NAMES == {"open_url", "search", "play_youtube", "search_open",
                          "navigate", "read_page", "click", "type_text",
                          "press_enter", "scroll", "list_tabs", "close_tab"}


def test_required_args_declared():
    by_name = {t["function"]["name"]: t["function"]["parameters"] for t in TOOLS}
    assert by_name["open_url"]["required"] == ["url"]
    assert by_name["search"]["required"] == ["query"]
    assert "uid" in by_name["click"]["required"]
