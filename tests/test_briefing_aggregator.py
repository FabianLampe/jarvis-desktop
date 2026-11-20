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


def test_script_reads_todays_calendar():
    data = {"datetime": "heute", "weather": None, "mails": [], "issues": [], "system": {},
            "calendar": True,
            "events": [{"time": "09:00", "title": "Daily"}, {"time": "14:30", "title": "Projekt Alpha"},
                       {"time": None, "title": "Urlaub Team"}]}
    text = " ".join(build_script(data))
    assert "Heute stehen 3 Termine an." in text
    assert "Um 9 Uhr: Daily." in text and "Um 14 Uhr 30: Projekt Alpha." in text
    assert "Ganztägig: Urlaub Team." in text


def test_script_calendar_states():
    base = {"datetime": "heute", "weather": None, "mails": [], "issues": [], "system": {}}
    assert "keine Termine" in " ".join(build_script({**base, "calendar": True, "events": []}))
    assert "Kalender erreiche ich" in " ".join(build_script({**base, "calendar": True, "events": None}))
    assert "Kalender" not in " ".join(build_script({**base, "calendar": False, "events": None}))


def test_today_events_asks_for_today_only_and_converts_to_local_time():
    from jarvis_core.integrations.services import today_events
    seen = {}

    class Events:
        def list(self, **kw):
            seen.update(kw)
            return self
        def execute(self):
            return {"items": [{"summary": "Daily", "start": {"dateTime": "2026-11-02T08:00:00Z"}},
                              {"summary": "Urlaub", "start": {"date": "2026-11-02"}},
                              {"start": {"dateTime": "2026-11-02T15:30:00+01:00"}}]}

    class Calendar:
        def service(self):
            return type("S", (), {"events": lambda self: Events()})()

    events = today_events(Calendar(), "Europe/Berlin")
    assert events == [{"time": "09:00", "title": "Daily"}, {"time": None, "title": "Urlaub"},
                      {"time": "15:30", "title": "(ohne Titel)"}]
    assert seen["calendarId"] == "primary" and seen["singleEvents"] is True
    assert seen["timeMin"].endswith(("T00:00:00+01:00", "T00:00:00+02:00"))
