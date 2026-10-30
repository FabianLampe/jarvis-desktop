"""GitHub-API und SSH zu ausdrücklich konfigurierten eigenen Hosts."""
from __future__ import annotations

import os
import re
import shlex

import requests

from .extension_utils import I, S, confirm, process_result, result, run
from .registry import ALLOW, CONFIRM, Assessment, Tool, ToolError


def github(ctx, a):
    name = str(a.get("repo") or ctx.cfg.briefing.github_repo)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", name):
        raise ToolError("repo muss owner/name sein")
    action = a.get("action", "issues")
    routes = {"issues": ("GET", "issues"), "pulls": ("GET", "pulls"),
              "create_issue": ("POST", "issues"), "create_pr": ("POST", "pulls"),
              "update_issue": ("PATCH", "issues")}
    if action not in routes:
        raise ToolError("Unbekannte GitHub-Aktion")
    method, route = routes[action]
    if action == "update_issue":
        number = a.get("number")
        if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
            raise ToolError("Gültige Issue-Nummer nötig")
        route += "/" + str(number)
    token = os.environ.get("JARVIS_GITHUB_TOKEN", "")
    if not token and method != "GET":
        raise ToolError("JARVIS_GITHUB_TOKEN fehlt; Token mit passenden Repository-Schreibrechten einrichten")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = {}
    if action in ("create_issue", "update_issue", "create_pr"):
        payload = {k: a[k] for k in ("title", "body") if k in a}
    if action in ("create_issue", "create_pr") and not payload.get("title"):
        raise ToolError("title fehlt")
    if action == "create_pr":
        if not a.get("head") or not a.get("base"):
            raise ToolError("head und base müssen vorhandene Branches benennen")
        payload.update(head=a["head"], base=a["base"], draft=True)
    try:
        r = requests.request(method, f"https://api.github.com/repos/{name}/{route}", headers=headers,
                             params={"per_page": 20, "state": "open"} if method == "GET" else None,
                             json=payload if method != "GET" else None, timeout=(5, 30), allow_redirects=False)
    except requests.RequestException:
        raise ToolError("GitHub-Verbindungsfehler" + (
            "; Status der Schreibaktion unklar, vor Wiederholung auf GitHub prüfen" if method != "GET" else "")) from None
    if not 200 <= r.status_code < 300:
        raise ToolError(f"GitHub HTTP {r.status_code}; Rechte, Repository und Branches prüfen")
    raw = r.json()
    def item(x):
        return {k: x.get(k) for k in ("number", "title", "body", "state", "html_url")}
    if isinstance(raw, list):
        items = [x for x in raw if action != "issues" or "pull_request" not in x]
        return result({"items": [item(x) for x in items], "truncated": bool(getattr(r, "links", {}).get("next"))})
    return result(item(raw))


def assess_github(ctx, a):
    if a.get("action", "issues") not in ("issues", "pulls"):
        return confirm("GITHUB SCHREIBEN")(ctx, a)
    return Assessment(ALLOW)


def ssh_argv(ctx, alias):
    cfg = ctx.cfg.extensions.get("ssh") or {}
    host = (cfg.get("hosts") or {}).get(alias)
    if not cfg.get("enabled") or not isinstance(host, dict):
        raise ToolError("SSH-Host nicht eingerichtet: extensions.ssh.enabled und hosts.<alias> konfigurieren")
    address, user = str(host.get("hostname", "")), str(host.get("user", ""))
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", address) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", user):
        raise ToolError("Ungültiger SSH-Hostname/Benutzer in der Konfiguration")
    port = int(host.get("port", 22))
    if not 1 <= port <= 65535:
        raise ToolError("Ungültiger SSH-Port")
    return ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=5",
            "-p", str(port), f"{user}@{address}"]


def ssh_diagnose(ctx, a):
    service = str(a.get("service", "ollama"))
    if not re.fullmatch(r"[A-Za-z0-9_.@-]+", service) or service.startswith("-"):
        raise ToolError("Ungültiger Dienstname")
    commands = {
        "system": "hostname; uptime; free -m; df -h /",
        "network": "ip -brief address; ip route; getent hosts example.com; ping -c 2 -W 2 1.1.1.1",
        "service": "systemctl status --no-pager -- " + shlex.quote(service),
        "logs": "journalctl --no-pager -n 60 -u " + shlex.quote(service),
    }
    check = a.get("check", "system")
    if check not in commands:
        raise ToolError("check muss system|network|service|logs sein")
    # Jeder Abschnitt meldet seinen Status: ein erfolgreicher letzter Befehl verdeckt keine Fehler.
    script = "status=0\n" + "\n".join(f"{c} || status=1" for c in commands[check].split("; ")) + "\nexit $status"
    return process_result(run(ctx, [*ssh_argv(ctx, a["host"]), script], timeout=45))


def ssh_run(ctx, a):
    argv = a.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(v, str) and "\x00" not in v for v in argv):
        raise ToolError("argv muss eine nicht-leere Stringliste sein")
    return process_result(run(ctx, [*ssh_argv(ctx, a["host"]), shlex.join(argv)], timeout=60))


def assess_diagnose(ctx, a):
    return confirm("SSH DIAGNOSE NACH FREMDINHALT")(ctx, a) if ctx.tainted else Assessment(ALLOW)


def tools():
    return [
        Tool("github", "git", "GitHub Issues/PRs lesen, Issue erstellen/ändern, Draft-PR erstellen.",
             {"repo": S, "action": {**S, "enum": ["issues", "pulls", "create_issue", "update_issue", "create_pr"]},
              "number": I, "title": S, "body": S, "head": S, "base": S}, (), github, assess_github, untrusted=True),
        Tool("ssh_diagnose", "ssh", "Konfigurierten Linux/Pi-Host prüfen: system, network, service oder logs.",
             {"host": S, "check": {**S, "enum": ["system", "network", "service", "logs"]}, "service": S},
             ("host",), ssh_diagnose, assess_diagnose, untrusted=True),
        Tool("ssh_run", "ssh", "Befehl auf konfiguriertem SSH-Host ausführen; Schlüssel-Login und Bestätigung nötig.",
             {"host": S, "argv": {"type": "array", "items": S}}, ("host", "argv"), ssh_run, confirm("SSH BEFEHL AUSFÜHREN"), untrusted=True),
    ]
