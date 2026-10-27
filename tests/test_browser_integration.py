import shutil

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.skipif(not shutil.which("npx"), reason="npx (Node) nicht installiert")
def test_start_and_list_tools():
    from jarvis_core.tools.browser import BrowserController
    bc = BrowserController(profile_dir=".chrome-jarvis-test", headless=True)
    try:
        bc.ensure_started()
        names = bc.tool_names()
        assert "navigate_page" in names and "take_snapshot" in names
    finally:
        bc.shutdown()


@pytest.mark.skipif(not shutil.which("npx"), reason="npx nicht installiert")
def test_open_and_read(tmp_path):
    from jarvis_core.tools.browser import BrowserController
    page = tmp_path / "p.html"
    page.write_text("<html><body><h1>Jarvis Testseite</h1><p>Hallo Sir</p></body></html>",
                    encoding="utf-8")
    bc = BrowserController(profile_dir=".chrome-jarvis-test", headless=True)
    try:
        bc.ensure_started()
        bc.open_url(page.as_uri())
        text = bc.read_page()
        assert "Jarvis Testseite" in text and "Hallo Sir" in text
        assert isinstance(bc.list_tabs(), str)
        bc.close_tab()
    finally:
        bc.shutdown()
