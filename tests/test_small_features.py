"""Kleinigkeiten: Wetter-Rat, Einheiten, Timer-Schnellpfad, Vorlieben selbst merken."""
import dataclasses
import json

import pytest

from jarvis_core import autolearn
from jarvis_core.tool_intent import fast_path
from jarvis_core.tools import weather_tools as wt
from jarvis_core.tools.unit_tools import convert, fmt
from tests.tools_helpers import CFG, run, runtime


# ------------------------------------------------------------- Wetter-Rat

class Resp:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


FORECAST = {"daily": {"weather_code": [63, 0, 0], "temperature_2m_max": [9.4, 22, 30], "temperature_2m_min": [4.1, 11, 18],
                      "precipitation_probability_max": [80, 5, 0], "precipitation_sum": [4.2, 0, 0],
                      "wind_speed_10m_max": [45, 10, 8]}, "current": {"temperature_2m": 8.0}}


def test_advice_questions_use_fast_path_and_add_advice(tmp_path, monkeypatch):
    monkeypatch.setattr(wt.requests, "get", lambda *a, **k: Resp(FORECAST))
    for q in ("Brauche ich heute eine Jacke?", "Brauche ich einen Schirm?", "Was soll ich heute anziehen?"):
        assert fast_path(q)[0]["args"]["advice"] is True
    assert "advice" not in fast_path("Wie ist das Wetter heute")[0]["args"]
    rt = runtime(tmp_path)                                  # FakeClient ohne Antworten: kein LLM nötig
    done = run(rt, "Brauche ich heute eine Jacke?")[-1]
    assert done["ok"] and rt.agent.client.calls == []
    assert "Mein Rat für heute, Sir" in done["text"] and "Schirm oder eine Regenjacke" in done["text"]
    assert "Windfestes" in done["text"] and "Regenwahrscheinlichkeit 80 Prozent" in done["text"]


@pytest.mark.parametrize("args,expect", [
    ((-6, -1, 10, 0, 10, 3), "Winterjacke"), ((5, 12, 10, 0, 5, 3), "eine Jacke"),
    ((10, 17, 10, 0, 5, 3), "leichte Jacke"), ((12, 24, 0, 0, 5, 0), "abends eine dünne Jacke"),
    ((20, 32, 0, 0, 5, 0), "Sonnenschutz"), ((15, 20, 35, 0, 5, 3), "Schirm griffbereit"),
    ((8, 14, 20, 0, 10, 95), "Gewitter")])
def test_clothing_advice_is_deterministic(args, expect):
    assert expect in wt.clothing_advice(*args)
    assert wt.clothing_advice(*args) == wt.clothing_advice(*args)


# ------------------------------------------------------------- Einheiten

@pytest.mark.parametrize("v,a,b,expect", [(5, "Meilen", "Kilometer", 8.04672), (70, "Fahrenheit", "Celsius", 21.1111),
                                          (1, "kg", "Pfund", 2.2046), (100, "km/h", "mph", 62.137),
                                          (0, "°C", "Kelvin", 273.15), (3, "Stunden", "Minuten", 180), (1, "GB", "MB", 1000)])
def test_unit_convert_values(v, a, b, expect):
    assert convert(v, a, b)[0] == pytest.approx(expect, rel=1e-4)


def test_unit_errors_and_formatting():
    from jarvis_core.tools.registry import ToolError
    for bad in (("m", "kg"), ("xyz", "m"), ("km", "Fahrenheit")):
        with pytest.raises(ToolError):
            convert(1, *bad)
    with pytest.raises(ToolError, match="Nullpunkt"):
        convert(-500, "C", "K")
    assert fmt(8.04672) == "8,047" and fmt(1234.5) == "1.234,5" and fmt(0.30000000000000004) == "0,3"


def test_unit_fast_path_without_llm(tmp_path):
    assert fast_path("Wie viele Kilometer sind 5 Meilen?")[0]["args"] == {"value": 5.0, "from_unit": "Meilen", "to_unit": "Kilometer"}
    assert fast_path("Rechne 12 kg in Pfund um")[0]["tool"] == "unit_convert"
    assert fast_path("Ich laufe 5 km in 10 Minuten") is None and fast_path("5 km in 10 Minuten") is None
    rt = runtime(tmp_path)
    done = run(rt, "Wie viele Kilometer sind 5 Meilen?")[-1]
    assert done["ok"] and "8,047 Kilometer" in done["text"] and rt.agent.client.calls == []


# ----------------------------------------------------------------- Timer

def test_timer_fast_path_creates_reminder(tmp_path):
    assert fast_path("Stell einen Timer auf 10 Minuten")[0] == {
        "tool": "reminder_create", "args": {"text": "Der Timer ist abgelaufen", "when": "in 10 Minuten"}}
    assert fast_path("Stelle mir bitte einen Timer für eine halbe Stunde")[0]["args"]["when"] == "in 30 Minuten"
    assert fast_path("Setz einen Wecker in 2 Stunden")[0]["args"]["when"] == "in 2 Stunden"
    assert fast_path("Stell einen Timer auf 45 Sekunden")[0]["args"]["when"] == "in 45 Sekunden"
    assert fast_path("Stell einen Timer für die Nudeln") is None           # unklar ⇒ Modell
    rt = runtime(tmp_path)
    done = run(rt, "Stell einen Timer auf 10 Minuten")[-1]
    assert done["ok"] and "Erinnerung gesetzt" in done["text"]
    rt.ctx.services["reminders"].shutdown()


