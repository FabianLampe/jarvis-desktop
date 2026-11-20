"""Gemeinsame Test-Absicherungen."""
import pytest


@pytest.fixture(autouse=True)
def _no_real_browser(monkeypatch):
    # config.yaml enthält Startlinks (experience.open_on_start) — Tests öffnen nie echte Fenster.
    monkeypatch.setattr("webbrowser.open", lambda *a, **k: True)


@pytest.fixture(autouse=True)
def _no_real_mac_processes(monkeypatch):
    """osascript/shortcuts/mdfind/… dürfen in Tests nie wirklich laufen — Tests ersetzen `_spawn` gezielt."""
    from jarvis_core.tools import mac_script

    def refuse(argv, timeout, stdin_text=None):
        raise AssertionError(f"Test startete einen echten Prozess: {argv[:3]}")
    monkeypatch.setattr(mac_script, "_spawn", refuse)
