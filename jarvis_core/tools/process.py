"""Prozessausführung: Argumentlisten, Zeitlimit, begrenzte Ausgabe, Abbruch.

Normale Aufrufe laufen ohne Shell (shell=False). Shell-Syntax gibt es nur über
den ausdrücklich getrennten run_shell-Pfad (Adapter.shell_argv).
"""
from __future__ import annotations

import itertools
import logging
import os
import re
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..platform import PlatformAdapter

log = logging.getLogger("jarvis.process")

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_BLOCKED_ENV_PREFIXES = ("DYLD_", "LD_", "JARVIS_")
_SECRET_HINTS = ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "APIKEY", "CREDENTIAL")


class _Capture:
    """Behält Anfang und Ende der Ausgabe, verwirft die Mitte (Speicher bleibt begrenzt)."""

    def __init__(self, limit: int):
        self.head_cap = int(limit * 0.6)
        self.tail_cap = limit - self.head_cap
        self.head: list[str] = []
        self.head_len = 0
        self.tail: deque[str] = deque()
        self.tail_len = 0
        self.total = 0
        self._lock = threading.Lock()

    def feed(self, s: str) -> None:
        with self._lock:
            self.total += len(s)
            if self.head_len < self.head_cap:
                take = s[: self.head_cap - self.head_len]
                self.head.append(take)
                self.head_len += len(take)
                s = s[len(take):]
            if s:
                self.tail.append(s)
                self.tail_len += len(s)
                while self.tail_len - len(self.tail[0]) >= self.tail_cap:
                    self.tail_len -= len(self.tail.popleft())

    def text(self) -> tuple[str, bool]:
        with self._lock:
            head = "".join(self.head)
            tail = "".join(self.tail)
            if len(tail) > self.tail_cap:
                tail = tail[-self.tail_cap:]
            kept = len(head) + len(tail)
            if kept >= self.total:
                return head + tail, False
            dropped = self.total - kept
            return f"{head}\n… [{dropped} Zeichen ausgelassen] …\n{tail}", True


@dataclass
class RunResult:
    ok: bool
    exit_code: Optional[int]
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False
    cancelled: bool = False
    truncated: bool = False
    error: Optional[str] = None      # z. B. Programm nicht gefunden
    job_id: Optional[str] = None
    cwd: str = ""

    def render(self) -> str:
        if self.error:
            return f"FEHLGESCHLAGEN: {self.error}"
        head = []
        if self.timed_out:
            head.append(f"FEHLGESCHLAGEN: Zeitlimit überschritten, Prozess beendet ({self.duration_s:.1f} s)")
        elif self.cancelled:
            head.append("ABGEBROCHEN: Prozess wurde beendet")
        elif self.ok:
            head.append(f"ERFOLG: Rückgabecode 0 ({self.duration_s:.1f} s)")
        else:
            head.append(f"FEHLGESCHLAGEN: Rückgabecode {self.exit_code} ({self.duration_s:.1f} s)")
        if self.stdout.strip():
            head.append("Ausgabe:\n" + self.stdout.rstrip())
        if self.stderr.strip():
            head.append("Fehlerausgabe:\n" + self.stderr.rstrip())
        if not self.stdout.strip() and not self.stderr.strip():
            head.append("(keine Ausgabe)")
        return "\n".join(head)


@dataclass
class Job:
    id: str
    argv: list[str]
    cwd: str
    proc: subprocess.Popen
    started: float
    out: _Capture
    err: _Capture
    timeout_s: float
    done: threading.Event = field(default_factory=threading.Event)
    timed_out: bool = False
    cancelled: bool = False
    threads: list = field(default_factory=list)

    @property
    def running(self) -> bool:
        return self.proc.poll() is None


