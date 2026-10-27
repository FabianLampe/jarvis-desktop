from jarvis_core.config import load


def test_briefing_defaults():
    cfg = load(None)
    assert cfg.briefing.github_repo == "FabianLampe/jarvis-desktop"
    assert cfg.briefing.gmail_max >= 1
    assert isinstance(cfg.briefing.weather_lat, float)


def test_briefing_from_yaml(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("profile: home\nbriefing:\n  github_repo: a/b\n  weather: {lat: 52.5, lon: 13.4, ort: Berlin}\n",
                 encoding="utf-8")
    cfg = load(p)
    assert cfg.briefing.github_repo == "a/b" and cfg.briefing.weather_ort == "Berlin"
