from jarvis_core.config import load


def test_browser_defaults():
    cfg = load(None)
    assert cfg.browser.enabled in (True, False)
    assert cfg.browser.profile_dir == ".chrome-jarvis"
    assert cfg.browser.max_steps >= 1


def test_browser_from_yaml(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("profile: home\nbrowser:\n  enabled: true\n  max_steps: 4\n", encoding="utf-8")
    cfg = load(p)
    assert cfg.browser.enabled is True and cfg.browser.max_steps == 4
