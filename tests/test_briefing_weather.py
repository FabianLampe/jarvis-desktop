from unittest.mock import patch, MagicMock

from jarvis_core.briefing.weather import today


def _resp(payload):
    m = MagicMock()
    m.ok = True
    m.json.return_value = payload
    m.raise_for_status.return_value = None
    return m


def test_today_parses_open_meteo():
    payload = {"current": {"temperature_2m": 12.3, "weather_code": 3},
               "daily": {"temperature_2m_max": [15.0], "temperature_2m_min": [8.0]}}
    with patch("jarvis_core.briefing.weather.requests.get", return_value=_resp(payload)):
        w = today(53.55, 9.99)
    assert w["temp_now"] == 12 and w["temp_max"] == 15 and w["temp_min"] == 8
    assert "bewölkt" in w["text"].lower()


def test_today_handles_error():
    with patch("jarvis_core.briefing.weather.requests.get", side_effect=Exception("net")):
        w = today(0.0, 0.0)
    assert w is None
