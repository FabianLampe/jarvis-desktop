"""Recherche mit Quellenangaben: Suche (SearXNG oder DuckDuckGo) → Seitentext (Trafilatura).

Seiteninhalt ist fremder Inhalt. Abrufe gehen nur an öffentliche http(s)-Adressen
(kein localhost/LAN), damit eine Webseite Jarvis nicht auf lokale Dienste lenken kann.
Die Prüfung sitzt am Verbindungsaufbau selbst: geprüft wird genau die Adresse, zu der
verbunden wird — ein Name, der erst „öffentlich“ und beim Abruf „intern“ auflöst
(DNS-Rebinding), kommt nicht durch.

Quellen werden bewertet: etablierte Seiten zuerst, soziale Netzwerke und Frageportale
gar nicht, Unbekanntes nur als Ergänzung und entsprechend gekennzeichnet.
"""
from __future__ import annotations

import ipaddress
import logging
import re
import socket
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool

from .registry import Tool, ToolContext, ToolError, ToolResult, sanitize_untrusted, wrap_untrusted

log = logging.getLogger("jarvis.research")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Jarvis/1.0"
MAX_BYTES = 2_000_000
OUTPUT_CHARS = 18_000            # Gesamtbudget einer Recherche an das Modell

# Etablierte Quellen (Redaktionen mit Impressum, Behörden, Forschung, Herstellerdoku, Nachschlagewerke).
TRUSTED = (
    "wikipedia.org", "wikimedia.org", "britannica.com", "tagesschau.de", "zdf.de", "zdfheute.de", "ard.de",
    "deutschlandfunk.de", "dw.com", "spiegel.de", "zeit.de", "faz.net", "sueddeutsche.de", "handelsblatt.com",
    "tagesspiegel.de", "nzz.ch", "derstandard.at", "orf.at", "srf.ch", "reuters.com", "apnews.com", "bbc.com",
    "bbc.co.uk", "theguardian.com", "nytimes.com", "economist.com", "ft.com", "bloomberg.com", "npr.org",
    "heise.de", "golem.de", "arstechnica.com", "theverge.com", "kicker.de", "sportschau.de",
    "nature.com", "science.org", "arxiv.org", "nih.gov", "who.int", "nasa.gov", "esa.int", "mpg.de",
    "fraunhofer.de", "helmholtz.de", "dwd.de", "rki.de", "destatis.de", "bund.de", "bundesregierung.de",
    "bundestag.de", "europa.eu", "gesetze-im-internet.de", "verbraucherzentrale.de", "test.de", "adac.de",
    "bmas.de", "bmwk.de", "bmbf.de", "bundesfinanzministerium.de", "auswaertiges-amt.de", "bundesbank.de",
    "bundesnetzagentur.de", "bafin.de", "zoll.de", "arbeitsagentur.de", "deutsche-rentenversicherung.de",
    "umweltbundesamt.de", "bpb.de", "hamburg.de", "berlin.de", "ndr.de", "wdr.de", "br.de", "swr.de", "mdr.de",
    "hr.de", "rbb24.de", "n-tv.de", "stern.de", "welt.de", "taz.de", "computerbase.de", "ct.de", "netzpolitik.org",
    "python.org", "mozilla.org", "apple.com", "microsoft.com", "github.com",
    "stackoverflow.com", "ietf.org", "w3.org", "duden.de", "dwds.de",
)
TRUSTED_SUFFIX = (".gov", ".edu", ".ac.uk", ".gv.at", ".admin.ch")
# Nicht als Beleg geeignet: Nutzerbeiträge ohne Redaktion, Linksammlungen.
LOW = (
    "pinterest.com", "pinterest.de", "facebook.com", "instagram.com", "tiktok.com", "x.com", "twitter.com",
    "gutefrage.net", "quora.com", "answers.com", "reddit.com", "telegram.org", "t.me", "vk.com",
    "9gag.com", "knowyourmeme.com", "medium.com", "blogspot.com", "tumblr.com", "wattpad.com",
)
TIER_LABEL = {"trusted": "etablierte Quelle", "unknown": "unbekannte Quelle, mit Vorsicht", "low": "nicht belastbar"}


