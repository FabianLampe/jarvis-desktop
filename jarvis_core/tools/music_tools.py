"""Apple Music (App „Musik“) steuern: abspielen, pausieren, springen, Zufallswiedergabe, „was läuft“.

Alle AppleScripts sind fest im Code; Suchbegriffe und Playlist-Namen kommen nur als argv an.
Namen von Titeln, Interpreten und Playlists sind fremder Text (Metadaten aus der Mediathek) und werden
bereinigt und als Fremdinhalt markiert. Die App startet nur bei „play“ — pausieren, weiter oder „was läuft“
wecken sie nie auf. Spotify bleibt eigene Sache (integrations); „Musik“ ohne Angabe meint Apple Music."""
from __future__ import annotations

import re

from . import mac_script as ms
from .network_tools import clean
from .registry import Tool, ToolContext, ToolError, ToolResult, wrap_untrusted

APP = "Music"
SEP = "\x1f"       # Feldtrenner in den Skriptausgaben (kommt in Titeln praktisch nie vor)

NOW_SCRIPT = f"""tell application "Music"
    if player state is stopped then return "stopped"
    set t to current track
    set sep to (ASCII character 31)
    return (player state as text) & sep & (name of t) & sep & (artist of t) & sep & (album of t)
end tell"""

PLAYLISTS_SCRIPT = """tell application "Music"
    set AppleScript's text item delimiters to linefeed
    return (name of every user playlist) as text
end tell"""

PLAY_PLAYLIST_SCRIPT = """on run argv
    tell application "Music"
        play (first user playlist whose name is (item 1 of argv))
    end tell
end run"""

# Titel-/Interpretensuche in der Mediathek; spielt den ersten Treffer. Leere Ausgabe = nichts gefunden.
SEARCH_PLAY_SCRIPT = """on run argv
    tell application "Music"
        set hits to search (first library playlist) for (item 1 of argv)
        if (count of hits) is 0 then return ""
        set t to item 1 of hits
        play t
        return (name of t) & (ASCII character 31) & (artist of t)
    end tell
end run"""

SIMPLE = {"play": "play", "pause": "pause", "next": "next track", "previous": "previous track",
          "shuffle_on": "set shuffle enabled to true", "shuffle_off": "set shuffle enabled to false"}
SPOKEN = {"play": "Die Wiedergabe läuft.", "pause": "Pausiert.", "next": "Nächster Titel.",
          "previous": "Voriger Titel.", "shuffle_on": "Zufallswiedergabe ist an.", "shuffle_off": "Zufallswiedergabe ist aus."}

# Stimmung → Namensstämme von Playlists, die dazu passen könnten (klein geschrieben, Teilstring-Abgleich).
MOODS = {
    "ruhig": ("ruhig", "entspann", "chill", "relax", "calm", "sanft", "ambient", "acoustic", "akustik"),
    "arbeiten": ("arbeit", "fokus", "focus", "konzentr", "work", "study", "lern", "deep"),
    "sport": ("sport", "workout", "training", "fitness", "gym", "lauf", "run"),
    "party": ("party", "tanz", "dance", "club"),
    "schlafen": ("schlaf", "sleep", "einschlaf", "night"),
    "gute_laune": ("laune", "happy", "fröhlich", "feel good", "sommer", "summer"),
}
MOOD_WORDS = {
    "ruhig": r"ruhig\w*|entspann\w*|chill\w*|sanft\w*|gem[üu]tlich\w*|relax\w*|zum entspannen",
    "arbeiten": r"arbeit\w*|konzentr\w*|fokus\w*|lern\w*|zum arbeiten|study",
    "sport": r"sport\w*|workout|training|fitness|laufen|joggen",
    "party": r"party\w*|tanz\w*|feier\w*",
    "schlafen": r"schlaf\w*|einschlaf\w*",
    "gute_laune": r"gute laune|fröhlich\w*|happy|beschwingt\w*|motivier\w*",
}


def _running() -> bool:
    return ms.is_running(APP)


def playlist_names() -> list[str]:
    raw = ms.osa(PLAYLISTS_SCRIPT, app="Musik")
    return [n for n in raw.splitlines() if n.strip()]


def detect_mood(query: str) -> str | None:
    low = query.casefold()
    for mood, rx in MOOD_WORDS.items():
        if re.search(rf"\b(?:{rx})", low):
            return mood
    return None


