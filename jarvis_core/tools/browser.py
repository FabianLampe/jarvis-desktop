"""MCP-Client für chrome-devtools-mcp — startet den Node-Server als stdio-Subprozess
und steuert einen isolierten Chrome (eigenes, dauerhaftes Jarvis-Profil).

Das mcp-SDK ist async. Wir betreiben einen eigenen Event-Loop in einem Daemon-Thread
(wie jarvis_app/ws_server.py) und bieten synchrone Methoden für den Tool-Loop an.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("jarvis.browser")


class BrowserError(Exception):
    pass


class BrowserController:
    def __init__(self, profile_dir: str = ".chrome-jarvis", channel: str = "stable",
                 headless: bool = False, repo_root: Optional[Path] = None):
        root = repo_root or Path(__file__).resolve().parent.parent.parent
        self._profile = str((root / profile_dir).resolve())
        self._channel = channel
        self._headless = headless
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._session = None          # mcp.ClientSession
        self._ctx_stack = None        # AsyncExitStack zum sauberen Schließen
        self._tools: dict[str, Any] = {}   # Name -> inputSchema
        self._ready = threading.Event()
        self._start_error: Optional[Exception] = None

    # ----------------------------------------------------------- Lebenszyklus

    def ensure_started(self, timeout: float = 90.0) -> None:
        if self._session is not None:
            return
        self._thread = threading.Thread(target=self._run_loop, name="jarvis-browser", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            raise BrowserError("chrome-devtools-mcp nicht rechtzeitig gestartet")
        if self._start_error:
            raise BrowserError(str(self._start_error))

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._connect())
            loop.run_forever()
        except Exception as exc:  # noqa: BLE001
            self._start_error = exc
            self._ready.set()

    async def _connect(self) -> None:
        from contextlib import AsyncExitStack
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        Path(self._profile).mkdir(parents=True, exist_ok=True)
        # --isolated NICHT setzen → dauerhaftes Profil aus --user-data-dir.
        # --headless/--isolated sind boolesche Schalter (kein Wert).
        args = ["-y", "chrome-devtools-mcp@latest",
                "--user-data-dir", self._profile, "--channel", self._channel]
        if self._headless:
            args.append("--headless")
        params = StdioServerParameters(command="npx", args=args)

        self._ctx_stack = AsyncExitStack()
        read, write = await self._ctx_stack.enter_async_context(stdio_client(params))
        self._session = await self._ctx_stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        resp = await self._session.list_tools()
        # mcp-SDK: inputSchema (1.x) bzw. input_schema (2.x)
        self._tools = {t.name: (getattr(t, "inputSchema", None) or getattr(t, "input_schema", None))
                       for t in resp.tools}
        log.info("chrome-devtools-mcp bereit (%d Werkzeuge, Profil %s)",
                 len(self._tools), self._profile)
        self._ready.set()

    def shutdown(self) -> None:
        if self._loop is None:
            return
        async def _close():
            if self._ctx_stack is not None:
                await self._ctx_stack.aclose()
        try:
            fut = asyncio.run_coroutine_threadsafe(_close(), self._loop)
            fut.result(timeout=10)
        except Exception as exc:  # noqa: BLE001
            log.debug("Browser-Shutdown: %s", exc)
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._session = None

    def tool_names(self) -> set[str]:
        return set(self._tools)

    # --------------------------------------------------------- roher MCP-Aufruf

    def _call(self, tool: str, arguments: dict[str, Any]) -> str:
        if self._session is None or self._loop is None:
            raise BrowserError("Browser nicht gestartet")
        if self._needs_page_id(tool) and "pageId" not in arguments:
            pid = self._selected_page_id(self._raw_call("list_pages", {}))
            if pid is None:
                raise BrowserError("Kein offener Tab — zuerst eine Seite öffnen")
            arguments = {"pageId": pid, **arguments}
        return self._raw_call(tool, arguments)

    def _needs_page_id(self, tool: str) -> bool:
        """Neuere chrome-devtools-mcp-Versionen verlangen pageId in fast allen Werkzeugen."""
        schema = self._tools.get(tool) or {}
        return "pageId" in (schema.get("required") or [])

    def _raw_call(self, tool: str, arguments: dict[str, Any]) -> str:
        session, loop = self._session, self._loop
        if session is None or loop is None:
            raise BrowserError("Browser nicht gestartet")

        async def _run():
            result = await session.call_tool(tool, arguments)
            parts = [str(getattr(c, "text", "")) for c in result.content if getattr(c, "type", None) == "text"]
            text = "\n".join(parts) if parts else "(kein Text)"
            # mcp 1.x: isError · mcp 2.x: is_error
            if getattr(result, "isError", False) or getattr(result, "is_error", False):
                raise BrowserError(text)
            return text
        fut = asyncio.run_coroutine_threadsafe(_run(), loop)
        return fut.result(timeout=60)

    # ------------------------------------------------------------ hohe API
    # Mapping auf die rohen chrome-devtools-mcp-Werkzeuge.

    def open_url(self, url: str) -> str:
        return self._call("new_page", {"url": url})

    def search(self, query: str, engine: str = "google") -> str:
        from urllib.parse import quote_plus
        if engine == "youtube":
            url = "https://www.youtube.com/results?search_query=" + quote_plus(query)
        else:
            url = "https://www.google.com/search?q=" + quote_plus(query)
        return self._call("new_page", {"url": url})

    def play_youtube(self, query: str) -> str:
        """Öffnet die YouTube-Suche und navigiert zum ersten Video (spielt es ab)."""
        from urllib.parse import quote_plus
        self._call("new_page", {"url": "https://www.youtube.com/results?search_query="
                                + quote_plus(query)})
        js = ("() => { const a = document.querySelector("
              "'a#video-title, a#video-title-link, ytd-video-renderer a#thumbnail[href]'); "
              "return a ? a.href : ''; }")
        href = self._first_url(js, r"watch\?v=")
        if href:
            return self._call("navigate_page", {"type": "url", "url": href})
        return "Ich finde auf YouTube kein passendes Video, Sir."

    def search_open(self, query: str, engine: str = "google") -> str:
        """Sucht (Bing — toleranter gegen Automatisierung als Google) und öffnet das
        erste organische Ergebnis (für unbekannte Seiten). Bing verlinkt über einen
        Redirect (bing.com/ck/a …u=a1<base64>); die echte URL wird dekodiert."""
        from urllib.parse import quote_plus
        self._call("new_page", {"url": "https://www.bing.com/search?q=" + quote_plus(query)})
        js = ("() => { const a = document.querySelector("
              "'#b_results li.b_algo h2 a[href^=\"http\"], "
              "#b_results li.b_algo a[href^=\"http\"]'); return a ? a.href : ''; }")
        href = self._first_url(js, r"https?://")
        real = self._debing(href)
        if real.startswith("http"):
            return self._call("navigate_page", {"type": "url", "url": real})
        return "Ich finde dazu keine passende Seite, Sir."

    @staticmethod
    def _debing(url: str) -> str:
        """Bing-Redirect (bing.com/ck/a?…u=a1<base64url>) → echte Zieladresse."""
        if "bing.com/ck/" not in url:
            return url
        import base64
        from urllib.parse import parse_qs, urlparse
        u = parse_qs(urlparse(url).query).get("u", [""])[0]
        if u.startswith("a1"):
            u = u[2:]
        try:
            return base64.urlsafe_b64decode(u + "=" * (-len(u) % 4)).decode("utf-8", "ignore")
        except Exception:
            return url

    def _first_url(self, js: str, must_contain: str, skip: tuple[str, ...] = ()) -> str:
        """Führt js mehrfach aus, bis eine passende URL im Ergebnis steht (SPA-Ladezeit)."""
        import re
        import time as _t
        pat = re.compile(r"https?://[^\s\"'<>]+")
        for _ in range(12):
            out = self._call("evaluate_script", {"function": js})
            for cand in pat.findall(out):
                if re.search(must_contain, cand) and not any(s in cand for s in skip):
                    return cand
            _t.sleep(0.6)
        return ""

    def navigate(self, action: str) -> str:
        # navigate_page.type ∈ {url, back, forward, reload}
        return self._call("navigate_page", {"type": action})

    def read_page(self) -> str:
        js = "() => document.body.innerText.slice(0, 4000)"
        return self._call("evaluate_script", {"function": js})

    def snapshot(self) -> str:
        return self._call("take_snapshot", {})

    def browser_snapshot(self) -> str:
        return self.snapshot()

    def click(self, uid: str) -> str:
        return self._call("click", {"uid": uid})

    def type_text(self, uid: str, text: str) -> str:
        return self._call("fill", {"uid": uid, "value": text})

    def press_enter(self) -> str:
        return self._call("press_key", {"key": "Enter"})

    def scroll(self, direction: str) -> str:
        dy = -800 if direction == "up" else 800
        return self._call("evaluate_script", {"function": f"() => window.scrollBy(0, {dy})"})

    def list_tabs(self) -> str:
        return self._call("list_pages", {})

    def close_tab(self) -> str:
        # close_page braucht eine pageId — die aktive Seite aus list_pages lesen
        pages = self._call("list_pages", {})
        pid = self._selected_page_id(pages)
        if pid is None:
            return pages
        return self._call("close_page", {"pageId": pid})

    @staticmethod
    def _selected_page_id(pages_text: str) -> Optional[int]:
        import re
        selected = last = None
        for m in re.finditer(r"^\s*(\d+):\s*(.*)$", pages_text, re.MULTILINE):
            last = int(m.group(1))
            if "[selected]" in m.group(2):
                selected = last
        return selected if selected is not None else last