def _matches(host: str, domains) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def domain_tier(url: str, cfg=None) -> str:
    """trusted | unknown | low | blocked — eigene Listen aus config.yaml gehen vor."""
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    if not host:
        return "blocked"
    if cfg is not None:
        if _matches(host, cfg.blocked_domains):
            return "blocked"
        if _matches(host, cfg.trusted_domains):
            return "trusted"
    if _matches(host, LOW):
        return "low"
    if _matches(host, TRUSTED) or host.endswith(TRUSTED_SUFFIX):
        return "trusted"
    return "unknown"


def rank_hits(hits: list[dict], cfg=None) -> list[dict]:
    """Etablierte Quellen zuerst (Suchreihenfolge bleibt innerhalb der Stufe), Unbelastbares entfällt."""
    seen, keep = set(), []
    for h in hits:
        tier = domain_tier(h.get("url", ""), cfg)
        key = (urlparse(h.get("url", "")).hostname or "", urlparse(h.get("url", "")).path.rstrip("/"))
        if tier in ("blocked", "low") or key in seen:
            continue
        seen.add(key)
        keep.append({**h, "tier": tier})
    return sorted(keep, key=lambda h: 0 if h["tier"] == "trusted" else 1)


# ------------------------------------------------------- geschützter Abruf

def _is_public(ip: str) -> bool:
    """Nur weltweit geroutete Adressen: kein LAN, Loopback, Link-Local, CGNAT/VPN (100.64/10), Multicast."""
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    mapped = getattr(a, "ipv4_mapped", None)
    if mapped is not None:
        a = mapped
    return bool(a.is_global and not a.is_multicast)


def _public_addresses(host: str, port: int) -> list[tuple]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:
        raise ToolError(f"Adresse nicht auflösbar: {host}") from exc
    if not infos or any(not _is_public(i[4][0]) for i in infos):
        raise ToolError(f"Abruf blockiert: {host} ist keine öffentliche Adresse")
    return infos


def check_public_url(url: str) -> str:
    u = urlparse(url.strip())
    if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password:
        raise ToolError("Nur öffentliche http(s)-Adressen werden abgerufen")
    _public_addresses(u.hostname, u.port or (443 if u.scheme == "https" else 80))
    return url.strip()


class _GuardedMixin:
    """Löst den Namen selbst auf, prüft JEDE Adresse und verbindet zu genau einer geprüften."""

    def _new_conn(self):  # type: ignore[override]
        host = getattr(self, "_dns_host", None) or self.host
        last: Exception | None = None
        timeout = self.timeout if isinstance(self.timeout, (int, float)) else 15.0
        for family, kind, proto, _, addr in _public_addresses(host, self.port):
            try:
                sock = socket.socket(family, kind, proto)
                sock.settimeout(timeout)
                sock.connect(addr)
                return sock
            except OSError as exc:
                last = exc
        raise requests.ConnectionError(f"Verbindung zu {host} fehlgeschlagen: {last}")


class _GuardedHTTP(_GuardedMixin, HTTPConnection):
    pass


class _GuardedHTTPS(_GuardedMixin, HTTPSConnection):
    pass


class _HTTPPool(HTTPConnectionPool):
    ConnectionCls = _GuardedHTTP


class _HTTPSPool(HTTPSConnectionPool):
    ConnectionCls = _GuardedHTTPS


class PublicOnlyAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = {"http": _HTTPPool, "https": _HTTPSPool}


def public_session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False                      # kein Proxy aus der Umgebung — der würde die Prüfung umgehen
    s.mount("http://", PublicOnlyAdapter())
    s.mount("https://", PublicOnlyAdapter())
    return s


def _get(url: str, timeout: float = 15.0) -> requests.Response:
    """GET mit manueller Weiterleitungsprüfung (jede Station muss öffentlich sein)."""
    session = public_session()
    for _ in range(5):
        check_public_url(url)
        try:
            r = session.get(url, headers={"User-Agent": UA, "Accept-Language": "de,en;q=0.7"},
                            timeout=timeout, stream=True, allow_redirects=False)
        except requests.RequestException as exc:
            cause = exc.__context__ or exc.__cause__
            if isinstance(cause, ToolError):
                raise cause from exc
            raise ToolError(f"Seite nicht erreichbar: {type(exc).__name__}") from exc
        if r.is_redirect and r.headers.get("location"):
            url = urljoin(url, r.headers["location"])
            r.close()
            continue
        r.url = url
        return r
    raise ToolError("Zu viele Weiterleitungen")


