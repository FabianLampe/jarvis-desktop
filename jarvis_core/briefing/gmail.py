"""Neue Gmail-Nachrichten (nur lesen) über die Gmail-API.

OAuth-Setup einmalig (Desktop-Client): .secrets/credentials.json bereitstellen,
dann  python -m jarvis_core.briefing.gmail --login  → schreibt .secrets/token.json.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("jarvis.briefing.gmail")

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def _secrets_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / ".secrets"


def _header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _sender_name(value: str) -> str:
    # „Alice <a@x.de>" → „Alice"; „a@x.de" → „a@x.de"
    value = value.strip()
    if "<" in value:
        name = value.split("<", 1)[0].strip().strip('"')
        return name or value.split("<", 1)[1].rstrip(">")
    return value


def recent_unread(service, max_results: int = 10) -> list[dict]:
    """Liefert neue (ungelesene) Mails [{id,sender,subject,snippet}]."""
    resp = service.users().messages().list(
        userId="me", labelIds=["INBOX", "UNREAD"], maxResults=max_results).execute()
    out = []
    for ref in resp.get("messages", []):
        msg = service.users().messages().get(
            userId="me", id=ref["id"], format="metadata",
            metadataHeaders=["From", "Subject"]).execute()
        headers = msg.get("payload", {}).get("headers", [])
        out.append({
            "id": ref["id"],
            "sender": _sender_name(_header(headers, "From")),
            "subject": _header(headers, "Subject") or "(kein Betreff)",
            "snippet": (msg.get("snippet", "") or "").strip(),
        })
    return out


def build_service():
    """Baut den Gmail-Service aus .secrets/token.json (None, wenn nicht eingerichtet)."""
    token = _secrets_dir() / "token.json"
    if not token.is_file():
        log.info("Gmail nicht eingerichtet (kein token.json) — "
                 "python -m jarvis_core.briefing.gmail --login")
        return None
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        creds = Credentials.from_authorized_user_file(str(token), SCOPES)
        return build("gmail", "v1", credentials=creds, cache_discovery=False)
    except Exception as exc:  # noqa: BLE001
        log.warning("Gmail-Service nicht aufbaubar: %s", exc)
        return None


def login() -> None:
    """Einmaliger OAuth-Login → .secrets/token.json."""
    from google_auth_oauthlib.flow import InstalledAppFlow
    sd = _secrets_dir()
    sd.mkdir(parents=True, exist_ok=True)
    cred = sd / "credentials.json"
    if not cred.is_file():
        raise SystemExit(f"Lege das OAuth-Client-Secret als {cred} ab "
                         f"(Google Cloud → Desktop-App).")
    flow = InstalledAppFlow.from_client_secrets_file(str(cred), SCOPES)
    creds = flow.run_local_server(port=0)
    (sd / "token.json").write_text(creds.to_json(), encoding="utf-8")
    print("Gmail eingerichtet — token.json geschrieben.")


if __name__ == "__main__":
    import sys
    if "--login" in sys.argv:
        login()
    else:
        print("Aufruf: python -m jarvis_core.briefing.gmail --login")
