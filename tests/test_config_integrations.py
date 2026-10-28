"""Konfiguration (Profile, doppelte Schlüssel) und Integrationsstatus."""
from pathlib import Path

import pytest
import yaml

from jarvis_core import config
from jarvis_core.integrations.services import Discord, GoogleCalendar, HomeAssistant, Spotify, Telegram, build

ROOT = Path(__file__).parent.parent


def test_config_yaml_has_all_profiles_and_no_duplicates():
    cfg_path = ROOT / "config.yaml"
    raw = yaml.load(cfg_path.read_text(encoding="utf-8"), Loader=config._UniqueKeyLoader)   # wirft bei Duplikaten
    assert {"dev", "home", "mac"} <= set(raw["profiles"])
    assert raw["profiles"]["home"]["general"] == "qwen2.5:3b" and raw["profiles"]["home"]["heavy"] == "qwen2.5:7b"
    assert raw["profiles"]["dev"]["general"] == "qwen3.5:0.8b" and raw["profiles"]["dev"]["heavy"] is None
    assert config.load(cfg_path, "mac").profile.gpu_monitor is False


def test_duplicate_keys_rejected(tmp_path):
    p = tmp_path / "c.yaml"; p.write_text("profiles:\n  a:\n    general: x\n    general: y\n")
    with pytest.raises(config.ConfigError, match="doppelter Schlüssel"):
        config.load(p)


def test_auto_profile(monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin"); assert config.default_profile_name() == "mac"
    monkeypatch.setattr("sys.platform", "win32"); assert config.default_profile_name() == "home"


def test_env_file(tmp_path, monkeypatch):
    f = tmp_path / ".env"; f.write_text("# c\nJARVIS_TEST_X='abc'\nJARVIS_TEST_Y=1\n")
    monkeypatch.setenv("JARVIS_TEST_Y", "keep")
    config.load_env_file(f)
    import os
    assert os.environ["JARVIS_TEST_X"] == "abc" and os.environ["JARVIS_TEST_Y"] == "keep"
    monkeypatch.delenv("JARVIS_TEST_X")


@pytest.mark.parametrize("cls", [Spotify, HomeAssistant, Telegram, Discord])
def test_integration_setup_status(cls, monkeypatch):
    for v in cls.env:
        monkeypatch.delenv(v, raising=False)
    assert cls({"enabled": False}).status().state == "disabled"
    st = cls({"enabled": True}).status()
    assert st.state == "needs_setup" and st.missing and st.message


def test_calendar_needs_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr("jarvis_core.integrations.services.secrets_dir", lambda: tmp_path)
    monkeypatch.setattr("jarvis_core.integrations.base.secrets_dir", lambda: tmp_path)
    st = GoogleCalendar({"enabled": True}).status()
    assert st.state == "needs_setup" and "credentials.json" in st.missing[0]


def test_tool_without_credentials_fails_with_setup_help(tmp_path, monkeypatch):
    from tests.tools_helpers import runtime
    monkeypatch.delenv("JARVIS_HA_TOKEN", raising=False); monkeypatch.delenv("JARVIS_HA_URL", raising=False)
    rt = runtime(tmp_path)
    rt.ctx.services["integrations"] = build({"home_assistant": {"enabled": True}})
    r = rt.registry.execute(rt.ctx, "ha_states", {}, 2000)
    assert not r.ok and "needs_setup" in r.text and "JARVIS_HA_TOKEN" in r.text


def test_home_assistant_speaks_real_api(monkeypatch):
    """Gegen einen lokalen HTTP-Server mit HA-Antwortformat (kein Mock der Bibliothek)."""
    import http.server, json, threading
    seen = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen["auth"] = self.headers.get("Authorization")
            self.send_response(200); self.end_headers()
            self.wfile.write(json.dumps({"message": "API running."}).encode())
        def log_message(self, *a): pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    monkeypatch.setenv("JARVIS_HA_URL", f"http://127.0.0.1:{srv.server_port}")
    monkeypatch.setenv("JARVIS_HA_TOKEN", "tok")
    st = HomeAssistant({"enabled": True}).status()
    assert st.state == "ready" and seen["auth"] == "Bearer tok"


def test_optional_modules_honest(monkeypatch):
    from jarvis_core.integrations.optional import optional_status
    assert optional_status({"enabled": True}, "face_recognition").state == "error"
    assert optional_status({}, "translation").state == "disabled"
