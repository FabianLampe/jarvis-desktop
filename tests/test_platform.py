"""Plattformtrennung: Adapterwahl und argv-Konstruktion (Windows-Adapter nur per Mock)."""
import sys
from pathlib import Path

import pytest

from jarvis_core.platform import current, for_name
from jarvis_core.platform.base import PlatformError


def test_adapter_selection():
    assert for_name("macos").name == "macos" and for_name("macos").gui == "cocoa"
    assert for_name("windows").name == "windows" and for_name("windows").gui == "edgechromium"
    assert for_name("other").name == "other"
    assert current().name == {"darwin": "macos", "win32": "windows"}.get(sys.platform, "other")


def test_mac_open_target(tmp_path):
    m = for_name("macos")
    f = tmp_path / "mit Leerzeichen.txt"; f.write_text("x")
    assert m.open_target("Safari") == ["open", "-a", "Safari"]
    assert m.open_target("https://example.org") == ["open", "https://example.org"]
    assert m.open_target(str(f)) == ["open", str(f)]
    with pytest.raises(PlatformError):
        m.open_target("  ")


def test_mac_notify_text_is_argument_not_script():
    argv = for_name("macos").notify_argv('Titel', 'x" & do shell script "id')
    assert argv[0] == "osascript" and argv[-1] == 'x" & do shell script "id'
    assert "do shell script" not in argv[2]          # Skript-Quelltext enthält den Text nie


def test_shell_argv_and_windows_mock():
    assert for_name("macos").shell_argv("ls | wc")[:2] == ["/bin/zsh", "-c"]
    w = for_name("windows")
    assert w.shell_argv("dir")[:2] == ["cmd.exe", "/c"]
    assert w.open_target(r"C:\a b\c.txt")[-1] == r"C:\a b\c.txt"
    assert "creationflags" in w.popen_kwargs()
    assert "start_new_session" in for_name("macos").popen_kwargs()


def test_windows_only_features_not_on_mac():
    assert for_name("macos").gpu_status() is None          # kein nvidia-smi auf dem Mac
    with pytest.raises(PlatformError):
        for_name("other").screenshot(Path("x.png"))


def test_requirements_have_no_windows_libs():
    text = (Path(__file__).parent.parent / "requirements.txt").read_text()
    assert "pywinauto" not in text and "pycaw" not in text


def test_data_dir_override(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "d"))
    assert for_name("macos").data_dir() == tmp_path / "d"
