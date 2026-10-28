"""Prozessausführung: Argumentlisten, Fehlerfall, Timeout, Abbruch, Ausgabelimit."""
import os
import threading
import time

from jarvis_core.platform import current
from jarvis_core.tools.process import ProcessRunner, clean_env
import pytest


@pytest.fixture
def runner():
    return ProcessRunner(current())


def test_success_and_output(runner, tmp_path):
    r = runner.run(["echo", "hallo welt"], tmp_path)
    assert r.ok and r.exit_code == 0 and r.stdout.strip() == "hallo welt"


def test_exit_code_and_stderr(runner, tmp_path):
    r = runner.run(["sh", "-c", "echo boom >&2; exit 3"], tmp_path)
    assert not r.ok and r.exit_code == 3 and "boom" in r.stderr
    assert "FEHLGESCHLAGEN" in r.render() and "ERFOLG" not in r.render()


def test_timeout_kills_process_tree(runner, tmp_path):
    t = time.time()
    r = runner.run(["sh", "-c", "sleep 30 & sleep 30"], tmp_path, timeout_s=0.6)
    assert r.timed_out and not r.ok and time.time() - t < 6


def test_missing_program(runner, tmp_path):
    r = runner.run(["gibt-es-nicht-xyz"], tmp_path)
    assert not r.ok and "nicht gefunden" in r.error


def test_output_is_bounded(runner, tmp_path):
    r = runner.run(["sh", "-c", "seq 1 300000"], tmp_path, max_chars=300)
    assert r.truncated and len(r.stdout) < 500 and "ausgelassen" in r.stdout


def test_cancel(runner, tmp_path):
    ev = threading.Event()
    threading.Timer(0.4, ev.set).start()
    r = runner.run(["sleep", "30"], tmp_path, cancel=ev)
    assert r.cancelled and not r.ok


def test_cwd_with_spaces_and_no_shell(runner, tmp_path):
    d = tmp_path / "Mein Ordner"; d.mkdir()
    (d / "a b.txt").write_text("x")
    r = runner.run(["ls", "a b.txt"], d)
    assert r.ok and "a b.txt" in r.stdout
    r = runner.run(["echo", "$HOME; rm -rf /"], d)       # keine Shell ⇒ wörtlich
    assert r.stdout.strip() == "$HOME; rm -rf /"


def test_env_controlled_and_secrets_stripped(monkeypatch):
    monkeypatch.setenv("JARVIS_HA_TOKEN", "geheim")
    monkeypatch.setenv("MY_API_KEY", "geheim")
    env = clean_env({"FOO": "bar"})
    assert env["FOO"] == "bar" and "JARVIS_HA_TOKEN" not in env and "MY_API_KEY" not in env
    for bad in ({"DYLD_INSERT_LIBRARIES": "x"}, {"1bad": "x"}):
        with pytest.raises(ValueError):
            clean_env(bad)


def test_background_job_cancel(runner, tmp_path):
    job = runner.start_background(["sleep", "30"], tmp_path)
    assert job.running and runner.cancel(job.id)
    job.done.wait(5)
    assert not job.running
