"""macOS Accessibility: begrenzter Elementbaum und gezielte Aktionen auf dessen IDs."""
from __future__ import annotations

import sys
import secrets
import time

from . import mac_script as ms
from .extension_utils import S, confirm, result
from .registry import ALLOW, CONFIRM, Assessment, Tool, ToolError, ToolResult


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
            raise ToolError(ms.ACCESSIBILITY_HELP)
        # Nur das systemweite AX-Objekt setzt den Timeout für alle Elemente dieses Prozesses.
        AX.AXUIElementSetMessagingTimeout(AX.AXUIElementCreateSystemWide(), 2.0)
        self.ax, self.apps, self.q = AX, AppKit, Quartz
        self.elements, self.app, self.at = {}, None, 0

    def attr(self, element, name, default=None):
        if getattr(self, "cancel", None) is not None and self.cancel.is_set():
            raise ToolError("Mac-UI-Auftrag abgebrochen")
        deadline = getattr(self, "_read_deadline", None)
        if deadline is not None and time.monotonic() >= deadline:
            raise ToolError("Zeitlimit beim Lesen der Mac-Oberfläche erreicht")
        err, value = self.ax.AXUIElementCopyAttributeValue(element, name, None)
        return default if err else value

    def snapshot(self, app_name=None):
        self._read_deadline = time.monotonic() + 10
        try:
            return self._snapshot(app_name)
        except Exception:
            self.elements.clear()
            self.app, self.at = None, 0
            raise
        finally:
            self._read_deadline = None

    def _snapshot(self, app_name=None):
        ws = self.apps.NSWorkspace.sharedWorkspace()
        if app_name:
            matches = [a for a in ws.runningApplications() if str(a.localizedName()).casefold() == app_name.casefold()]
            if len(matches) != 1:
                raise ToolError("App nicht eindeutig gefunden; exakten Namen einer laufenden App angeben")
            app = matches[0]
        else:
            app = ws.frontmostApplication()
        if app is None:
            raise ToolError("Keine aktive Mac-App gefunden")
        self.elements.clear()
        self.app, self.at = app, time.monotonic()
        prefix = secrets.token_hex(4)
        pending = [(self.ax.AXUIElementCreateApplication(app.processIdentifier()), 0)]
        rows = []
        while pending and len(rows) < 200:
            element, depth = pending.pop(0)
            eid = f"ax{prefix}:{len(rows) + 1}"
            self.elements[eid] = element
            role = self.attr(element, "AXRole")
            if not role:
                raise ToolError("Mac-App antwortet nicht oder Elementbaum ist veraltet; Snapshot erneuern")
            role = str(role)
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
        try:
            return self._action(a)
        finally:
            self.at = 0
            self.elements.clear()

    def _action(self, a):
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
        raise ToolError("Mac-App-Steuerung ist ausgeschaltet; in config.yaml extensions.mac_ui.enabled: true setzen "
                        "und unter Systemeinstellungen → Datenschutz & Sicherheit → Bedienungshilfen das Terminal freigeben")
    # Element-IDs sind auf eine Aufgabe beschränkt, nie zwischen Aufgaben geteilt.
    if "mac_ui" not in ctx.services:
        ctx.services["mac_ui"] = MacUI()
    ctx.services["mac_ui"].cancel = ctx.cancel
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


# ----------------------------------------------------- Text in eine App tippen

# Fest im Code: App-Name und Text kommen ausschließlich als argv an, nie im Skripttext.
TYPE_SCRIPT = """on run argv
    set appName to item 1 of argv
    set theText to item 2 of argv
    tell application appName to activate
    delay 0.7
    set wantId to id of application appName
    tell application "System Events"
        set frontId to bundle identifier of first application process whose frontmost is true
        if frontId is not wantId then error "FRONT_MISMATCH"
        keystroke theText
    end tell
end run"""
MAX_TYPE_CHARS = 2000
# Hier würde getippter Text Befehle auslösen oder Zugangsdaten betreffen: immer Rückfrage.
SENSITIVE_APPS = {"terminal", "iterm", "iterm2", "warp", "ghostty", "alacritty", "kitty", "hyper", "wezterm",
                  "systemeinstellungen", "system settings", "schlüsselbundverwaltung", "keychain access",
                  "passwörter", "passwords", "visual studio code", "cursor", "xcode", "pycharm", "pycharm ce", "zed",
                  "sublime text", "skripteditor", "script editor", "automator", "kurzbefehle", "shortcuts"}


