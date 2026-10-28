"""Terminal-Werkzeuge: Befehle (ohne Shell), Skripte, ausdrücklicher Shell-Aufruf,
Hintergrundjobs, Prozessliste. Die Richtlinie (assess_*) entscheidet, was direkt
laufen darf und was die Bestätigung des Nutzers braucht."""
from __future__ import annotations

import os
import re
import shlex
import sys
from pathlib import Path
from typing import Optional

import psutil

from .paths import in_workspace, is_protected, resolve
from .process import ProcessRunner, RunResult
from .registry import (ALLOW, CONFIRM, DENY, Assessment, Tool, ToolContext, ToolError,
                       ToolResult, wrap_untrusted)

# Befehle, die ohne Rückfrage laufen dürfen (rein lesend)
READONLY = {
    "ls", "cat", "pwd", "echo", "grep", "egrep", "head", "tail", "wc", "date", "whoami",
    "uname", "sw_vers", "df", "du", "which", "ps", "stat", "file", "sort", "uniq", "cut",
    "tr", "basename", "dirname", "realpath", "hostname", "uptime", "id", "printf", "diff",
    "sysctl", "system_profiler", "tree", "true", "false", "test",
}
# sed/awk/less/more sind KEINE reinen Leser (awk system(), sed w/e, less !cmd) → normale Regeln
WRAPPERS = {"env", "xargs", "nohup", "nice", "time", "command", "exec", "caffeinate", "timeout", "stdbuf"}
GIT_READONLY = {"status", "log", "diff", "show", "rev-parse", "ls-files", "blame", "describe", "shortlog"}
_ENV_RISKY = ("GIT_", "NODE_OPTIONS", "NODE_PATH", "PYTHON", "PERL", "RUBY", "BASH_ENV", "ENV", "ZDOTDIR",
              "PATH", "SHELLOPTS", "PS4", "PROMPT_COMMAND", "NPM_CONFIG_", "npm_config_", "LESSOPEN", "EDITOR",
              "VISUAL", "PAGER", "GIT", "SSH_", "IFS")
# Befehle, die immer bestätigt werden müssen (löschen, Rechte, System, Prozesse)
DANGEROUS = {
    "rm", "rmdir", "unlink", "shred", "dd", "mkfs", "fdisk", "diskutil", "newfs", "shutdown",
    "reboot", "halt", "launchctl", "kill", "killall", "pkill", "chown", "chmod", "chgrp",
    "mv", "ln", "defaults", "networksetup", "pmset", "csrutil", "spctl", "tccutil", "security",
    "crontab", "su", "doas", "sudo", "scutil", "dscl", "hdiutil", "mount", "umount", "trash",
    "format", "del", "erase", "rd", "taskkill", "reg", "sc", "diskpart",
}
INLINE_CODE_FLAGS = {"python": {"-c"}, "python3": {"-c"}, "node": {"-e", "--eval"},
                     "perl": {"-e"}, "ruby": {"-e"}, "osascript": {"-e"}, "php": {"-r"},
                     "sh": {"-c"}, "bash": {"-c"}, "zsh": {"-c"}, "fish": {"-c"},
                     "powershell": {"-command", "-c"}, "pwsh": {"-command", "-c"}, "cmd": {"/c"}}
GIT_RISKY = {"push", "reset", "clean", "checkout", "rebase", "rm", "restore", "switch", "stash", "merge",
             "pull", "gc", "prune", "filter-branch", "revert", "tag", "branch", "fetch", "clone", "config",
             "remote", "am", "apply", "submodule", "worktree", "update-ref", "reflog", "cherry-pick", "bisect"}
_INTERPRETERS = {".py": [sys.executable or "python3"], ".sh": ["/bin/sh"], ".zsh": ["/bin/zsh"],
                 ".js": ["node"], ".mjs": ["node"], ".rb": ["ruby"], ".pl": ["perl"]}


def _runner(ctx: ToolContext) -> ProcessRunner:
    return ctx.service("runner", lambda: ProcessRunner(ctx.platform))


