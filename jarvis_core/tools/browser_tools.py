"""Browser-Werkzeuge in der gemeinsamen Registry (Adapter auf BrowserController)."""
from __future__ import annotations

from .registry import ALLOW, CONFIRM, Assessment, Tool, ToolContext, ToolError, ToolResult, wrap_untrusted
from .schemas import TOOLS

_UNTRUSTED = {"read_page", "list_tabs", "browser_snapshot"}


def _controller(ctx: ToolContext):
    def make():
        from .browser import BrowserController
        b = ctx.cfg.browser
        return BrowserController(profile_dir=b.profile_dir, channel=b.channel, headless=b.headless)
    return ctx.service("browser", make)


def _handler(name: str):
    def run(ctx: ToolContext, a: dict) -> ToolResult:
        if not ctx.cfg.browser.enabled:
            raise ToolError("Die Browser-Steuerung ist abgeschaltet (config.yaml: browser.enabled)")
        b = _controller(ctx)
        try:
            b.ensure_started()
            out = getattr(b, name)(**a)
        except TypeError as exc:
            raise ToolError(f"Ungültige Argumente für {name}: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"Browser: {type(exc).__name__}: {exc}"[:300]) from exc
        text = str(out or "Erledigt")
        return ToolResult(True, wrap_untrusted(text) if name in _UNTRUSTED else text)
    return run


def _assess_type(ctx: ToolContext, a: dict) -> Assessment:
    if ctx.tainted:
        return Assessment(CONFIRM, f"IM BROWSER TIPPEN (nach Lesen fremder Inhalte): „{a.get('text', '')[:120]}“")
    return Assessment(ALLOW)


def tools() -> list[Tool]:
    from ..agent import _narrate
    out = []
    for t in TOOLS:
        f = t["function"]
        out.append(Tool(
            f["name"], "browser", f["description"], f["parameters"]["properties"],
            tuple(f["parameters"]["required"]), _handler(f["name"]),
            assess=_assess_type if f["name"] == "type_text" else None,
            announce=(lambda n: lambda a: _narrate(n, a))(f["name"]),
            untrusted=f["name"] in _UNTRUSTED))
    return out