# Deutsche Bezeichnungen, die Sir sagt, ↔ Programmname im Ordner Programme.
APP_ALIASES = {"notizen": "Notes", "kalender": "Calendar", "erinnerungen": "Reminders", "nachrichten": "Messages",
               "kontakte": "Contacts", "systemeinstellungen": "System Settings", "mail": "Mail", "safari": "Safari",
               "vorschau": "Preview", "rechner": "Calculator", "karten": "Maps", "musik": "Music",
               "kurzbefehle": "Shortcuts", "passwörter": "Passwords"}


def _installed(name: str):
    from ..tool_intent import installed_app
    return installed_app(APP_ALIASES.get(name.casefold(), name))


def _type_target(a: dict) -> str:
    name = str(a.get("app", "")).strip()
    if not name or any(c in name for c in "/\\\n\x00") or name.startswith("-"):
        raise ToolError("Bitte den App-Namen angeben, z. B. Notizen oder TextEdit.")
    app = _installed(name)
    if app is None:
        raise ToolError(f"Eine App „{name[:40]}“ finde ich nicht.")
    return app


def _mac_ui_enabled(ctx) -> bool:
    return bool((ctx.cfg.extensions.get("mac_ui") or {}).get("enabled"))


def app_type_text(ctx, a: dict) -> ToolResult:
    if not _mac_ui_enabled(ctx):
        raise ToolError("Mac-App-Steuerung ist ausgeschaltet; in config.yaml extensions.mac_ui.enabled: true setzen.")
    app = _type_target(a)
    text = str(a.get("text", ""))
    if not text or len(text) > MAX_TYPE_CHARS:
        raise ToolError(f"Der Text muss zwischen 1 und {MAX_TYPE_CHARS} Zeichen lang sein.")
    ms.osa(TYPE_SCRIPT, app, text, app=app, timeout=30)
    return ToolResult(True, f"In {app} getippt ({len(text)} Zeichen).")


def assess_type_text(ctx, a: dict) -> Assessment:
    app = _type_target(a)
    text = str(a.get("text", ""))
    preview = text[:80].replace("\n", " ") + ("…" if len(text) > 80 else "")
    if app.casefold() in SENSITIVE_APPS:
        return Assessment(CONFIRM, f"TEXT IN {app.upper()} TIPPEN (hier können Eingaben Befehle auslösen): „{preview}“")
    if ctx.tainted:
        return Assessment(CONFIRM, f"TEXT IN {app.upper()} TIPPEN (nach Lesen fremder Inhalte): „{preview}“")
    return Assessment(ALLOW)


def tools():
    return [
        Tool("mac_ui_snapshot", "mac_ui", "Laufende Mac-App/Fensterelemente mit kurzlebigen IDs lesen; app leer = Vordergrund.",
             {"app": S}, (), lambda c, a: result(controller(c).snapshot(a.get("app"))), untrusted=True),
        Tool("mac_ui_action", "mac_ui", "Mac-Element aus frischem Snapshot klicken/beschreiben oder Tastenkürzel in derselben Vordergrund-App senden.",
             {"action": {**S, "enum": ["click", "type", "key"]}, "id": S, "text": S, "key": S},
             ("action",), lambda c, a: result(controller(c).action(a)), assess_action),
        Tool("app_type_text", "mac_ui",
             "Aktiviert eine Mac-App (z. B. Notizen, TextEdit, Pages) und tippt Text hinein, als würde Sir tippen. "
             "Für einfaches Schreiben in eine App; für Klicks und Menüs mac_ui_snapshot/mac_ui_action.",
             {"app": S, "text": S}, ("app", "text"), app_type_text, assess_type_text,
             announce=lambda a: f"Ich tippe in {str(a.get('app', 'die App'))[:30]}, Sir."),
    ]