def _argv(raw) -> list[str]:
    if isinstance(raw, str):
        try:
            return shlex.split(raw)
        except ValueError as exc:
            raise ToolError(f"Befehl nicht lesbar: {exc}") from exc
    if isinstance(raw, (list, tuple)) and raw and all(isinstance(x, (str, int, float)) for x in raw):
        return [str(x) for x in raw]
    raise ToolError("argv muss eine nicht-leere Liste von Strings sein, z. B. [\"ls\", \"-la\"]")


def _cwd(ctx: ToolContext, a: dict) -> Path:
    if not a.get("cwd"):
        return ctx.workspace
    p = resolve(ctx, str(a["cwd"]), must_exist=True)
    if not p.is_dir():
        raise ToolError(f"Arbeitsverzeichnis ist kein Ordner: {p}")
    return p


def _timeout(ctx: ToolContext, a: dict) -> float:
    t = float(a.get("timeout_s") or ctx.cfg.tools.shell_timeout_s)
    return max(0.5, min(t, ctx.cfg.tools.max_timeout_s))


def _pretty(argv: list[str]) -> str:
    return " ".join(shlex.quote(x) for x in argv)


def _path_tokens(argv: list[str], cwd: Path) -> list[Path]:
    """Alle Argumente, die Pfade sein könnten (auch relativ, auch in --opt=wert), aufgelöst gegen cwd."""
    out = []
    for tok in argv[1:]:
        val = tok.split("=", 1)[1] if tok.startswith("-") and "=" in tok else tok
        if not val or (val.startswith("-") and "=" not in tok) or "://" in val:
            continue
        p = Path(val).expanduser()
        p = (p if p.is_absolute() else cwd / p).resolve(strict=False)
        if p.exists() or "/" in val or val.startswith(("~", ".")):
            out.append(p)
    return out


def _touches_protected(ctx: ToolContext, argv: list[str], cwd: Path) -> Optional[Path]:
    return next((p for p in _path_tokens(argv, cwd) if is_protected(ctx, p)), None)


def _outside_paths(ctx: ToolContext, argv: list[str], cwd: Path) -> list[Path]:
    """Pfadargumente außerhalb des Arbeitsordners (z. B. cp a /Users/x/…, curl -o /abs, tar -C /)."""
    return [p for p in _path_tokens(argv, cwd) if not in_workspace(ctx, p)]


def _unwrap(argv: list[str]) -> list[str]:
    """env/xargs/nohup/nice/time … umschließen den eigentlichen Befehl — den bewerten."""
    seen = 0
    while argv and Path(argv[0]).name.lower() in WRAPPERS and seen < 5:
        seen += 1
        rest = argv[1:]
        while rest and (rest[0].startswith("-") or ("=" in rest[0] and not rest[0].startswith("/"))
                        or rest[0].replace(".", "", 1).isdigit()):
            rest = rest[1:]                      # Optionen, VAR=wert, Zahlen (nice -n 5, timeout 10)
        if not rest:
            return argv
        argv = rest
    return argv


def _norm_name(argv0: str) -> str:
    name = Path(argv0).name.lower().removesuffix(".exe")
    return re.sub(r"[\d.]+$", "", name) or name        # python3.12 → python, node22 → node


