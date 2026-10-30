"""Regressionstests für die Funde des Code-Reviews (2026-10-28)."""
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from jarvis_core.memory.when import parse_when
from tests.tools_helpers import call, run, runtime

BERLIN = ZoneInfo("Europe/Berlin")


def A(rt, n, **a):
    return rt.registry.assess(rt.ctx, n, a).level


@pytest.mark.parametrize("argv", [
    ["awk", 'BEGIN{system("touch x")}'], ["sed", "-n", "w /tmp/x", "f"], ["sed", "-Ei", "s/a/b/", "f"],
    ["sed", "--in-place", "s/a/b/", "f"], ["env", "rm", "-rf", "dir"], ["xargs", "rm"], ["nice", "-n", "5", "rm", "x"],
    ["python3.12", "-c", "print(1)"], ["node", "-p", "1"], ["cp", "a", "/Users/x/Documents/a"],
    ["touch", "/etc/foo"], ["curl", "-o", "/tmp/out", "https://example.org"], ["tar", "-xf", "a.tar", "-C", "/"],
    ["git", "-C", ".", "push"], ["git", "-c", "alias.x=!touch p", "x"], ["git", "--git-dir=.git", "push"],
    ["git", "fetch"], ["git", "config", "core.pager", "x"], ["find", ".", "-okdir", "rm", "{}", ";"],
])
def test_dangerous_commands_need_confirmation(tmp_path, argv):
    rt = runtime(tmp_path)
    assert A(rt, "run_command", argv=argv) == "confirm", argv


def test_ordinary_commands_still_run_directly(tmp_path):
    rt = runtime(tmp_path)
    for argv in (["awk", "{print $1}", "f.txt"], ["sed", "-n", "1,5p", "f.txt"], ["ls", "-la"], ["git", "status"], ["git", "-C", ".", "log"], ["mkdir", "neu"],
                 ["python3", "--version"], ["cp", "a.txt", "b.txt"], ["cat", "/etc/hosts"]):
        assert A(rt, "run_command", argv=argv) == "allow", argv


def test_risky_environment_variables_need_confirmation(tmp_path):
    rt = runtime(tmp_path)
    for env in ({"GIT_CONFIG_COUNT": "1"}, {"NODE_OPTIONS": "--require x"}, {"PATH": "/tmp"}, {"BASH_ENV": "x"}):
        assert A(rt, "run_command", argv=["git", "status"], env=env) == "confirm", env
    assert A(rt, "run_command", argv=["ls"], env={"MEIN_WERT": "1"}) == "allow"
    assert A(rt, "run_script", path="s.py", env={"PYTHONPATH": "/x"}) == "confirm"


def test_protected_paths_are_case_insensitive_and_relative(tmp_path):
    rt = runtime(tmp_path)
    from jarvis_core.config import repo_root
    assert not rt.registry.execute(rt.ctx, "read_file", {"path": "~/.SSH/known_hosts"}, 100).ok
    assert A(rt, "run_command", argv=["cat", ".env"], cwd=str(repo_root())) == "deny"
    assert A(rt, "run_command", argv=["cat", "~/.SSH/id_rsa"]) == "deny"
    for cmd in ("cat ~/.ssh/known_hosts", "cat $HOME/.ssh/id_rsa", "grep x ${HOME}/.SSH/config | head"):
        assert A(rt, "run_shell", command=cmd) == "deny", cmd


def test_find_files_does_not_enter_protected_dirs(tmp_path):
    rt = runtime(tmp_path)                               # Jarvis-Datenordner (geschützt) liegt unter tmp_path/data
    (rt.ctx.data_dir / "geheim.txt").write_text("x")
    (rt.ctx.workspace / "offen.txt").write_text("x")
    r = rt.registry.execute(rt.ctx, "find_files", {"root": str(tmp_path), "name": "*.txt"}, 4000)
    assert "offen.txt" in r.text and "geheim.txt" not in r.text


def test_failure_is_not_hidden_by_success_on_another_target(tmp_path):
    rt = runtime(tmp_path, {"tool_calls": [call("run_command", argv=["ls", "/gibt/es/nicht"])]},
                 {"tool_calls": [call("run_command", argv=["ls"])]}, {"content": "Build erledigt."})
    done = run(rt, "Führe im Terminal ls aus")[-1]
    assert not done["ok"] and "Build erledigt" not in done["text"] and "Rückgabecode" in done["text"]


def test_retry_with_same_target_resolves_failure(tmp_path):
    rt = runtime(tmp_path, {"tool_calls": [call("write_file", path="a.txt", content="x")]},
                 {"tool_calls": [call("write_file", path="a.txt", content="y", mode="overwrite")]},
                 {"content": "Datei aktualisiert."})
    (rt.ctx.workspace / "a.txt").write_text("alt")
    done = run(rt, "Schreibe die Datei a.txt")[-1]
    assert done["ok"] and (rt.ctx.workspace / "a.txt").read_text() == "y"


def test_affirmation_only_after_an_offer(tmp_path):
    from jarvis_app.bridge import Bridge
    from tests.test_bridge import FakeChat, FakeHub, FakeWindow
    from tests.tools_helpers import CFG
    b = Bridge(CFG, chat=FakeChat(), hub=FakeHub(), layout_path=tmp_path / "l.json"); b._window = FakeWindow()
    b._chat.history = [{"role": "user", "content": "Erinnere mich morgen um 9 an den Zahnarzt"},
                       {"role": "assistant", "content": "Erinnerung gesetzt, Sir."}]
    assert not b._offer_pending()                 # erledigt ⇒ „ja“ wiederholt nichts
    b._chat.history[-1]["content"] = "Soll ich das Wetter für Sie abrufen?"
    assert b._offer_pending()


def test_when_evening_and_relative_days_with_time():
    now = datetime(2026, 10, 28, 15, 0, tzinfo=BERLIN)
    assert parse_when("in 2 Tagen um 9 Uhr", now).strftime("%d.%m %H:%M") == "30.10 09:00"
    assert parse_when("morgen Abend um 8", now).strftime("%d.%m %H:%M") == "29.10 20:00"
    assert parse_when("um 7 abends", now).strftime("%H:%M") == "19:00"
    assert parse_when("heute um 11 nachts", now).strftime("%H:%M") == "23:00"
    assert parse_when("morgen um 7 Uhr morgens", now).strftime("%H:%M") == "07:00"


def test_missed_reminder_older_than_grace_is_not_announced(tmp_path):
    from jarvis_core.memory.reminders import ReminderService
    s = ReminderService(tmp_path, misfire_grace_s=1); s.start()
    s.create("alt", datetime.now(BERLIN) + timedelta(seconds=1)); s.shutdown(); time.sleep(3.2)
    got = []
    s2 = ReminderService(tmp_path, misfire_grace_s=1, on_due=got.append); s2.start(); s2.deliver_pending(); s2.shutdown()
    assert got == [] and s2.list() == []


def test_echo_filter_keeps_real_commands():
    from voice_daemon.commands import is_own_echo
    said = ["Erinnerung gesetzt: Zahnarzt morgen um 9 Uhr, Sir."]
    assert not is_own_echo("Setze eine Erinnerung für morgen um 10", said)
    assert is_own_echo("Erinnerung gesetzt Zahnarzt morgen um 9 Uhr Sir", said)


def test_reminder_service_start_is_idempotent(tmp_path):
    from jarvis_core.memory.reminders import ReminderService
    s = ReminderService(tmp_path); s.start()
    assert s.start() == []
    s.shutdown()
