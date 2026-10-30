"""macOS Accessibility: begrenzter Elementbaum und gezielte Aktionen auf dessen IDs."""
from __future__ import annotations

import sys
import secrets
import time

from .extension_utils import S, confirm, result
from .registry import Tool, ToolError


class MacUI:
    def __init__(self):
        if sys.platform != "darwin":
            raise ToolError("Mac-UI-Steuerung ist nur auf macOS verfügbar")
        try:
            import ApplicationServices as AX
            import AppKit
            import Quartz
        except ImportError:
            raise ToolError("Mac-UI-Pakete fehlen: pip install -r requirements-tools.txt") from None
        if not AX.AXIsProcessTrusted():
            raise ToolError("Bedienungshilfen-Zugriff fehlt: Systemeinstellungen → Datenschutz & Sicherheit → Bedienungshilfen; startendes Terminal/Jarvis freigeben")
        self.ax, self.apps, self.q = AX, AppKit, Quartz
        self.elements, self.app, self.at = {}, None, 0

    def attr(self, element, name, default=None):
        err, value = self.ax.AXUIElementCopyAttributeValue(element, name, None)
        return default if err else value

    def snapshot(self, app_name=None):
        ws = self.apps.NSWorkspace.sharedWorkspace()
        if app_name:
            matches = [a for a in ws.runningApplications() if str(a.localizedName()).casefold() == app_name.casefold()]
            if len(matches) != 1:
                raise ToolError("App nicht eindeutig gefunden; exakten Namen einer laufenden App angeben")
            app = matches[0]
        else:
            app = ws.frontmostApplication()
        self.elements.clear()
        self.app, self.at = app, time.monotonic()
        prefix = secrets.token_hex(4)
        pending = [(self.ax.AXUIElementCreateApplication(app.processIdentifier()), 0)]
        rows = []
        while pending and len(rows) < 200:
            element, depth = pending.pop(0)
            eid = f"ax{prefix}:{len(rows) + 1}"
            self.elements[eid] = element
            role = str(self.attr(element, "AXRole", ""))
            secure = role == "AXSecureTextField" or self.attr(element, "AXSubrole") == "AXSecureTextField"
            value = self.attr(element, "AXValue", "") if not secure else "[geschützt]"
            rows.append({"id": eid, "role": role, "title": str(self.attr(element, "AXTitle", ""))[:150],
                         "description": str(self.attr(element, "AXDescription", ""))[:150],
                         "value": str(value)[:200], "depth": depth})
            if depth < 8:
                children = self.attr(element, "AXChildren", []) or []
                pending.extend((e, depth + 1) for e in list(children)[:200])
        return {"app": str(app.localizedName()), "pid": int(app.processIdentifier()),
                "elements": rows, "truncated": bool(pending), "valid_for_s": 30}

    def action(self, a):
        if self.app is None or time.monotonic() - self.at > 30:
            raise ToolError("Zuerst einen frischen mac_ui_snapshot abrufen (IDs gelten 30 Sekunden)")
        action = a["action"]
        if action in ("click", "type"):
            element = self.elements.get(a.get("id"))
            if element is None or not self.attr(element, "AXRole"):
                raise ToolError("Unbekannte/veraltete Element-ID; Snapshot erneuern")
            if action == "click":
                err = self.ax.AXUIElementPerformAction(element, "AXPress")
            else:
                if (self.attr(element, "AXRole") not in ("AXTextField", "AXTextArea", "AXComboBox")
                        or self.attr(element, "AXSubrole") == "AXSecureTextField"):
                    raise ToolError("Dieses Element unterstützt keine direkte Texteingabe")
                err = self.ax.AXUIElementSetAttributeValue(element, "AXValue", str(a.get("text", "")))
            if err:
                raise ToolError(f"macOS hat die UI-Aktion abgelehnt (AX-Fehler {err})")
        elif action == "key":
            # Tastendrücke dürfen nicht versehentlich an eine inzwischen gewechselte App gehen.
            front = self.apps.NSWorkspace.sharedWorkspace().frontmostApplication()
            if front.processIdentifier() != self.app.processIdentifier():
                raise ToolError("Vordergrund-App hat gewechselt; Snapshot erneuern")
            keys = {"return": 36, "tab": 48, "space": 49, "escape": 53, "backspace": 51,
                    "left": 123, "right": 124, "down": 125, "up": 126}
            keys.update({"a":0,"s":1,"d":2,"f":3,"h":4,"g":5,"z":6,"x":7,"c":8,"v":9,"b":11,
                         "q":12,"w":13,"e":14,"r":15,"y":16,"t":17,"1":18,"2":19,"3":20,"4":21,
                         "6":22,"5":23,"9":25,"7":26,"8":28,"0":29,"o":31,"u":32,"i":34,"p":35,
                         "l":37,"j":38,"k":40,"n":45,"m":46})
            # Layoutabhängige Undo/Redo-Kürzel nicht als falsche Taste senden.
            keys.pop("y")
            keys.pop("z")
            parts = str(a.get("key", "")).lower().split("+")
            mods = {"cmd": self.q.kCGEventFlagMaskCommand, "ctrl": self.q.kCGEventFlagMaskControl,
                    "alt": self.q.kCGEventFlagMaskAlternate, "shift": self.q.kCGEventFlagMaskShift}
            if parts[-1] not in keys or any(m not in mods for m in parts[:-1]):
                raise ToolError("Taste nicht unterstützt; z. B. return, tab, cmd+s, cmd+shift+s")
            flags = 0
            for mod in parts[:-1]:
                flags |= mods[mod]
            for down in (True, False):
                event = self.q.CGEventCreateKeyboardEvent(None, keys[parts[-1]], down)
                self.q.CGEventSetFlags(event, flags)
                self.q.CGEventPost(self.q.kCGHIDEventTap, event)
        else:
            raise ToolError("action muss click|type|key sein")
        # Aktionen können den Elementbaum ändern; IDs danach grundsätzlich erneuern.
        self.at = 0
        self.elements.clear()
        return {"performed": action, "app": str(self.app.localizedName()), "refresh_snapshot": True}


