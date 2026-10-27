# Sorgt dafür, dass pytest das Repo-Wurzelverzeichnis auf sys.path legt
# (jarvis_core / jarvis_app sind damit in den Tests importierbar).


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "integration: benötigt externe Dienste (Node/Chrome)")