def clean_env(extra: Optional[dict] = None) -> dict[str, str]:
    """Umgebung für Kindprozesse: ohne Jarvis-Zugangsdaten, ohne Lader-Variablen."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(_BLOCKED_ENV_PREFIXES)
           and not any(h in k.upper() for h in _SECRET_HINTS)}
    for k, v in (extra or {}).items():
        if not _ENV_NAME.match(str(k)):
            raise ValueError(f"Ungültiger Variablenname: {k!r}")
        if str(k).startswith(_BLOCKED_ENV_PREFIXES):
            raise ValueError(f"Variable {k} darf nicht gesetzt werden")
        env[str(k)] = str(v)
    return env


class ProcessRunner:
    def __init__(self, platform: PlatformAdapter):
        self.platform = platform
        self.jobs: dict[str, Job] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    # ----------------------------------------------------------- Start

    def _start(self, argv: list[str], cwd: Path, env: Optional[dict],
               timeout_s: float, max_chars: int) -> Job | RunResult:
        t0 = time.monotonic()
        try:
            proc = subprocess.Popen(
                argv, cwd=str(cwd), env=clean_env(env), shell=False,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                **self.platform.popen_kwargs())
        except FileNotFoundError:
            return RunResult(False, None, "", "", 0.0, cwd=str(cwd),
                             error=f"Programm nicht gefunden: {argv[0]}")
        except NotADirectoryError:
            return RunResult(False, None, "", "", 0.0, cwd=str(cwd),
                             error=f"Arbeitsverzeichnis ist kein Ordner: {cwd}")
        except PermissionError as exc:
            return RunResult(False, None, "", "", 0.0, cwd=str(cwd),
                             error=f"Keine Berechtigung zum Starten von {argv[0]}: {exc}")
        except OSError as exc:
            return RunResult(False, None, "", "", 0.0, cwd=str(cwd),
                             error=f"Start fehlgeschlagen: {exc}")
        job = Job(id=f"j{next(self._ids)}", argv=argv, cwd=str(cwd), proc=proc, started=t0,
                  out=_Capture(max_chars), err=_Capture(max_chars), timeout_s=timeout_s)
        for stream, cap in ((proc.stdout, job.out), (proc.stderr, job.err)):
            th = threading.Thread(target=self._pump, args=(stream, cap), daemon=True)
            th.start()
            job.threads.append(th)
        threading.Thread(target=self._watch, args=(job,), daemon=True).start()
        with self._lock:
            self.jobs[job.id] = job
        return job

    @staticmethod
    def _pump(stream, cap: _Capture) -> None:
        try:
            for chunk in iter(lambda: stream.read1(4096) if hasattr(stream, "read1") else stream.read(4096), b""):
                cap.feed(chunk.decode("utf-8", errors="replace"))
        except (ValueError, OSError):
            pass

    def _watch(self, job: Job) -> None:
        """Erzwingt das Zeitlimit unabhängig vom aufrufenden Thread."""
        deadline = job.started + job.timeout_s
        while job.proc.poll() is None:
            if time.monotonic() > deadline:
                job.timed_out = True
                self.kill(job)
                break
            time.sleep(0.05)
        job.proc.wait()
        for th in job.threads:
            th.join(timeout=2)
        job.done.set()

    # ------------------------------------------------------------- API

    def run(self, argv: list[str], cwd: Path, *, env: Optional[dict] = None,
            timeout_s: float = 60.0, max_chars: int = 6000,
            cancel: Optional[threading.Event] = None) -> RunResult:
        """Startet und wartet. Abbruch über `cancel` beendet den Prozess."""
        started = self._start(argv, cwd, env, timeout_s, max_chars)
        if isinstance(started, RunResult):
            return started
        job = started
        while not job.done.wait(0.1):
            if cancel is not None and cancel.is_set():
                job.cancelled = True
                self.kill(job)
        return self._result(job)

    def start_background(self, argv: list[str], cwd: Path, *, env: Optional[dict] = None,
                         timeout_s: float = 600.0, max_chars: int = 6000) -> Job | RunResult:
        return self._start(argv, cwd, env, timeout_s, max_chars)

    def _result(self, job: Job) -> RunResult:
        out, t1 = job.out.text()
        err, t2 = job.err.text()
        code = job.proc.returncode
        return RunResult(
            ok=(code == 0 and not job.timed_out and not job.cancelled),
            exit_code=code, stdout=out, stderr=err,
            duration_s=time.monotonic() - job.started, timed_out=job.timed_out,
            cancelled=job.cancelled, truncated=t1 or t2, job_id=job.id, cwd=job.cwd)

    def kill(self, job: Job) -> None:
        if job.proc.poll() is not None:
            return
        self.platform.terminate(job.proc, force=False)
        try:
            job.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.platform.terminate(job.proc, force=True)

    def cancel(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if job is None or not job.running:
            return False
        job.cancelled = True
        self.kill(job)
        return True

    def cancel_all(self) -> int:
        n = 0
        for job in list(self.jobs.values()):
            if job.running:
                job.cancelled = True
                self.kill(job)
                n += 1
        return n

    def snapshot(self, job_id: str) -> Optional[tuple[Job, RunResult]]:
        job = self.jobs.get(job_id)
        return (job, self._result_partial(job)) if job else None

    def _result_partial(self, job: Job) -> RunResult:
        out, t1 = job.out.text()
        err, t2 = job.err.text()
        code = job.proc.poll()
        return RunResult(ok=(code == 0), exit_code=code, stdout=out, stderr=err,
                         duration_s=time.monotonic() - job.started, timed_out=job.timed_out,
                         cancelled=job.cancelled, truncated=t1 or t2, job_id=job.id, cwd=job.cwd)
