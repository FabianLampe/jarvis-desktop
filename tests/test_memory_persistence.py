"""Gedächtnis, Dokumente, Erinnerungen: Persistenz über Neustarts."""
import hashlib
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from jarvis_core.memory.knowledge import Knowledge, chunk_markdown
from jarvis_core.memory.reminders import ReminderService
from jarvis_core.memory.store import MemoryStore
from jarvis_core.memory.when import detect_repeat, parse_when

BERLIN = ZoneInfo("Europe/Berlin")


def test_memory_survives_restart_and_crud(tmp_path):
    db = tmp_path / "m.db"
    m = MemoryStore(db)
    mid, new = m.add("Mein Geburtstag ist am 12. März")
    assert new and m.add("mein geburtstag ist am 12. märz") == (mid, False)
    del m
    m2 = MemoryStore(db)                                   # „Neustart“
    assert m2.search("Wann habe ich Geburtstag?")[0]["id"] == mid
    assert m2.update(mid, "Mein Geburtstag ist am 13. März")
    assert "13." in MemoryStore(db).get(mid)["text"] and not MemoryStore(db).search("12. März")[:0]
    assert m2.delete(mid) and m2.search("Geburtstag") == []


def test_memory_tools_roundtrip(tmp_path):
    from tests.tools_helpers import runtime
    rt = runtime(tmp_path)
    ex = lambda n, **a: rt.registry.execute(rt.ctx, n, a, 2000)
    assert ex("memory_save", text="Lieblingsfarbe ist Blau").ok
    assert "Blau" in ex("memory_search", query="Farbe").text
    assert rt.registry.assess(rt.ctx, "memory_forget", {"id": 1}).level == "confirm"
    assert "Blau" in rt.registry.assess(rt.ctx, "memory_forget", {"id": 1}).summary
    assert ex("memory_update", id=1, text="Lieblingsfarbe ist Grün").ok
    assert ex("memory_forget", id=1).ok and "nichts" in ex("memory_search", query="Farbe").text


class FakeEmbedder:
    """Deterministische Bag-of-words-Vektoren (schnell, kein Modelldownload)."""
    VOC = ["kündigungsfrist", "kaution", "mietvertrag", "monate", "euro", "dezember", "umsatz", "folie"]

    def _v(self, t):
        t = t.lower()
        v = [float(t.count(w)) for w in self.VOC]
        return v if any(v) else [0.01] * len(self.VOC)

    def docs(self, texts): return [self._v(t) for t in texts]
    def query(self, t): return self._v(t)


def test_chunking_keeps_page_heading_and_lines():
    md = "# Vertrag\nEins\n\x0c## Kündigung\nDie Kündigungsfrist beträgt drei Monate.\n\x0cKaution 1500 Euro"
    chunks = chunk_markdown(md, size=60, overlap=0, pdf=True)
    by_text = {c.text: c for c in chunks}
    k = next(c for c in chunks if "Kündigungsfrist" in c.text)
    assert k.page == 2 and k.section == "Kündigung" and k.line_start >= 3
    assert next(c for c in chunks if "Kaution" in c.text).page == 3
    assert "Seite/Folie 2" in k.location and "Zeilen" in k.location


def test_document_search_cites_source(tmp_path):
    f = tmp_path / "Mietvertrag.md"
    f.write_text("# Laufzeit\nDer Mietvertrag endet im Dezember.\n\n# Kündigung\nDie Kündigungsfrist beträgt drei Monate.\n\n# Kaution\nDie Kaution beträgt 1500 Euro.\n")
    kn = Knowledge(tmp_path / "data", "x", chunk_chars=60, overlap=0, embedder=FakeEmbedder())
    r = kn.index_path(f)
    assert r["files"] == 1 and r["chunks"] >= 3
    hit = Knowledge(tmp_path / "data", "x", embedder=FakeEmbedder()).search("Wie lang ist die Kündigungsfrist?", 1)[0]  # neue Instanz = Neustart
    assert hit["file"] == "Mietvertrag.md" and hit["path"] == str(f.resolve())
    assert "Kündigungsfrist" in hit["text"] and "Kündigung" in hit["location"] and "Zeilen 4" in hit["location"]
    kn.index_path(f)                                           # erneut indexieren dupliziert nicht
    assert kn.stats()["chunks"] == r["chunks"]


