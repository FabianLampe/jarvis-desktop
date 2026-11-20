"""Programmier-Projekte: anlegen, Pakete installieren, committen, pushen.

Projekte liegen in <Arbeitsordner>/Code/<Name> (Standard ~/Jarvis/Code) und bekommen eine eigene .venv.
Pakete aus dem Internet sind fremder Code: `project_install` fragt deshalb IMMER nach, und Paketnamen werden
streng geprüft (nur Name + Versionsangabe — keine URLs, Pfade, Optionen). Alles läuft als argv-Liste ohne Shell.
`git_commit` darf im Arbeitsordner ohne Rückfrage committen (Hooks sind abgeschaltet, Geheimnisse wie .env
werden nie mit aufgenommen); `git_push` bestätigt Sir immer und kennt kein Force."""
from __future__ import annotations

import fnmatch
import re
import shutil
import sys
from pathlib import Path
from typing import Optional

from .developer_tools import repo as _repo_dir
from .network_tools import clean
from .paths import in_workspace, resolve, venv_python
from .registry import (ALLOW, CONFIRM, Assessment, InvalidArgs, Tool, ToolContext, ToolError, ToolResult,
                       wrap_untrusted)
from .shell_tools import _runner

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
# Paketname (+ Extras) und Versionsklauseln wie ==1.2, >=2,<3 — nichts sonst (kein @, /, :, Leerzeichen, -).
_VER = r"(?:==|>=|<=|~=|!=|<|>)[A-Za-z0-9.*+!_]+"
PKG_RE = re.compile(rf"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?(?:\[[A-Za-z0-9,._-]+\])?(?:{_VER}(?:,{_VER})*)?$")
MAX_PACKAGES = 20
LANGUAGES = ("python", "javascript")
INSTALL_TIMEOUT_S = 600
SECRET_GLOBS = (".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*", "id_ecdsa*",
                ".netrc", "credentials*.json", "*.secret", "*.secrets", "secrets.*", "token.json")
BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./-]{0,120}$")
REMOTE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,60}$")

GITIGNORE = """.venv/
__pycache__/
*.pyc
.env
.env.*
*.pem
*.key
.DS_Store
.idea/
.vscode/
node_modules/
dist/
build/
"""


def _exec(ctx: ToolContext, argv: list[str], cwd: Path, timeout: float = 60.0):
    """Einziger Prozessstart dieses Moduls (Tests ersetzen ihn). Gibt ein RunResult zurück."""
    return _runner(ctx).run(argv, cwd, timeout_s=timeout, max_chars=8000, cancel=ctx.cancel)


# ----------------------------------------------------------- Prüfungen

def valid_name(raw) -> str:
    name = str(raw or "").strip()
    if not NAME_RE.fullmatch(name):
        raise InvalidArgs("Der Projektname darf nur Buchstaben, Ziffern, _ und - enthalten (höchstens 64 Zeichen).")
    return name


def valid_packages(raw) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise InvalidArgs("packages muss eine Liste von Paketnamen sein.")
    out = []
    for p in raw:
        p = str(p).strip()
        if not PKG_RE.fullmatch(p):
            raise InvalidArgs(f"Paketangabe „{clean(p, 40)}“ ist nicht erlaubt (nur Name und Version, z. B. requests oder flask>=3).")
        out.append(p)
    if len(out) > MAX_PACKAGES:
        raise InvalidArgs(f"Höchstens {MAX_PACKAGES} Pakete auf einmal.")
    return list(dict.fromkeys(out))


def code_dir(ctx: ToolContext) -> Path:
    return ctx.workspace / "Code"


def project_dir(ctx: ToolContext, raw: Optional[str]) -> Path:
    """Projektordner aus Namen (unter Code/) oder Pfad (im Arbeitsordner)."""
    raw = str(raw or "").strip()
    if not raw:
        raise InvalidArgs("Kein Projekt angegeben.")
    p = code_dir(ctx) / raw if NAME_RE.fullmatch(raw) else resolve(ctx, raw)
    if not p.is_dir():
        raise ToolError(f"Ein Projekt „{clean(raw, 40)}“ gibt es nicht (erwartet in {code_dir(ctx)}).")
    if not in_workspace(ctx, p):
        raise ToolError("Pakete installiere ich nur in Projekten im Arbeitsordner.")
    return p


