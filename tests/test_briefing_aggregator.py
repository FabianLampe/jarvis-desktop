from jarvis_core.briefing.aggregator import build_script


def test_script_mentions_all_sections():
    data = {
        "datetime": "Dienstag, 16. Juni, 22 Uhr 30",
        "weather": {"temp_now": 14, "temp_max": 18, "temp_min": 9, "text": "bewölkt", "ort": "Hamburg"},
        "mails": [{"sender": "Alice", "subject": "Rechnung", "snippet": "x"},
                  {"sender": "Bob", "subject": "Meeting", "snippet": "y"}],
        "issues": [{"number": 5, "title": "Bug A", "labels": []}],
        "system": {"cpu": 12, "ram": 40, "vram": 55},
    }
    script = build_script(data)
    text = " ".join(script)
    assert "Dienstag" in text
    assert "Hamburg" in text and "14" in text
    assert "2" in text and "Alice" in text          # zwei Mails, erster Absender
    assert "5" in text or "Bug A" in text            # Issue
    assert script and all(isinstance(s, str) for s in script)


def test_script_handles_missing_sources():
    data = {"datetime": "heute", "weather": None, "mails": None, "issues": None,
            "system": {"cpu": 5, "ram": 30, "vram": None}}
    script = build_script(data)
    text = " ".join(script).lower()
    assert "erreiche ich gerade" in text or "keine" in text  # graceful
