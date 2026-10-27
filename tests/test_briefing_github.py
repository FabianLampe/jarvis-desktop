from unittest.mock import patch, MagicMock

from jarvis_core.briefing.github import open_issues


def _resp(payload):
    m = MagicMock()
    m.ok = True
    m.status_code = 200
    m.json.return_value = payload
    m.raise_for_status.return_value = None
    return m


def test_open_issues_filters_pull_requests():
    payload = [
        {"number": 5, "title": "Bug A", "labels": [{"name": "bug"}], "updated_at": "2026-06-16T10:00:00Z"},
        {"number": 6, "title": "PR B", "pull_request": {"url": "x"}, "labels": [], "updated_at": "x"},
    ]
    with patch("jarvis_core.briefing.github.requests.get", return_value=_resp(payload)):
        issues = open_issues("a/b", token="t")
    assert len(issues) == 1 and issues[0]["number"] == 5 and issues[0]["labels"] == ["bug"]


def test_open_issues_error_returns_none():
    with patch("jarvis_core.briefing.github.requests.get", side_effect=Exception("net")):
        assert open_issues("a/b", token="t") is None
