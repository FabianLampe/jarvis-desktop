"""Recherche mit Quellenangaben: Suche (SearXNG oder DuckDuckGo) → Seitentext (Trafilatura).

Seiteninhalt ist fremder Inhalt. Abrufe gehen nur an öffentliche http(s)-Adressen
(kein localhost/LAN), damit eine Webseite Jarvis nicht auf lokale Dienste lenken kann."""
from __future__ import annotations

import ipaddress
import logging
import re
import socket
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests

from .registry import Tool, ToolContext, ToolError, ToolResult, wrap_untrusted

log = logging.getLogger("jarvis.research")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Jarvis/1.0"
MAX_BYTES = 2_000_000


def check_public_url(url: str) -> str:
    u = urlparse(url.strip())
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ToolError("Nur öffentliche http(s)-Adressen werden abgerufen")
    try:
        infos = socket.getaddrinfo(u.hostname, None)
    except socket.gaierror as exc:
        raise ToolError(f"Adresse nicht auflösbar: {u.hostname}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ToolError(f"Abruf blockiert: {u.hostname} ist keine öffentliche Adresse")
    return url.strip()


def _get(url: str, timeout: float = 15.0) -> requests.Response:
    """GET mit manueller Weiterleitungsprüfung (jede Station muss öffentlich sein)."""
    for _ in range(5):
        check_public_url(url)
        r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "de,en;q=0.7"},
                         timeout=timeout, stream=True, allow_redirects=False)
        if r.is_redirect and r.headers.get("location"):
            url = urljoin(url, r.headers["location"])
            continue
        r.url = url
        return r
    raise ToolError("Zu viele Weiterleitungen")


def search_web(ctx: ToolContext, query: str, n: int) -> list[dict]:
    cfg = ctx.cfg.research
    if cfg.searxng_url:
        r = requests.get(cfg.searxng_url.rstrip("/") + "/search",
                         params={"q": query, "format": "json", "language": "de"}, timeout=15)
        r.raise_for_status()
        return [{"title": x.get("title", ""), "url": x["url"], "snippet": x.get("content", "")}
                for x in r.json().get("results", [])[:n]]
    r = requests.post("https://html.duckduckgo.com/html/", data={"q": query, "kl": "de-de"},
                      headers={"User-Agent": UA}, timeout=15)
    r.raise_for_status()
    out = []
    for m in re.finditer(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=<a[^>]+class="result__a"|$)',
                         r.text, re.S):
        href, title, rest = m.group(1), re.sub(r"<[^>]+>", "", m.group(2)), m.group(3)
        if "uddg=" in href:
            href = unquote(parse_qs(urlparse(href if href.startswith("http") else "https:" + href).query)["uddg"][0])
        sn = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', rest, re.S)
        out.append({"title": _unhtml(title), "url": href,
                    "snippet": _unhtml(re.sub(r"<[^>]+>", "", sn.group(1))) if sn else ""})
        if len(out) >= n:
            break
    if not out:
        raise ToolError("Die Websuche lieferte keine Treffer (Suchdienst nicht erreichbar oder blockiert)")
    return out


def _unhtml(s: str) -> str:
    import html
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def page_text(url: str, limit: int) -> tuple[str, str]:
    """(Titel, Text) einer Seite über Trafilatura."""
    r = _get(url)
    ctype = r.headers.get("content-type", "")
    if "html" not in ctype and "text" not in ctype and "xml" not in ctype:
        raise ToolError(f"Kein Text/HTML ({ctype or 'unbekannter Typ'})")
    body = r.raw.read(MAX_BYTES, decode_content=True)
    import trafilatura
    html = body.decode(r.encoding or "utf-8", errors="replace")
    text = trafilatura.extract(html, include_comments=False, include_tables=True, url=url) or ""
    meta = trafilatura.extract_metadata(html)
    return (meta.title if meta and meta.title else url), text[:limit]


def web_search(ctx: ToolContext, a: dict) -> ToolResult:
    hits = search_web(ctx, str(a.get("query", "")), int(a.get("limit") or 5))
    lines = [f"[{i}] {h['title']} — {h['url']}\n    {h['snippet'][:200]}" for i, h in enumerate(hits, 1)]
    return ToolResult(True, wrap_untrusted("\n".join(lines)),
                      {"sources": [{"n": i, "title": h["title"], "url": h["url"]} for i, h in enumerate(hits, 1)]})


def research(ctx: ToolContext, a: dict) -> ToolResult:
    """Suchen, die besten Seiten lesen, nummerierte Auszüge mit URL liefern."""
    cfg = ctx.cfg.research
    question = str(a.get("question", ""))
    hits = search_web(ctx, question, cfg.max_sources + 3)
    blocks, sources, failed = [], [], 0
    for h in hits:
        if len(sources) >= cfg.max_sources:
            break
        if ctx.cancel.is_set():
            break
        try:
            title, text = page_text(h["url"], cfg.max_chars_per_source)
        except Exception as exc:  # noqa: BLE001
            log.info("Quelle übersprungen %s: %s", h["url"], exc)
            failed += 1
            continue
        if len(text) < 200:
            failed += 1
            continue
        n = len(sources) + 1
        sources.append({"n": n, "title": title, "url": h["url"]})
        blocks.append(f"[{n}] {title} ({h['url']})\n{text}")
    if not sources:
        raise ToolError("Keine lesbare Quelle gefunden — ich kann dazu nichts belegen.")
    note = f"\n({failed} Treffer waren nicht lesbar.)" if failed else ""
    return ToolResult(True, wrap_untrusted("\n\n".join(blocks)) + note, {"sources": sources})


def fetch_url(ctx: ToolContext, a: dict) -> ToolResult:
    title, text = page_text(str(a.get("url", "")), int(a.get("max_chars") or ctx.cfg.research.max_chars_per_source))
    if not text:
        raise ToolError("Auf der Seite wurde kein lesbarer Text gefunden")
    return ToolResult(True, f"{title} ({a['url']})\n" + wrap_untrusted(text),
                      {"sources": [{"n": 1, "title": title, "url": a["url"]}]})


def tools() -> list[Tool]:
    S = {"type": "string"}
    return [
        Tool("research", "research",
             "Recherchiert im Web: sucht, liest die besten Seiten, liefert nummerierte Auszüge [1], [2] mit URL. Antwort MUSS die Quellen nennen.",
             {"question": S}, ("question",), research, untrusted=True,
             announce=lambda a: "Ich recherchiere im Netz, Sir."),
        Tool("web_search", "research", "Websuche: Titel, URL, Kurztext.",
             {"query": S, "limit": {"type": "integer"}}, ("query",), web_search, untrusted=True),
        Tool("fetch_url", "research", "Liest den Haupttext einer Webseite.",
             {"url": S, "max_chars": {"type": "integer"}}, ("url",), fetch_url, untrusted=True),
    ]
