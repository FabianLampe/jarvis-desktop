"""Git, gezielte pytest-Läufe und überprüfbare Datei-Ergebnisse."""
from __future__ import annotations

import hashlib
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from .extension_utils import B, I, S, confirm, process_result, result, run
from .paths import resolve
from .registry import Tool, ToolError


def repo(ctx, a):
    p = resolve(ctx, str(a.get("repo") or "."), must_exist=True)
    if not p.is_dir():
        raise ToolError("repo muss ein Ordner sein")
    return p


def git(ctx, a, args):
    # Keine externen Diff-/Textconv-/Fsmonitor-Helfer beim Lesen ausführen.
    return run(ctx, ["git", "--no-pager", "--no-optional-locks", "-c", "core.fsmonitor=false",
                     "-c", "core.hooksPath=/dev/null", *args], repo(ctx, a))


def git_status(ctx, a):
    return process_result(git(ctx, a, ["status", "--short", "--branch"]))


def git_diff(ctx, a):
    args = ["diff", "--no-ext-diff", "--no-textconv"]
    if a.get("staged"):
        args.append("--cached")
    args.append("--")
    if a.get("path"):
        root = repo(ctx, a)
        p = resolve(ctx, str(root / a["path"]))
        if not p.is_relative_to(root):
            raise ToolError("Diff-Pfad muss im Repository liegen")
        args.append(str(p.relative_to(root)))
    return process_result(git(ctx, a, args))


def git_branch(ctx, a):
    name = str(a.get("name", ""))
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,120}", name) or ".." in name or "@{" in name:
        raise ToolError("Ungültiger Branchname")
    args = ["switch", "-c", name] if a.get("switch", True) else ["branch", "--", name]
    return process_result(git(ctx, a, args))


def run_tests(ctx, a):
    root = repo(ctx, a)
    targets = a.get("targets") or ["tests"]
    if not isinstance(targets, list) or not 1 <= len(targets) <= 20:
        raise ToolError("targets muss eine Liste mit 1–20 relativen Testpfaden sein")
    for t in targets:
        if not isinstance(t, str) or t.startswith("-"):
            raise ToolError("Nur Testpfade / pytest-Node-IDs, keine Optionen")
        p = resolve(ctx, str(root / t.split("::")[0]), must_exist=True)
        if not p.is_relative_to(root):
            raise ToolError("Tests müssen im Projekt liegen")
    # JUnit liefert echte Zähler, unabhängig von Sprache und Format der Konsolenausgabe.
    with tempfile.TemporaryDirectory(prefix="jarvis-tests-") as tmp:
        report = Path(tmp) / "results.xml"
        res = run(ctx, [sys.executable, "-m", "pytest", *targets, "-q", "--tb=short",
                        f"--junitxml={report}"], root, float(a.get("timeout_s", 120)))
        counts = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
        if report.exists():
            try:
                tree = ET.parse(report)
                for suite in tree.iter("testsuite"):
                    for key in counts:
                        counts[key] += int(suite.get(key, 0))
            except (ET.ParseError, ValueError):
                pass
        data = {**counts, "exit_code": res.exit_code, "timed_out": res.timed_out,
                "cancelled": res.cancelled, "output": (res.stdout + "\n" + res.stderr)[-12000:]}
        return result(data, res.ok and counts["tests"] > 0 and not counts["failures"] and not counts["errors"])


def verify_file(ctx, a):
    p = resolve(ctx, str(a["path"]))
    if not p.is_file():
        return result({"path": str(p), "exists": False}, False)
    if p.stat().st_size > 25_000_000:
        raise ToolError("Dateiprüfung auf 25 MB begrenzt")
    raw = p.read_bytes()
    data = {"path": str(p), "exists": True, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    checks = {"exists": True}
    if "contains" in a:
        checks["contains"] = str(a["contains"]) in raw.decode("utf-8", errors="replace")
    if "sha256" in a:
        checks["sha256"] = data["sha256"] == str(a["sha256"]).lower()
    if "min_bytes" in a:
        checks["min_bytes"] = len(raw) >= int(a["min_bytes"])
    return result({**data, "checks": checks}, all(checks.values()))


def tools():
    return [
        Tool("git_status", "git", "Git-Branch und geänderte Dateien anzeigen.", {"repo": S}, (), git_status, untrusted=True),
        Tool("git_diff", "git", "Git-Änderungen lesen, optional staged oder path.", {"repo": S, "staged": B, "path": S}, (), git_diff, untrusted=True),
        Tool("git_branch", "git", "Neuen Branch erstellen, optional direkt wechseln.", {"repo": S, "name": S, "switch": B}, ("name",), git_branch, confirm("GIT BRANCH")),
        Tool("run_tests", "testing", "pytest ausführen; echte Test-/Fehlerzähler zurückgeben.",
             {"repo": S, "targets": {"type": "array", "items": S}, "timeout_s": I}, (), run_tests, confirm("PROJEKTTESTS AUSFÜHREN"), untrusted=True),
        Tool("verify_file", "testing", "Datei auf Existenz, Inhalt, Mindestgröße oder SHA256 prüfen.",
             {"path": S, "contains": S, "sha256": S, "min_bytes": I}, ("path",), verify_file, untrusted=True),
    ]