# ------------------------------------------------------------------ Suche

_DDG_HIT = re.compile(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=<a[^>]+class="result__a"|$)', re.S)
_DDG_LITE = re.compile(r'<a[^>]+href="([^"]+)"[^>]+class=.result-link.[^>]*>(.*?)</a>(.*?)(?=<a[^>]+class=.result-link.|$)', re.S)


def _ddg_url(href: str) -> str:
    if "uddg=" in href:
        q = parse_qs(urlparse(href if href.startswith("http") else "https:" + href).query)
        href = unquote(q["uddg"][0]) if q.get("uddg") else href
    return href


def _parse_ddg(html_text: str, n: int) -> list[dict]:
    out = []
    for rx, snip in ((_DDG_HIT, r'class="result__snippet"[^>]*>(.*?)</a>'),
                     (_DDG_LITE, r"class=.result-snippet.[^>]*>(.*?)</td>")):
        for m in rx.finditer(html_text):
            href = _ddg_url(m.group(1))
            if not href.startswith(("http://", "https://")) or "duckduckgo.com/y.js" in href:
                continue                                   # Anzeigen und interne Links
            sn = re.search(snip, m.group(3), re.S)
            out.append({"title": _unhtml(re.sub(r"<[^>]+>", "", m.group(2))), "url": href,
                        "snippet": _unhtml(re.sub(r"<[^>]+>", "", sn.group(1))) if sn else ""})
            if len(out) >= n:
                return out
        if out:
            break
    return out


def search_web(ctx: ToolContext, query: str, n: int) -> list[dict]:
    cfg = ctx.cfg.research
    query = query.strip()
    if not query:
        raise ToolError("Suchbegriff fehlt")
    if cfg.searxng_url:
        r = requests.get(cfg.searxng_url.rstrip("/") + "/search",
                         params={"q": query, "format": "json", "language": "de"}, timeout=15)
        r.raise_for_status()
        return [{"title": x.get("title", ""), "url": x["url"], "snippet": x.get("content", "")}
                for x in r.json().get("results", [])[:n]]
    last = ""
    for endpoint in ("https://html.duckduckgo.com/html/", "https://lite.duckduckgo.com/lite/"):
        try:
            r = requests.post(endpoint, data={"q": query, "kl": "de-de"},
                              headers={"User-Agent": UA}, timeout=15)
            r.raise_for_status()
        except requests.RequestException as exc:
            last = type(exc).__name__
            continue
        if (out := _parse_ddg(r.text, n)):
            return out
    raise ToolError("Die Websuche lieferte keine Treffer (Suchdienst nicht erreichbar oder blockiert"
                    + (f": {last}" if last else "") + ")")


def _unhtml(s: str) -> str:
    import html
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


# ----------------------------------------------------------- Seiteninhalt

def page_content(url: str) -> dict:
    """{"title","text","date"} einer Seite über Trafilatura (voller Haupttext, ungekürzt)."""
    r = _get(url)
    try:
        ctype = r.headers.get("content-type", "")
        if "html" not in ctype and "text" not in ctype and "xml" not in ctype:
            raise ToolError(f"Kein Text/HTML ({ctype or 'unbekannter Typ'})")
        if r.status_code >= 400:
            raise ToolError(f"Seite antwortet mit Fehler {r.status_code}")
        body = r.raw.read(MAX_BYTES, decode_content=True)
        encoding = r.encoding or "utf-8"
    finally:
        r.close()
    import trafilatura
    try:
        html_text = body.decode(encoding, errors="replace")
    except LookupError:
        html_text = body.decode("utf-8", errors="replace")
    text = trafilatura.extract(html_text, include_comments=False, include_tables=True, url=url) or ""
    meta = trafilatura.extract_metadata(html_text)
    return {"title": sanitize_untrusted(meta.title if meta and meta.title else url)[:160],
            "text": text,
            # Wikis melden das Anlagedatum des Artikels, nicht den Stand — das würde in die Irre führen.
            "date": str(meta.date)[:10] if meta and meta.date and "wiki" not in (urlparse(url).hostname or "") else ""}