def _git_sub(argv: list[str]) -> tuple[str, bool]:
    """(Unterbefehl, hat -c/--config-env). Überspringt globale Optionen samt Wert (-C dir, --git-dir=…)."""
    i, cfg = 1, False
    while i < len(argv):
        a = argv[i]
        if a in ("-c", "--config-env") or a.startswith(("--config-env=", "-c")) and a != "-C":
            cfg = True
            i += 2 if a in ("-c", "--config-env") else 1
            continue
        if a in ("-C", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--super-prefix"):
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        return a, cfg
    return "", cfg


def classify_argv(ctx: ToolContext, argv: list[str], cwd: Path, env: Optional[dict] = None) -> Assessment:
    """Risikoeinstufung eines Befehls (ohne Shell). Reihenfolge: gesperrt → Umgebungsvariablen →
    gefährlich → Code per Option → git → Pfade außerhalb → reiner Leser → außerhalb/Fremdinhalt → erlaubt."""
    shown = _pretty(argv)
    if (bad := _touches_protected(ctx, argv, cwd)):
        return Assessment(DENY, f"Geschützter Pfad: {bad}")
    risky_env = [k for k in (env or {}) if str(k).startswith(_ENV_RISKY)]
    if risky_env:
        return Assessment(CONFIRM, f"Befehl mit verhaltensändernden Variablen ({', '.join(risky_env)}): {shown}")
    if Path(argv[0]).name.lower() == "sudo":
        return Assessment(CONFIRM, f"ADMINISTRATOR-BEFEHL: {shown}")
    inner = _unwrap(argv)
    if inner is not argv and Path(inner[0]).name.lower() == "sudo":
        return Assessment(CONFIRM, f"ADMINISTRATOR-BEFEHL: {shown}")
    name = _norm_name(inner[0])
    if name in DANGEROUS or name.startswith("mkfs"):
        return Assessment(CONFIRM, f"Riskanter Befehl: {shown}")
    flags = INLINE_CODE_FLAGS.get(name, set()) | ({"-p", "--print"} if name == "node" else set())
    if any(a.lower() in flags or a.lower().startswith(tuple(f + "=" for f in flags)) for a in inner[1:]):
        return Assessment(CONFIRM, f"Beliebiger Code per Befehlszeile: {shown}")
    if name in ("sed", "gsed") and any(re.match(r"^-[a-zA-Z]*i|^--in-place", a) for a in inner[1:]):
        return Assessment(CONFIRM, f"Datei wird verändert: {shown}")
    # awk/sed-Programme können Befehle starten oder Dateien schreiben — wie python -c behandeln
    if name in ("awk", "gawk", "nawk", "mawk") and any(
            re.search(r"system\s*\(|\|\s*getline|getline\s*<|print[^;}]*[>|]|printf[^;}]*[>|]", a) for a in inner[1:]):
        return Assessment(CONFIRM, f"awk-Programm startet Befehle oder schreibt Dateien: {shown}")
    if name in ("sed", "gsed") and any(re.search(r"(^|[;{}\s\d/$])[wWe](\s|$)|/[wWe]\s", a) for a in inner[1:]):
        return Assessment(CONFIRM, f"sed-Programm schreibt Dateien oder startet Befehle: {shown}")
    if name == "find" and any(x in inner for x in ("-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprintf", "-fls")):
        return Assessment(CONFIRM, f"find mit Aktion: {shown}")
    if name == "git":
        sub, cfg = _git_sub(inner)
        if cfg:
            return Assessment(CONFIRM, f"Git mit Konfiguration per Befehlszeile (-c): {shown}")
        if sub in GIT_RISKY:
            return Assessment(CONFIRM, f"Git verändert Verlauf/Stand/Netz: {shown}")
        if sub in GIT_READONLY:
            return Assessment(ALLOW)
    pure_reader = name in READONLY and inner is argv
    if not pure_reader and (outside := _outside_paths(ctx, inner, cwd)):
        return Assessment(CONFIRM, f"Befehl berührt Pfade außerhalb des Arbeitsordners ({outside[0]}): {shown}")
    if pure_reader:
        return Assessment(ALLOW)
    if not in_workspace(ctx, cwd):
        return Assessment(CONFIRM, f"Befehl außerhalb des Arbeitsordners ({cwd}): {shown}")
    if ctx.tainted:
        return Assessment(CONFIRM, f"Befehl nach Lesen fremder Inhalte: {shown}")
    return Assessment(ALLOW)


# ------------------------------------------------------------- run_command

def _admin_wrap(ctx: ToolContext, argv: list[str]) -> list[str]:
    """sudo ⇒ plattformeigener Administratordialog (macOS: osascript mit Passwortabfrage)."""
    if Path(argv[0]).name == "sudo" and ctx.platform.name == "macos":
        inner = _pretty([a for a in argv[1:] if a not in ("-n", "-S")])
        script = 'on run argv\n do shell script (item 1 of argv) with administrator privileges\nend run'
        return ["osascript", "-e", script, inner]
    return argv


def run_command(ctx: ToolContext, a: dict) -> ToolResult:
    argv = _argv(a.get("argv") or a.get("command"))
    cwd = _cwd(ctx, a)
    env = a.get("env") if isinstance(a.get("env"), dict) else None
    argv = _admin_wrap(ctx, argv)
    try:
        if a.get("background"):
            job = _runner(ctx).start_background(argv, cwd, env=env, timeout_s=ctx.cfg.tools.max_timeout_s,
                                                max_chars=ctx.cfg.tools.max_output_chars)
            if isinstance(job, RunResult):          # Start fehlgeschlagen
                return ToolResult(False, job.render())
            return ToolResult(True, f"Gestartet als Job {job.id} (Befehl: {_pretty(argv)}). "
                                    f"Status: job_status, Abbruch: cancel_job.", {"job_id": job.id})
        res = _runner(ctx).run(argv, cwd, env=env, timeout_s=_timeout(ctx, a),
                               max_chars=ctx.cfg.tools.max_output_chars, cancel=ctx.cancel)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    ctx.tainted = True      # Ausgabe ist fremder Inhalt
    body = res.render()
    return ToolResult(res.ok, f"$ {_pretty(argv)}  (in {cwd})\n" + _wrap_output(body),
                      {"exit_code": res.exit_code, "timed_out": res.timed_out})


def _wrap_output(rendered: str) -> str:
    """Statuszeile bleibt außerhalb der Fremdinhalt-Markierung (Ergebnis ist maßgeblich)."""
    first, _, rest = rendered.partition("\n")
    return first + ("\n" + wrap_untrusted(rest) if rest else "")


def _assess_run_command(ctx: ToolContext, a: dict) -> Assessment:
    try:
        argv = _argv(a.get("argv") or a.get("command"))
        cwd = _cwd(ctx, a)
    except ToolError:
        return Assessment(ALLOW)      # Handler meldet den Fehler
    return classify_argv(ctx, argv, cwd, a.get("env") if isinstance(a.get("env"), dict) else None)


# --------------------------------------------------------------- run_shell

_SHELL_OPS = set("&;<>()$`\\\n{}*?~!")


def _simple_pipeline(command: str) -> Optional[list[list[str]]]:
    """Zerlegt `a | b | c`, solange keine weitere Shell-Syntax vorkommt."""
    if any(c in _SHELL_OPS for c in re.sub(r"'[^']*'", "", command)):
        return None
    try:
        lex = shlex.shlex(command, posix=True, punctuation_chars="|")
        lex.whitespace_split = True
        toks = list(lex)
    except ValueError:
        return None
    segs, cur = [], []
    for t in toks:
        if t == "|":
            segs.append(cur); cur = []
        elif set(t) <= set("|"):
            return None
        else:
            cur.append(t)
    segs.append(cur)
    return segs if all(segs) else None


def run_shell(ctx: ToolContext, a: dict) -> ToolResult:
    command = str(a.get("command", "")).strip()
    if not command:
        raise ToolError("command fehlt")
    cwd = _cwd(ctx, a)
    argv = ctx.platform.shell_argv(command)
    res = _runner(ctx).run(argv, cwd, timeout_s=_timeout(ctx, a),
                           max_chars=ctx.cfg.tools.max_output_chars, cancel=ctx.cancel)
    ctx.tainted = True
    return ToolResult(res.ok, f"[{ctx.platform.shell_label}] {command}  (in {cwd})\n" + _wrap_output(res.render()),
                      {"exit_code": res.exit_code, "timed_out": res.timed_out})


def _shell_mentions_protected(ctx: ToolContext, command: str, cwd: Path) -> Optional[Path]:
    """Shell-Text auf geschützte Pfade prüfen (~, $HOME, relative Namen), Groß-/Kleinschreibung egal."""
    home = str(Path.home())
    text = re.sub(r"\$\{?HOME\}?|(?<![\w/])~(?=/|\b)", home, command)
    try:
        toks = shlex.split(text)
    except ValueError:
        toks = text.split()
    for t in toks:
        for part in re.split(r"[|;&<>()`]+", t):
            part = part.strip("'\"")
            if not part or part.startswith("-") or "://" in part:
                continue
            p = Path(part).expanduser()
            p = (p if p.is_absolute() else cwd / p).resolve(strict=False)
            if is_protected(ctx, p):
                return p
    low = text.casefold()
    for b in ctx.platform.protected_paths():
        if str(b).casefold() in low:
            return b
    return None


def _assess_run_shell(ctx: ToolContext, a: dict) -> Assessment:
    command = str(a.get("command", "")).strip()
    try:
        cwd = _cwd(ctx, a)
    except ToolError:
        return Assessment(ALLOW)
    if (bad := _shell_mentions_protected(ctx, command, cwd)):
        return Assessment(DENY, f"Geschützter Pfad: {bad}")
    segs = _simple_pipeline(command)
    if segs is not None and not ctx.tainted:
        verdicts = [classify_argv(ctx, s, cwd) for s in segs]
        if any(v.level == DENY for v in verdicts):
            return next(v for v in verdicts if v.level == DENY)
        if all(v.level == ALLOW for v in verdicts) and all(
                Path(s[0]).name.lower() in READONLY or s[0] == "git" for s in segs):
            return Assessment(ALLOW)
    return Assessment(CONFIRM, f"SHELL-BEFEHL ({'zsh' if sys.platform == 'darwin' else 'sh'}): {command}")


# -------------------------------------------------------------- run_script

def run_script(ctx: ToolContext, a: dict) -> ToolResult:
    script = resolve(ctx, str(a.get("path", "")), must_exist=True)
    if not script.is_file():
        raise ToolError(f"Keine Datei: {script}")
    argv = _script_argv(script, a)
    cwd = _cwd(ctx, a) if a.get("cwd") else script.parent
    res = _runner(ctx).run(argv, cwd, env=a.get("env") if isinstance(a.get("env"), dict) else None,
                           timeout_s=_timeout(ctx, a), max_chars=ctx.cfg.tools.max_output_chars,
                           cancel=ctx.cancel)
    ctx.tainted = True
    return ToolResult(res.ok, f"$ {_pretty(argv)}  (in {cwd})\n" + _wrap_output(res.render()),
                      {"exit_code": res.exit_code, "timed_out": res.timed_out})


def _script_argv(script: Path, a: dict) -> list[str]:
    interp = _INTERPRETERS.get(script.suffix.lower())
    if interp is None:
        if os.access(script, os.X_OK):
            interp = []
        else:
            raise ToolError(f"Unbekannter Skripttyp {script.suffix or '(ohne Endung)'} und nicht ausführbar")
    extra = a.get("args") or []
    if not isinstance(extra, list):
        raise ToolError("args muss eine Liste sein")
    return [*interp, str(script), *[str(x) for x in extra]]


def _assess_run_script(ctx: ToolContext, a: dict) -> Assessment:
    try:
        script = resolve(ctx, str(a.get("path", "")))
        cwd = _cwd(ctx, a) if a.get("cwd") else script.parent
        argv = _script_argv(script, a)
    except ToolError:
        return Assessment(ALLOW)
    env: dict = a["env"] if isinstance(a.get("env"), dict) else {}
    if (risky := [k for k in env if str(k).startswith(_ENV_RISKY)]):
        return Assessment(CONFIRM, f"Skript mit verhaltensändernden Variablen ({', '.join(risky)}): {_pretty(argv)}")
    if not in_workspace(ctx, script) or not in_workspace(ctx, cwd):
        return Assessment(CONFIRM, f"Skript außerhalb des Arbeitsordners ausführen: {_pretty(argv)}")
    if ctx.tainted:
        return Assessment(CONFIRM, f"Skript ausführen nach Lesen fremder Inhalte: {_pretty(argv)}")
    return Assessment(ALLOW)


# ------------------------------------------------------------------- Jobs

def job_status(ctx: ToolContext, a: dict) -> ToolResult:
    snap = _runner(ctx).snapshot(str(a.get("job_id", "")))
    if snap is None:
        raise ToolError(f"Unbekannter Job: {a.get('job_id')}")
    job, res = snap
    state = "läuft" if job.running else f"beendet (Rückgabecode {res.exit_code})"
    ctx.tainted = True
    return ToolResult(True, f"Job {job.id} {state}: {_pretty(job.argv)}\n" + wrap_untrusted(res.render()))


def cancel_job(ctx: ToolContext, a: dict) -> ToolResult:
    jid = a.get("job_id")
    r = _runner(ctx)
    if not jid:
        n = r.cancel_all()
        return ToolResult(True, f"{n} laufende Job(s) abgebrochen")
    if r.cancel(str(jid)):
        return ToolResult(True, f"Job {jid} abgebrochen")
    raise ToolError(f"Job {jid} läuft nicht (mehr) oder ist unbekannt")


def list_processes(ctx: ToolContext, a: dict) -> ToolResult:
    needle = str(a.get("filter") or "").lower()
    rows = []
    for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
        n = p.info["name"] or ""
        if needle and needle not in n.lower():
            continue
        rows.append((p.info["memory_percent"] or 0, f"{p.info['pid']:>6}  {n[:40]}  RAM {p.info['memory_percent'] or 0:.1f}%"))
    rows.sort(reverse=True)
    return ToolResult(True, "PID  Name\n" + "\n".join(r for _, r in rows[:15]) if rows else "Keine Treffer")


def kill_process(ctx: ToolContext, a: dict) -> ToolResult:
    pid = int(a.get("pid", 0))
    if pid in (os.getpid(), 0, 1):
        raise ToolError("Diesen Prozess beende ich nicht")
    try:
        p = psutil.Process(pid)
        name = p.name()
        p.terminate()
        try:
            p.wait(3)
        except psutil.TimeoutExpired:
            p.kill(); p.wait(3)
    except psutil.NoSuchProcess:
        raise ToolError(f"Prozess {pid} existiert nicht") from None
    except psutil.AccessDenied:
        raise ToolError(f"Keine Berechtigung, Prozess {pid} zu beenden") from None
    return ToolResult(True, f"Prozess {pid} ({name}) beendet")


def _assess_kill(ctx: ToolContext, a: dict) -> Assessment:
    try:
        pid = int(a.get("pid", 0)); name = psutil.Process(pid).name()
    except Exception:  # noqa: BLE001
        return Assessment(ALLOW)
    return Assessment(CONFIRM, f"PROZESS BEENDEN: PID {pid} ({name})")


def tools() -> list[Tool]:
    S = {"type": "string"}
    return [
        Tool("run_command", "terminal",
             "Führt ein Programm OHNE Shell aus. argv = Liste, z. B. [\"ls\",\"-la\"]. Liefert Rückgabecode, Ausgabe, Fehlerausgabe.",
             {"argv": {"type": "array", "items": S, "description": "Programm und Argumente"},
              "cwd": {**S, "description": "Arbeitsverzeichnis (Standard: Arbeitsordner)"},
              "env": {"type": "object", "description": "Zusätzliche Umgebungsvariablen"},
              "timeout_s": {"type": "number"}, "background": {"type": "boolean"}},
             ("argv",), run_command, _assess_run_command,
             announce=lambda a: f"Ich führe aus: {_pretty(_safe_argv(a))}, Sir."),
        Tool("run_shell", "terminal",
             "NUR wenn Shell-Syntax nötig ist (Pipes, Umleitung, &&). Sonst run_command.",
             {"command": S, "cwd": S, "timeout_s": {"type": "number"}}, ("command",),
             run_shell, _assess_run_shell, announce=lambda a: "Ich führe den Shell-Befehl aus, Sir."),
        Tool("run_script", "terminal", "Führt eine Skriptdatei aus (.py .sh .js …).",
             {"path": S, "args": {"type": "array", "items": S}, "cwd": S,
              "env": {"type": "object"}, "timeout_s": {"type": "number"}}, ("path",),
             run_script, _assess_run_script, announce=lambda a: "Ich führe das Skript aus, Sir."),
        Tool("job_status", "terminal", "Status und bisherige Ausgabe eines Hintergrundjobs.",
             {"job_id": S}, ("job_id",), job_status, untrusted=True),
        Tool("cancel_job", "terminal", "Bricht einen laufenden Job ab (ohne job_id: alle).",
             {"job_id": S}, (), cancel_job, announce=lambda a: "Ich breche ab, Sir."),
        Tool("list_processes", "terminal", "Zeigt laufende Prozesse (optional gefiltert).",
             {"filter": S}, (), list_processes),
        Tool("kill_process", "terminal", "Beendet einen Prozess per PID (erfordert Bestätigung).",
             {"pid": {"type": "integer"}}, ("pid",), kill_process, _assess_kill),
    ]


def _safe_argv(a: dict) -> list[str]:
    try:
        return _argv(a.get("argv") or a.get("command"))
    except ToolError:
        return ["?"]