def pick_playlist(query: str, names: list[str]) -> str | None:
    """Echter Playlist-Name zur Anfrage: exakt, dann Teiltreffer, dann passend zur Stimmung — sonst None."""
    q = query.strip().casefold()
    for n in names:
        if n.casefold() == q:
            return n
    sub = [n for n in names if q and (q in n.casefold() or n.casefold() in q) and len(n) >= 3]
    if len(sub) == 1:
        return sub[0]
    mood = detect_mood(query)
    if mood:
        for stem in MOODS[mood]:
            for n in names:
                if stem in n.casefold():
                    return n
    return None


def _play_query(query: str) -> ToolResult:
    names = playlist_names()
    chosen = pick_playlist(query, names)
    if chosen:
        ms.osa(PLAY_PLAYLIST_SCRIPT, chosen, app="Musik")
        return ToolResult(True, f"Playlist „{clean(chosen, 60)}“ läuft.", {"playlist": clean(chosen, 60)})
    if detect_mood(query):
        shown = ", ".join(clean(n, 40) for n in names[:15]) or "keine"
        return ToolResult(False, f"Zu „{clean(query, 40)}“ passt keine meiner Playlists in Apple Music. Vorhanden: "
                                 + wrap_untrusted(shown))
    out = ms.osa(SEARCH_PLAY_SCRIPT, query, app="Musik", timeout=45)
    if not out.strip():
        shown = ", ".join(clean(n, 40) for n in names[:12]) or "keine"
        return ToolResult(False, f"In der Mediathek finde ich nichts zu „{clean(query, 40)}“. Playlists: "
                                 + wrap_untrusted(shown))
    title, _, artist = out.partition(SEP)
    return ToolResult(True, "Es läuft: " + wrap_untrusted(f"{clean(title, 80)}" + (f" – {clean(artist, 60)}" if artist else "")))


def music_control(ctx: ToolContext, a: dict) -> ToolResult:
    action = str(a.get("action", ""))
    query = str(a.get("query") or "").strip()
    volume = a.get("volume")
    if action == "volume":
        if not isinstance(volume, int) or isinstance(volume, bool) or not 0 <= volume <= 100:
            raise ToolError("volume muss eine Zahl von 0 bis 100 sein.")
        if not _running():
            return ToolResult(False, "Apple Music läuft gerade nicht, Sir.")
        ms.osa(f"on run argv\n tell application \"{APP}\" to set sound volume to ((item 1 of argv) as integer)\nend run",
               str(volume), app="Musik")
        return ToolResult(True, f"Musik-Lautstärke {volume} Prozent.")
    if action not in SIMPLE:
        raise ToolError("action muss play, pause, next, previous, shuffle_on, shuffle_off oder volume sein.")
    if action == "play":
        if query:
            return _play_query(query[:200])
        ms.osa(f'tell application "{APP}" to play', app="Musik")
        return ToolResult(True, SPOKEN["play"])
    if not _running():
        return ToolResult(False, "Apple Music läuft gerade nicht, Sir.")
    ms.osa(f'tell application "{APP}" to {SIMPLE[action]}', app="Musik")
    return ToolResult(True, SPOKEN[action])


def music_now(ctx: ToolContext, a: dict) -> ToolResult:
    if not _running():
        return ToolResult(True, "Apple Music läuft gerade nicht, Sir.")
    out = ms.osa(NOW_SCRIPT, app="Musik")
    if out.strip() in ("", "stopped"):
        return ToolResult(True, "In Apple Music läuft gerade nichts.")
    state, title, artist, album = (out.split(SEP) + ["", "", "", ""])[:4]
    verb = "Es läuft" if state.strip() == "playing" else "Pausiert"
    info = f"{clean(title, 80)}" + (f" – {clean(artist, 60)}" if artist.strip() else "") \
           + (f" ({clean(album, 60)})" if album.strip() else "")
    return ToolResult(True, f"{verb}: " + wrap_untrusted(info))


def tools() -> list[Tool]:
    S = {"type": "string"}
    return [
        Tool("music_control", "music",
             "Apple Music (App „Musik“) steuern. action: play (ohne query: weiter; mit query: Playlist, Titel oder "
             "Interpret aus der Mediathek, auch Stimmungen wie „etwas Ruhiges“), pause, next, previous, shuffle_on, "
             "shuffle_off, volume (volume 0–100). query und volume sind optional: „Spiel Musik“ ohne query startet einfach die "
             "Wiedergabe — nicht nachfragen. Standard für „Musik“, wenn Sir keinen Dienst nennt.",
             {"action": {**S, "enum": [*SIMPLE, "volume"]}, "query": S, "volume": {"type": "integer"}},
             ("action",), music_control, untrusted=True,
             announce=lambda a: "Ich kümmere mich um die Musik, Sir."),
        Tool("music_now", "music", "Was gerade in Apple Music läuft (Titel, Interpret).", {}, (), music_now,
             untrusted=True),
    ]