def page_text(url: str, limit: int) -> tuple[str, str]:
    """(Titel, Text) — Kompatibilität für bestehende Aufrufer."""
    c = page_content(url)
    return c["title"], c["text"][:limit]


_STOP = set("der die das den dem des ein eine einen einem einer eines und oder aber auch nicht ist sind war waren "
            "wird werden wurde hat haben hatte mit von für auf aus bei nach über unter zum zur im in an am als wie "
            "was wer wo wann warum welche welcher welches wieviel viel viele es sie er ich du wir ihr man sich "
            "dass ob noch nur schon mehr sehr kann können gibt the a an of and or to in on for is are was were "
            "what who when where why how which with from by at as it this that".split())


def keywords(question: str) -> list[str]:
    return [w for w in dict.fromkeys(re.findall(r"[\wäöüß]{3,}", question.lower())) if w not in _STOP]


def relevant_passages(text: str, question: str, limit: int) -> str:
    """Die zur Frage passendsten Absätze in Originalreihenfolge statt bloß des Seitenanfangs."""
    if len(text) <= limit:
        return text
    paras = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
    words = keywords(question)
    if not words or len(paras) < 3:
        return text[:limit]
    scored = []
    for i, p in enumerate(paras):
        low = p.lower()
        hits = sum(1 for w in words if w in low)
        density = sum(low.count(w) for w in words) / max(40, len(p)) * 100
        scored.append((hits * 2 + min(density, 3) + (1.5 if i == 0 else 0), i))
    chosen, used = set(), 0
    for _, i in sorted(scored, key=lambda s: (-s[0], s[1])):
        size = len(paras[i]) + 1
        if used + size > limit:
            if used == 0:
                chosen.add(i)                       # ein einzelner Riesenabsatz: gekürzt nehmen
                break
            continue
        chosen.add(i)
        used += size
    out, prev = [], None
    for i in sorted(chosen):
        if prev is not None and i != prev + 1:
            out.append("[…]")
        out.append(paras[i])
        prev = i
    return "\n".join(out)[:limit]


# ---------------------------------------------------------------- Werkzeuge

def web_search(ctx: ToolContext, a: dict) -> ToolResult:
    limit = max(1, min(int(a.get("limit") or 6), 10))
    hits = rank_hits(search_web(ctx, str(a.get("query", "")), limit + 6), ctx.cfg.research)[:limit]
    if not hits:
        raise ToolError("Die Suche fand nur nicht belastbare Seiten (soziale Netzwerke, Frageportale).")
    lines = [f"[{i}] {h['title']} — {h['url']} ({TIER_LABEL[h['tier']]})\n    {h['snippet'][:240]}"
             for i, h in enumerate(hits, 1)]
    return ToolResult(True, wrap_untrusted("\n".join(lines)),
                      {"sources": [{"n": i, "title": h["title"], "url": h["url"], "tier": h["tier"]}
                                   for i, h in enumerate(hits, 1)]})