# ------------------------------------------------------------ Anlegen

def _readme(name: str, language: str) -> str:
    run = "`.venv/bin/python main.py`" if language == "python" else "`node main.js`"
    return f"# {name}\n\nAngelegt von Jarvis.\n\n## Starten\n\n{run}\n"


def _pip_install(ctx: ToolContext, root: Path, packages: list[str]) -> ToolResult:
    py = venv_python(root)
    if py is None:
        raise ToolError("Dieses Projekt hat keine .venv (nur Python-Projekte von project_create).")
    ctx.tainted = True                       # pip-Ausgabe und frisch geladener Code sind fremd
    res = _exec(ctx, [str(py), "-m", "pip", "install", "--disable-pip-version-check", "--no-input", *packages],
                root, INSTALL_TIMEOUT_S)
    tail = (res.stdout + "\n" + res.stderr).strip()[-1500:]
    if not res.ok:
        return ToolResult(False, "FEHLGESCHLAGEN: pip install\n" + wrap_untrusted(tail))
    return ToolResult(True, f"Installiert in {root.name}: {', '.join(packages)}", {"packages": packages})


def project_create(ctx: ToolContext, a: dict) -> ToolResult:
    name = valid_name(a.get("name"))
    language = str(a.get("language") or "python").lower()
    if language not in LANGUAGES:
        raise ToolError("Ich lege Projekte für " + " oder ".join(LANGUAGES) + " an.")
    packages = valid_packages(a.get("packages"))
    if packages and language != "python":
        raise ToolError("Pakete installiere ich nur in Python-Projekten.")
    root = code_dir(ctx) / name
    if root.exists():
        raise ToolError(f"Ein Projekt „{name}“ gibt es schon in {code_dir(ctx)}.")
    root.mkdir(parents=True)
    (root / "README.md").write_text(_readme(name, language), encoding="utf-8")
    (root / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    if language == "python":
        (root / "main.py").write_text('def main() -> None:\n    print("Hallo von %s")\n\n\nif __name__ == "__main__":\n    main()\n' % name,
                                      encoding="utf-8")
    else:
        (root / "main.js").write_text(f'console.log("Hallo von {name}");\n', encoding="utf-8")
    notes = [f"Projekt {name} angelegt: {root}"]
    ok = True
    if language == "python":
        res = _exec(ctx, [shutil.which("python3") or sys.executable, "-m", "venv", ".venv"], root, 180)
        if res.ok:
            notes.append("virtuelle Umgebung .venv erstellt")
        else:
            ok = False
            notes.append("FEHLER bei .venv: " + clean((res.stderr or res.error or "").strip()[-160:], 160))
    res = _exec(ctx, ["git", "init", "-q", "-b", "main"], root, 30)
    if not res.ok:
        res = _exec(ctx, ["git", "init", "-q"], root, 30)
    notes.append("Git-Repository angelegt" if res.ok else "git init fehlgeschlagen")
    ok = ok and res.ok
    if packages and ok:
        inst = _pip_install(ctx, root, packages)
        ok = inst.ok
        notes.append(inst.text)
    return ToolResult(ok, "; ".join(notes) if ok else "FEHLGESCHLAGEN: " + "; ".join(notes),
                      {"path": str(root), "name": name})


def _assess_create(ctx: ToolContext, a: dict) -> Assessment:
    name = valid_name(a.get("name"))
    packages = valid_packages(a.get("packages"))
    if packages:
        return Assessment(CONFIRM, f"PROJEKT {name} ANLEGEN und Pakete aus dem Internet installieren: {', '.join(packages)}")
    if ctx.tainted:
        return Assessment(CONFIRM, f"PROJEKT {name} ANLEGEN (nach Lesen fremder Inhalte)")
    return Assessment(ALLOW)


def project_install(ctx: ToolContext, a: dict) -> ToolResult:
    packages = valid_packages(a.get("packages"))
    if not packages:
        raise InvalidArgs("Welche Pakete soll ich installieren?")
    return _pip_install(ctx, project_dir(ctx, a.get("project")), packages)


def _assess_install(ctx: ToolContext, a: dict) -> Assessment:
    packages = valid_packages(a.get("packages"))
    if not packages:
        raise InvalidArgs("Welche Pakete soll ich installieren?")
    root = project_dir(ctx, a.get("project"))
    return Assessment(CONFIRM, f"PAKETE INSTALLIEREN (lädt fremden Code aus dem Internet) in {root.name}: {', '.join(packages)}")


# --------------------------------------------------------------- Git

def _git(ctx: ToolContext, root: Path, args: list[str], timeout: float = 60.0):
    # Keine Hooks, keine externen Helfer: ein Repository darf hier nichts nebenbei ausführen.
    return _exec(ctx, ["git", "--no-pager", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
                       "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
                 root, timeout)


def _is_secret(path: str) -> bool:
    base = path.rsplit("/", 1)[-1].casefold()
    if base.endswith((".example", ".sample", ".template", ".dist")):       # Vorlagen ohne Geheimnisse
        return False
    return any(fnmatch.fnmatch(base, g) for g in SECRET_GLOBS)


def _commit_paths(ctx: ToolContext, root: Path, raw) -> list[str]:
    if raw in (None, []):
        return []
    if not isinstance(raw, list) or len(raw) > 200:
        raise InvalidArgs("paths muss eine Liste mit höchstens 200 Pfaden sein.")
    out = []
    for item in raw:
        text = str(item)
        if text.startswith("-"):
            raise InvalidArgs("Pfade dürfen nicht mit „-“ beginnen.")
        p = resolve(ctx, str(root / text))
        if not (p == root or root in p.parents):
            raise InvalidArgs(f"„{clean(text, 40)}“ liegt nicht im Repository.")
        out.append(str(p.relative_to(root)) or ".")
    return out


def _message(raw) -> str:
    msg = str(raw or "").strip()
    if not msg or len(msg) > 500 or "\x00" in msg:
        raise InvalidArgs("Die Commit-Nachricht fehlt oder ist zu lang (höchstens 500 Zeichen).")
    return msg


def git_commit(ctx: ToolContext, a: dict) -> ToolResult:
    root = _repo_dir(ctx, a)
    msg = _message(a.get("message"))
    paths = _commit_paths(ctx, root, a.get("paths"))
    add = _git(ctx, root, ["add", "--", *paths] if paths else ["add", "-A"])
    if not add.ok:
        raise ToolError("git add fehlgeschlagen: " + clean((add.stderr or add.error or "").strip()[-200:], 200))
    staged = _git(ctx, root, ["diff", "--cached", "--name-only"])
    skipped = [f for f in staged.stdout.splitlines() if f and _is_secret(f)]
    has_head = _git(ctx, root, ["rev-parse", "--verify", "-q", "HEAD"]).ok
    kept = []
    for f in skipped:                                         # NEUE Geheimnisse nie ins Repository
        if has_head and _git(ctx, root, ["cat-file", "-e", f"HEAD:{f}"]).ok:
            kept.append(f)                                    # schon getrackt: nicht anfassen (kein Löschen stagen)
            continue
        _git(ctx, root, ["reset", "-q", "--", f] if has_head else ["rm", "--cached", "-q", "--", f])
    skipped = [f for f in skipped if f not in kept]
    res = _git(ctx, root, ["commit", "-q", "-m", msg])
    out = (res.stdout + "\n" + res.stderr).strip()
    if not res.ok:
        if "nothing to commit" in out or "nichts zu committen" in out or "nothing added" in out:
            return ToolResult(False, "Es gibt nichts zu committen" + (f" (ausgelassen wegen Geheimnissen: {', '.join(clean(s, 40) for s in skipped)})" if skipped else "") + ".")
        return ToolResult(False, "FEHLGESCHLAGEN: git commit\n" + wrap_untrusted(out[-800:]))
    head = _git(ctx, root, ["log", "-1", "--format=%h"]).stdout.strip()
    note = f" Nicht aufgenommen (Geheimnisse): {', '.join(clean(s, 40) for s in skipped)}." if skipped else ""
    return ToolResult(True, f"Commit {clean(head, 12)} erstellt: „{clean(msg, 80)}“.{note}", {"commit": head})


def _assess_commit(ctx: ToolContext, a: dict) -> Assessment:
    root = _repo_dir(ctx, a)
    msg = _message(a.get("message"))
    _commit_paths(ctx, root, a.get("paths"))
    if not in_workspace(ctx, root):
        return Assessment(CONFIRM, f"GIT COMMIT außerhalb des Arbeitsordners ({root}): „{clean(msg, 80)}“")
    if ctx.tainted:
        return Assessment(CONFIRM, f"GIT COMMIT (nach Lesen fremder Inhalte) in {root.name}: „{clean(msg, 80)}“")
    return Assessment(ALLOW)


def _remote_and_branch(ctx: ToolContext, a: dict, root: Path) -> tuple[str, Optional[str]]:
    remote = str(a.get("remote") or "origin").strip()
    branch = str(a.get("branch") or "").strip() or None
    if not REMOTE_RE.fullmatch(remote):
        raise InvalidArgs("Ungültiger Remote-Name.")
    if branch is not None and (not BRANCH_RE.fullmatch(branch) or ".." in branch or "@{" in branch):
        raise InvalidArgs("Ungültiger Branchname (kein Force, keine Refspecs).")
    remotes = _git(ctx, root, ["remote"]).stdout.split()
    if remote not in remotes:
        raise ToolError(f"Ein Remote „{remote}“ ist in diesem Repository nicht eingerichtet"
                        + (f" (vorhanden: {', '.join(clean(r, 20) for r in remotes)})." if remotes else "."))
    return remote, branch


def git_push(ctx: ToolContext, a: dict) -> ToolResult:
    root = _repo_dir(ctx, a)
    remote, branch = _remote_and_branch(ctx, a, root)
    res = _git(ctx, root, ["push", remote, branch or "HEAD"], 120)
    out = (res.stdout + "\n" + res.stderr).strip()
    if not res.ok:
        return ToolResult(False, "FEHLGESCHLAGEN: git push\n" + wrap_untrusted(out[-800:]))
    return ToolResult(True, f"Gepusht nach {remote}" + (f" ({branch})" if branch else "") + ".\n" + wrap_untrusted(out[-400:]))


def _assess_push(ctx: ToolContext, a: dict) -> Assessment:
    root = _repo_dir(ctx, a)
    remote, branch = _remote_and_branch(ctx, a, root)
    url = _git(ctx, root, ["remote", "get-url", remote]).stdout.strip()
    return Assessment(CONFIRM, f"GIT PUSH von {root.name} nach {remote} ({clean(url, 100)}), Branch {branch or 'aktueller'}")


def tools() -> list[Tool]:
    S = {"type": "string"}
    L = {"type": "array", "items": S}
    return [
        Tool("project_create", "projects",
             "Legt ein Programmier-Projekt in ~/Jarvis/Code/<name> an (README, .gitignore, main.py, eigene .venv, "
             "git init). packages und language sind optional (Standard python, keine Pakete) — nicht nachfragen; "
             "packages werden mit Rückfrage installiert. language: python oder javascript.",
             {"name": S, "language": {**S, "enum": list(LANGUAGES)}, "packages": L}, ("name",),
             project_create, _assess_create, announce=lambda a: "Ich lege das Projekt an, Sir."),
        Tool("project_install", "projects",
             "Installiert Python-Pakete (pip) in die .venv eines Projekts aus ~/Jarvis/Code (project = Name).",
             {"project": S, "packages": L}, ("project", "packages"), project_install, _assess_install,
             untrusted=True, announce=lambda a: "Ich installiere die Pakete, Sir."),
        Tool("git_commit", "git",
             "Staged Änderungen (paths oder alles) und committet mit message. Geheimnisdateien wie .env bleiben außen vor.",
             {"repo": S, "message": S, "paths": L}, ("message",), git_commit, _assess_commit,
             announce=lambda a: "Ich committe, Sir."),
        Tool("git_push", "git", "Pusht den aktuellen oder genannten Branch zum Remote (Standard origin). Nie mit Force.",
             {"repo": S, "remote": S, "branch": S}, (), git_push, _assess_push, untrusted=True),
    ]
