"""Recherche: Quellenbewertung, passende Textstellen, Kennzeichnung."""
import pytest

from jarvis_core.tools import research_tools as rs
from tests.tools_helpers import CFG, runtime


@pytest.mark.parametrize("url,tier", [
    ("https://de.wikipedia.org/wiki/Hamburg", "trusted"), ("https://www.tagesschau.de/inland/x.html", "trusted"),
    ("https://www.bsi.bund.de/DE/x", "trusted"), ("https://web.mit.edu/x", "trusted"),
    ("https://www.gutefrage.net/frage/x", "low"), ("https://m.facebook.com/x", "low"),
    ("https://irgendein-blog.example/x", "unknown"), ("https://notwikipedia.org/x", "unknown"),
    ("https://evilwikipedia.org.attacker.example/x", "unknown"), ("kein-link", "blocked"),
])
def test_domain_tier(url, tier):
    assert rs.domain_tier(url, CFG.research) == tier


def test_own_lists_override_builtin():
    import dataclasses
    cfg = dataclasses.replace(CFG.research, trusted_domains=("mein-blog.example",), blocked_domains=("wikipedia.org",))
    assert rs.domain_tier("https://www.mein-blog.example/a", cfg) == "trusted"
    assert rs.domain_tier("https://de.wikipedia.org/wiki/X", cfg) == "blocked"


def test_rank_puts_established_first_and_drops_low_and_duplicates():
    hits = [{"url": "https://www.gutefrage.net/a", "title": "g"}, {"url": "https://blog.example/a", "title": "b"},
            {"url": "https://www.zeit.de/a", "title": "z"}, {"url": "https://www.zeit.de/a/", "title": "z2"},
            {"url": "https://de.wikipedia.org/wiki/A", "title": "w"}]
    assert [h["title"] for h in rs.rank_hits(hits, CFG.research)] == ["z", "w", "b"]


def test_relevant_passages_prefers_matching_paragraphs():
    paras = ["Einleitung zum Verein."] + [f"Absatz {i} über Wetter und Reisen. " * 6 for i in range(30)]
    paras.insert(20, "Der Mindestlohn beträgt seit Januar 13,90 Euro pro Stunde.")
    out = rs.relevant_passages("\n".join(paras), "Wie hoch ist der Mindestlohn?", 600)
    assert "13,90 Euro" in out and out.startswith("Einleitung") and "[…]" in out and len(out) <= 600
    assert rs.relevant_passages("kurz", "egal", 600) == "kurz"


def test_research_labels_sources_and_prefers_established(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "search_web", lambda ctx, q, n: [
        {"title": "Forum", "url": "https://www.gutefrage.net/f", "snippet": ""},
        {"title": "Blog", "url": "https://blog.example/a", "snippet": ""},
        {"title": "Zeit", "url": "https://www.zeit.de/a", "snippet": ""},
        {"title": "Wiki", "url": "https://de.wikipedia.org/wiki/A", "snippet": ""},
        {"title": "Blog2", "url": "https://anderer-blog.example/a", "snippet": ""}])
    read = []
    monkeypatch.setattr(rs, "page_content", lambda url: read.append(url) or {
        "title": url.split("/")[2], "date": "2026-11-01", "text": "Inhalt zur Frage. " * 40})
    rt = runtime(tmp_path)
    r = rt.registry.execute(rt.ctx, "research", {"question": "Was ist A?"}, 6000)
    assert r.ok and [s["tier"] for s in r.data["sources"]] == ["trusted", "trusted"]
    assert read == ["https://www.zeit.de/a", "https://de.wikipedia.org/wiki/A"]     # Forum nie, Blogs nicht nötig
    assert "etablierte Quelle, Stand 2026-11-01" in r.text and rt.ctx.tainted


def test_research_marks_unconfirmed_when_only_unknown_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "search_web", lambda ctx, q, n: [{"title": "Blog", "url": "https://blog.example/a", "snippet": ""}])
    monkeypatch.setattr(rs, "page_content", lambda url: {"title": "Blog", "date": "", "text": "Behauptung. " * 40})
    rt = runtime(tmp_path)
    r = rt.registry.execute(rt.ctx, "research", {"question": "Stimmt das?"}, 6000)
    assert r.ok and "unbekannte Quelle" in r.text and "unbestätigt" in r.text


def test_only_trusted_mode_refuses_unknown_sources(tmp_path, monkeypatch):
    import dataclasses
    from jarvis_core.runtime import build_runtime
    from tests.tools_helpers import FakeClient
    cfg = dataclasses.replace(CFG, research=dataclasses.replace(CFG.research, only_trusted=True))
    monkeypatch.setattr(rs, "search_web", lambda ctx, q, n: [{"title": "Blog", "url": "https://blog.example/a", "snippet": ""}])
    monkeypatch.setattr(rs, "page_content", lambda url: pytest.fail("unbekannte Quelle darf nicht gelesen werden"))
    rt = build_runtime(cfg, FakeClient(), workspace=tmp_path / "ws", data_dir=tmp_path / "data")
    r = rt.registry.execute(rt.ctx, "research", {"question": "Stimmt das?"}, 6000)
    assert not r.ok and "belastbare Quelle" in r.text


def test_research_output_gets_its_own_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "search_web", lambda ctx, q, n: [
        {"title": f"W{i}", "url": f"https://de.wikipedia.org/wiki/{i}", "snippet": ""} for i in range(4)])
    monkeypatch.setattr(rs, "page_content", lambda url: {"title": "W", "date": "", "text": "\n".join(
        f"Satz {i} über das Thema mit Inhalt." for i in range(400))})
    rt = runtime(tmp_path)
    r = rt.registry.execute(rt.ctx, "research", {"question": "Thema"}, 6000)
    assert 12_000 < len(r.text) <= rs.OUTPUT_CHARS + 800 and "Zeichen ausgelassen" not in r.text
    assert len(r.data["sources"]) == 4


def test_duckduckgo_parsing_skips_ads_and_decodes_redirects():
    html = ('<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.zeit.de%2Fa&rut=x">Die <b>Zeit</b></a>'
            '<a class="result__snippet" href="x">Kurz &amp; gut</a>'
            '<a class="result__a" href="https://duckduckgo.com/y.js?ad=1">Anzeige</a>'
            '<a class="result__a" href="https://example.org/b">B</a>')
    assert rs._parse_ddg(html, 5) == [{"title": "Die Zeit", "url": "https://www.zeit.de/a", "snippet": "Kurz & gut"},
                                     {"title": "B", "url": "https://example.org/b", "snippet": ""}]


@pytest.mark.parametrize("url", ["ftp://example.org/x", "file:///etc/passwd", "http://user:pw@example.org/",
                                 "http://127.0.0.1:11434/", "http://[::1]/", "javascript:alert(1)"])
def test_non_public_or_odd_urls_are_refused(tmp_path, url):
    rt = runtime(tmp_path)
    assert not rt.registry.execute(rt.ctx, "fetch_url", {"url": url}, 6000).ok