def research(ctx: ToolContext, a: dict) -> ToolResult:
    """Suchen, die besten Seiten lesen, nummerierte Auszüge mit URL und Einstufung liefern."""
    cfg = ctx.cfg.research
    question = str(a.get("question", "")).strip()
    if not question:
        raise ToolError("question fehlt")
    hits = rank_hits(search_web(ctx, question, cfg.max_sources + 8), cfg)
    if cfg.only_trusted:
        hits = [h for h in hits if h["tier"] == "trusted"]
    # Kopfzeilen, Markierung und Hinweise mitrechnen, damit das Ergebnis nie mittendrin gekappt wird.
    per_source = max(1200, min(cfg.max_chars_per_source, (OUTPUT_CHARS - 1200) // max(1, cfg.max_sources) - 300))
    blocks, sources, failed = [], [], 0
    for h in hits:
        if len(sources) >= cfg.max_sources or ctx.cancel.is_set():
            break
        # Unbekannte Seiten ergänzen nur, solange etablierte Belege fehlen.
        if h["tier"] != "trusted" and sum(1 for s in sources if s["tier"] == "trusted") >= 2:
            continue
        try:
            page = page_content(h["url"])
        except Exception as exc:  # noqa: BLE001
            log.info("Quelle übersprungen %s: %s", h["url"], exc)
            failed += 1
            continue
        if len(page["text"]) < 200:
            failed += 1
            continue
        n = len(sources) + 1
        sources.append({"n": n, "title": page["title"], "url": h["url"], "tier": h["tier"]})
        stand = f", Stand {page['date']}" if page["date"] else ""
        blocks.append(f"[{n}] {page['title']} ({h['url']}) — {TIER_LABEL[h['tier']]}{stand}\n"
                      + relevant_passages(page["text"], question, per_source))
    if not sources:
        raise ToolError("Keine lesbare, belastbare Quelle gefunden — ich kann dazu nichts belegen.")
    trusted = sum(1 for s in sources if s["tier"] == "trusted")
    note = f"\n({failed} Treffer waren nicht lesbar.)" if failed else ""
    note += ("\nHinweis: Keine etablierte Quelle dabei — Aussagen als unbestätigt kennzeichnen." if not trusted else
             "\nHinweis: Nur eine etablierte Quelle — nicht unabhängig bestätigt." if trusted == 1 and len(sources) == 1 else "")
    note += "\nWidersprechen sich die Quellen, nenne den Widerspruch, statt eine Seite zu wählen."
    return ToolResult(True, wrap_untrusted("\n\n".join(blocks)) + note,
                      {"sources": sources, "trusted": trusted})


def fetch_url(ctx: ToolContext, a: dict) -> ToolResult:
    url = check_public_url(str(a.get("url", "")))
    limit = max(500, min(int(a.get("max_chars") or 12_000), OUTPUT_CHARS - 500))
    tier = domain_tier(url, ctx.cfg.research)
    if tier == "blocked":
        raise ToolError("Diese Seite steht auf der Sperrliste (config.yaml: research.blocked_domains).")
    page = page_content(url)
    text, via = page["text"], ""
    if not text:
        # Kein Ausweichen auf den Browser: der löst Namen selbst auf und folgt Weiterleitungen,
        # die Prüfung auf öffentliche Adressen würde dort nicht greifen.
        raise ToolError("Auf der Seite wurde kein lesbarer Text gefunden (vermutlich lädt sie ihren Inhalt erst per "
                        "JavaScript). Sir kann sie ausdrücklich im Browser öffnen lassen.")
    focus = str(a.get("focus") or "")
    body = relevant_passages(text, focus, limit) if focus else text[:limit]
    more = f"\n(Die Seite ist länger: {len(text)} Zeichen. Mit focus gezielt nach einem Thema fragen.)" if len(text) > limit else ""
    stand = f", Stand {page['date']}" if page["date"] else ""
    return ToolResult(True, f"{page['title']} ({url}) — {TIER_LABEL[tier]}{stand}{via}\n" + wrap_untrusted(body) + more,
                      {"sources": [{"n": 1, "title": page["title"], "url": url, "tier": tier}]})


def tools() -> list[Tool]:
    S = {"type": "string"}
    return [
        Tool("research", "research",
             "Recherchiert im Web: sucht, liest die besten etablierten Seiten, liefert nummerierte Auszüge [1], [2] "
             "mit URL und Einstufung. question als vollständige Suchfrage formulieren. Antwort MUSS die Quellen nennen.",
             {"question": S}, ("question",), research, untrusted=True, output_chars=OUTPUT_CHARS,
             announce=lambda a: "Ich recherchiere im Netz, Sir."),
        Tool("web_search", "research", "Websuche: Titel, URL, Kurztext, Einstufung der Quelle. Danach fetch_url zum Lesen.",
             {"query": S, "limit": {"type": "integer"}}, ("query",), web_search, untrusted=True),
        Tool("fetch_url", "research",
             "Liest den Haupttext einer Webseite. focus = Thema/Frage, um bei langen Seiten die passenden Stellen zu bekommen.",
             {"url": S, "max_chars": {"type": "integer"}, "focus": S}, ("url",), fetch_url, untrusted=True,
             output_chars=OUTPUT_CHARS, announce=lambda a: "Ich lese die Seite, Sir."),
    ]
