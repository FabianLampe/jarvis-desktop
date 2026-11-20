"""Kalender-Werkzeuge: ein Zeitraum = ein Termin, nichts in der Vergangenheit, Löschen/Ändern mit Bestätigung."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from jarvis_core.tools.registry import CONFIRM
from tests.tools_helpers import runtime

TZ = ZoneInfo("Europe/Berlin")


class Call:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FakeEvents:
    def __init__(self, items):
        self.items, self.deleted, self.inserted, self.patched = items, [], [], []

    def list(self, **kw):
        lo, hi = datetime.fromisoformat(kw["timeMin"]), datetime.fromisoformat(kw["timeMax"])
        hits = [e for e in self.items if lo <= datetime.fromisoformat(e["start"]["dateTime"]) < hi]
        return Call(lambda: {"items": hits})

    def insert(self, calendarId, body):
        self.inserted.append(body)
        return Call(lambda: {"id": f"new{len(self.inserted)}"})

    def delete(self, calendarId, eventId):
        self.deleted.append(eventId)
        return Call(lambda: None)

    def patch(self, calendarId, eventId, body):
        self.patched.append((eventId, body))
        return Call(lambda: {})


class FakeCalendar:
    def __init__(self, items=()):
        self.events_api = FakeEvents(list(items))

    def require_ready(self):
        pass

    def service(self):
        api = self.events_api
        return type("Service", (), {"events": lambda self: api})()


def event(eid, title, start, hours=1):
    return {"id": eid, "summary": title, "start": {"dateTime": start.isoformat()},
            "end": {"dateTime": (start + timedelta(hours=hours)).isoformat()}}


@pytest.fixture
def cal(tmp_path):
    rt = runtime(tmp_path)
    tomorrow = (datetime.now(TZ) + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    fake = FakeCalendar([event("a", "Jim", tomorrow.replace(hour=18)), event("b", "Jim", tomorrow.replace(hour=20)),
                         event("c", "Zahnarzt", tomorrow.replace(hour=9))])
    rt.ctx.services["integrations"] = {"google_calendar": fake}
    return rt, fake


def execute(rt, name, **args):
    return rt.registry.execute(rt.ctx, name, args, 4000)


def test_a_time_span_is_one_event(cal):
    rt, fake = cal
    r = execute(rt, "calendar_create", title="Jim", start="morgen 18 Uhr", end="morgen 20 Uhr")
    assert r.ok, r.text
    (body,) = fake.events_api.inserted
    start, end = (datetime.fromisoformat(body[k]["dateTime"]) for k in ("start", "end"))
    assert (start.hour, end.hour) == (18, 20) and end - start == timedelta(hours=2)


def test_past_start_is_refused_with_a_question_for_sir(cal):
    rt, fake = cal
    past = (datetime.now(TZ) - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M")
    r = execute(rt, "calendar_create", title="Jim", start=past)
    assert not r.ok and "Vergangenheit" in r.text and "welcher Tag" in r.text
    assert not fake.events_api.inserted


def test_end_before_start_is_refused(cal):
    rt, _ = cal
    r = execute(rt, "calendar_create", title="Jim", start="morgen 20 Uhr", end="morgen 18 Uhr")
    assert not r.ok and "nicht nach dem Beginn" in r.text


def test_delete_removes_all_matching_events_and_the_confirmation_lists_them(cal):
    rt, fake = cal
    assessment = rt.registry.assess(rt.ctx, "calendar_delete", {"title": "jim"})
    assert assessment.level == CONFIRM and assessment.summary.count("„Jim“") == 2
    r = execute(rt, "calendar_delete", title="jim")
    assert r.ok and sorted(fake.events_api.deleted) == ["a", "b"]


def test_delete_unknown_title_says_so(cal):
    rt, fake = cal
    r = execute(rt, "calendar_delete", title="Friseur")
    assert not r.ok and "Kein Termin" in r.text and not fake.events_api.deleted


def test_update_keeps_duration_and_asks_when_ambiguous(cal):
    rt, fake = cal
    r = execute(rt, "calendar_update", title="Jim", start="übermorgen 10 Uhr")
    assert not r.ok and "Mehrere Termine" in r.text
    r = execute(rt, "calendar_update", title="Zahnarzt", start="übermorgen 10 Uhr", new_title="Zahnarzt Kontrolle")
    assert r.ok, r.text
    eid, body = fake.events_api.patched[-1]
    start, end = (datetime.fromisoformat(body[k]["dateTime"]) for k in ("start", "end"))
    assert eid == "c" and start.hour == 10 and end - start == timedelta(hours=1)
    assert body["summary"] == "Zahnarzt Kontrolle"


def test_delete_all_events_of_a_day_without_title(cal):
    rt, fake = cal
    assessment = rt.registry.assess(rt.ctx, "calendar_delete", {"day": "morgen"})
    assert assessment.level == CONFIRM and assessment.summary.count("„") == 3     # alle drei Termine aufgelistet
    r = execute(rt, "calendar_delete", day="morgen")
    assert r.ok and sorted(fake.events_api.deleted) == ["a", "b", "c"]


def test_delete_needs_title_or_day(cal):
    rt, fake = cal
    r = execute(rt, "calendar_delete")
    assert not r.ok and "Titel oder Tag" in r.text and not fake.events_api.deleted


def test_compound_calendar_words_reach_the_calendar_tools():
    from jarvis_core.tool_intent import select_groups
    assert "integrations" in select_groups("Jarvis, kannst du die Kalendereinträge löschen für heute?")
    assert "integrations" in select_groups("Verschieb meinen Zahnarzttermin auf Freitag")
    assert "integrations" not in select_groups("Öffne das Terminal")


def test_confirmation_shows_the_resolved_appointment_in_plain_german(cal):
    rt, _ = cal
    a = rt.registry.assess(rt.ctx, "calendar_create", {"title": "Jim", "start": "morgen 18 Uhr", "end": "morgen 20 Uhr"})
    day = datetime.now(TZ) + timedelta(days=1)
    assert a.level == CONFIRM and f"{day.day}. " in a.summary and "18 bis 20 Uhr" in a.summary


def test_invalid_appointment_is_never_put_in_front_of_sir(tmp_path):
    """Vergangenheit ⇒ keine Bestätigung, sondern ein Fehler, aus dem das Modell eine Rückfrage macht."""
    from tests.tools_helpers import call, run
    past = (datetime.now(TZ) - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M")
    rt = runtime(tmp_path, {"tool_calls": [call("calendar_create", title="Jim", start=past)]},
                 {"content": "Für welchen Tag soll ich den Termin eintragen, Sir?"})
    rt.ctx.services["integrations"] = {"google_calendar": FakeCalendar()}
    events = run(rt, "Trag Jim um 18 Uhr in den Kalender ein", answer=lambda ev: True)
    assert not any(e["type"] == "confirm" for e in events)
    assert "welchen Tag" in events[-1]["text"]


def test_deleting_something_that_does_not_exist_asks_nothing(cal):
    rt, _ = cal
    from jarvis_core.tools.registry import InvalidArgs
    with pytest.raises(InvalidArgs):
        rt.registry.assess(rt.ctx, "calendar_delete", {"title": "Friseur"})


def test_network_questions_get_terminal_tools():
    from jarvis_core.tool_intent import select_groups
    for text in ("Gib mir Informationen über das Netzwerk, mit dem ich verbunden bin.",
                 "Wie ist meine IP-Adresse?", "Mit welchem WLAN bin ich verbunden?", "Welcher DNS-Server ist eingestellt?"):
        assert "terminal" in select_groups(text), text
    assert "terminal" not in select_groups("Wie wird das Wetter morgen?")
