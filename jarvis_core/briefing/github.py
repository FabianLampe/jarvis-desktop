"""Offene GitHub-Issues eines Repos über die REST-API (read-only)."""
from __future__ import annotations

import logging
from typing import Optional

import requests

log = logging.getLogger("jarvis.briefing.github")


def open_issues(repo: str, token: Optional[str] = None, limit: int = 20,
                timeout: float = 8.0) -> Optional[list[dict]]:
    """Liefert offene Issues (ohne Pull-Requests) als [{number,title,labels,updated_at}]
    oder None bei Fehler. token=None ⇒ nur öffentliche Repos, niedrigeres Rate-Limit."""
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = requests.get(f"https://api.github.com/repos/{repo}/issues",
                         params={"state": "open", "per_page": limit, "sort": "updated"},
                         headers=headers, timeout=timeout)
        r.raise_for_status()
        out = []
        for it in r.json():
            if "pull_request" in it:  # PRs zählen bei GitHub als Issues — herausfiltern
                continue
            out.append({"number": it["number"], "title": it["title"],
                        "labels": [lab["name"] for lab in it.get("labels", [])],
                        "updated_at": it.get("updated_at", "")})
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("GitHub-Issues nicht abrufbar: %s", exc)
        return None