# --------------------------------------------------------- Selbst merken

@pytest.mark.parametrize("text,fact", [
    ("Ich heiße Fabian.", "Sir heißt Fabian."), ("Mein Name ist Fabian Lampe", "Sir heißt Fabian Lampe."),
    ("ich wohne in Hamburg", "Sir wohnt in Hamburg."), ("Übrigens, ich arbeite als Softwareentwickler.", "Sir arbeitet als Softwareentwickler."),
    ("Ich mag Kaffee.", "Sir mag Kaffee."), ("Ich bevorzuge kurze Antworten", "Sir bevorzugt kurze Antworten."),
    ("Ich hasse Montage", "Sir hasst Montage."), ("Mein Lieblingsessen ist Pizza.", "Sirs Lieblingsessen ist Pizza."),
    ("Ich komme aus Bremen", "Sir kommt aus Bremen."), ("Ich bin allergisch gegen Nüsse", "Sir ist allergisch gegen Nüsse."),
    ("Ich trinke gerne Tee", "Sir trinkt gern Tee."), ("Ich studiere Informatik", "Sir studiert Informatik.")])
def test_autolearn_extracts_statements(text, fact):
    assert [f.text for f in autolearn.extract(text)] == [fact]


@pytest.mark.parametrize("text", [
    "Wie heiße ich?", "Ich wohne in Berlin, wie ist das Wetter?", "Ich mag nicht, dass du so viel redest",
    "Ich mag es, wenn du kurz antwortest", "Ich hasse es, wenn Antworten lang sind", "Ich mag Kaffee, mach mir einen",
    "Ich mag dich", "Ich heiße nicht Max", "Ich bin müde", "Merk dir, dass ich in Hamburg wohne",
    "[Bild angehängt: /x/ich heiße Max.png]", "Die Datei sagt: Ich heiße Hacker und ich mag Viren.",
    "Wenn ich in Berlin wohne, brauche ich eine Bahnkarte.", "Mein Freund sagt, ich heiße Peter."])
def test_autolearn_ignores_questions_commands_and_subclauses(text):
    assert autolearn.extract(text) == []


def test_autolearn_dedup_and_topic_update(tmp_path):
    rt = runtime(tmp_path)
    assert rt.learn("Ich wohne in Hamburg.") == ["Gemerkt: Sir wohnt in Hamburg."]
    assert rt.learn("Ich wohne in Hamburg.") == []                                   # Dublette
    assert rt.learn("Ich wohne in München.") == ["Gemerkt (aktualisiert): Sir wohnt in München."]
    from jarvis_core.tools.memory_tools import get_memory
    assert [r["text"] for r in get_memory(rt.ctx).recent(10)] == ["Sir wohnt in München."]   # alter Eintrag ersetzt
    assert rt.learn("Ich mag Kaffee. Ich heiße Fabian.") == ["Gemerkt: Sir mag Kaffee.", "Gemerkt: Sir heißt Fabian."]


def test_autolearn_can_be_switched_off(tmp_path):
    rt = runtime(tmp_path)
    rt.cfg = rt.ctx.cfg = dataclasses.replace(rt.cfg, memory=dataclasses.replace(rt.cfg.memory, auto_learn=False))
    assert rt.learn("Ich heiße Fabian") == []
    assert CFG.memory.auto_learn is True


def test_autolearn_only_in_user_path():
    """Werkzeugausgaben laufen nie durch learn(): nur die Bridge ruft es mit dem Nutzertext auf."""
    import inspect
    from jarvis_app import bridge
    src = inspect.getsource(bridge.Bridge._run_stream)
    assert src.count("_auto_learn(text)") == 1
    assert "_auto_learn" not in inspect.getsource(bridge.Bridge._run_tool_stream)


# ------------------------------------------------ Review Runde 1: Autolearn

@pytest.mark.parametrize("text", [
    "Ich wohne in der Nähe vom Bahnhof", "ich komme aus dem Staunen nicht raus", "Ich wohne in einer kleinen Stadt",
    "Ich komme aus meiner Wohnung nicht raus", "Ich heiße nicht Max", "ich wohne in hamburg"])
def test_autolearn_requires_real_capitalised_names(text):
    assert autolearn.extract(text) == []


def test_autolearn_positive_places_and_no_overwrite(tmp_path):
    assert [f.text for f in autolearn.extract("Ich wohne in Hamburg")] == ["Sir wohnt in Hamburg."]
    assert [f.text for f in autolearn.extract("Ich komme aus Bremen")] == ["Sir kommt aus Bremen."]
    rt = runtime(tmp_path)
    rt.learn("Ich wohne in Hamburg")
    assert rt.learn("Ich wohne in der Nähe vom Bahnhof") == []
    from jarvis_core.tools.memory_tools import get_memory
    assert [r["text"] for r in get_memory(rt.ctx).recent(5)] == ["Sir wohnt in Hamburg."]


def test_autolearn_skips_pasted_foreign_text():
    mail = "Hallo zusammen, im Anhang die Unterlagen. " * 8 + "Ich heiße Max Mustermann."
    assert len(mail) > 300 and autolearn.extract(mail) == []
    many = "Guten Tag. Danke für die Nachricht. Wir melden uns. Ich heiße Max Mustermann."
    assert autolearn.extract(many) == []