def controller(ctx):
    if not (ctx.cfg.extensions.get("mac_ui") or {}).get("enabled"):
        raise ToolError("Mac-UI deaktiviert; extensions.mac_ui.enabled aktivieren und Bedienungshilfen freigeben")
    # Element-IDs sind auf eine Aufgabe beschränkt, nie zwischen Aufgaben geteilt.
    if "mac_ui" not in ctx.services:
        ctx.services["mac_ui"] = MacUI()
    return ctx.services["mac_ui"]


def assess_action(ctx, a):
    ui = ctx.services.get("mac_ui")
    label = ""
    if ui is not None and getattr(ui, "app", None) is not None:
        label = str(ui.app.localizedName())
        element = ui.elements.get(a.get("id"))
        if element is not None:
            label += " / " + str(ui.attr(element, "AXRole", "")) + " / " + str(ui.attr(element, "AXTitle", ""))
    return confirm("MAC-APP BEDIENEN " + label)(ctx, a)


def tools():
    return [
        Tool("mac_ui_snapshot", "mac_ui", "Laufende Mac-App/Fensterelemente mit kurzlebigen IDs lesen; app leer = Vordergrund.",
             {"app": S}, (), lambda c, a: result(controller(c).snapshot(a.get("app"))), untrusted=True),
        Tool("mac_ui_action", "mac_ui", "Mac-Element aus frischem Snapshot klicken/beschreiben oder Tastenkürzel in derselben Vordergrund-App senden.",
             {"action": {**S, "enum": ["click", "type", "key"]}, "id": S, "text": S, "key": S},
             ("action",), lambda c, a: result(controller(c).action(a)), assess_action),
    ]