def test_docs_tool_returns_citations(tmp_path):
    from tests.tools_helpers import runtime
    rt = runtime(tmp_path)
    rt.ctx.services["knowledge"] = Knowledge(rt.ctx.data_dir, "x", chunk_chars=60, overlap=0, embedder=FakeEmbedder())
    (rt.ctx.workspace / "a.md").write_text("# Kaution\nDie Kaution beträgt 1500 Euro.\n")
    assert rt.registry.execute(rt.ctx, "docs_index", {"path": "a.md"}, 1000).ok
    res = rt.registry.execute(rt.ctx, "docs_search", {"query": "Kaution"}, 2000)
    assert "a.md" in res.text and res.data["sources"][0]["path"].endswith("a.md") and rt.ctx.tainted


# ---------------------------------------------------------- Erinnerungen

def svc(tmp_path, delivered=None):
    return ReminderService(tmp_path, on_due=(lambda d: delivered.append(d)) if delivered is not None else None)


def test_reminder_persists_and_fires(tmp_path):
    got = []
    s = svc(tmp_path, got)
    s.start()
    r = s.create("Wäsche", datetime.now(BERLIN) + timedelta(seconds=2))
    s.shutdown()
    s2 = svc(tmp_path, got); s2.start()                    # Neustart: Job kommt aus dem JobStore
    assert [x["id"] for x in s2.list()] == [r["id"]]
    for _ in range(60):
        if got: break
        time.sleep(0.1)
    s2.shutdown()
    assert got and got[0]["text"] == "Wäsche" and not got[0]["missed"]


def test_missed_reminder_is_reported_not_silently_lost(tmp_path):
    s = svc(tmp_path); s.start()
    r = s.create("Zahnarzt", datetime.now(BERLIN) + timedelta(seconds=1))
    s.shutdown()                                           # Jarvis „aus“
    time.sleep(1.6)
    got = []
    s2 = svc(tmp_path, got)
    missed = s2.start()
    assert [m["id"] for m in missed] == [r["id"]]
    assert s2.deliver_pending() == 1 and got[0]["missed"] and "verpasst" in got[0]["text"]
    assert s2.list() == [] and s2.undelivered() == []      # erledigt, nicht doppelt
    s2.shutdown()


def test_recurring_reminder_advances_and_coalesces(tmp_path):
    s = svc(tmp_path); s.start()
    past = datetime.now(BERLIN) - timedelta(days=3, minutes=1)
    r = s.create("Briefing", past, repeat="daily")
    s.shutdown()
    s2 = svc(tmp_path); missed = s2.start()
    row = s2.get(r["id"])
    assert row["status"] == "active" and datetime.fromisoformat(row["due_at"]) > datetime.now(BERLIN)
    assert len(s2.undelivered()) == 1                      # drei verpasste Tage ⇒ eine Meldung
    s2.shutdown()


def test_cancel_and_past_rejected(tmp_path):
    s = svc(tmp_path); s.start()
    with pytest.raises(ValueError):
        s.create("x", datetime.now(BERLIN) - timedelta(hours=1))
    r = s.create("y", datetime.now(BERLIN) + timedelta(hours=1))
    assert s.cancel(r["id"]) and s.list() == []
    s.shutdown()


def test_timezone_europe_berlin_across_dst():
    now = datetime(2026, 10, 23, 12, 0, tzinfo=BERLIN)     # Sommerzeit endet 25.10.2026
    d = parse_when("morgen um 9 Uhr", now); d2 = parse_when("übermorgen um 9 Uhr", now)
    assert d.utcoffset() == timedelta(hours=2) and d2.utcoffset() == timedelta(hours=1)
    assert d2.hour == 9


def test_when_parser():
    now = datetime(2026, 10, 28, 15, 30, tzinfo=BERLIN)
    assert parse_when("in 10 Minuten", now) == now + timedelta(minutes=10)
    assert parse_when("in einer halben Stunde", now) == now + timedelta(minutes=30)
    assert parse_when("Freitag um 14 Uhr", now).strftime("%a %H:%M") == "Fri 14:00"
    assert parse_when("am 05.11. um 8", now).strftime("%d.%m %H:%M") == "05.11 08:00"
    assert parse_when("2026-11-02T08:15", now).minute == 15
    with pytest.raises(ValueError):
        parse_when("irgendwann mal", now)
    assert detect_repeat("jeden Montag") == "weekly" and detect_repeat("täglich um 8") == "daily"


def test_reminder_tool_states_limits(tmp_path):
    from tests.tools_helpers import runtime
    rt = runtime(tmp_path)
    res = rt.registry.execute(rt.ctx, "reminder_create", {"text": "Test", "when": "in 2 Stunden"}, 2000)
    assert res.ok and "Europe/Berlin" in res.text and "läuft" in res.text and "verpasst" in res.text
    rt.ctx.services["reminders"].shutdown()
