"""Werkzeugsteuerung: baut die gemeinsame Registry aus allen Werkzeugmodulen."""
from __future__ import annotations

from .registry import ToolRegistry


def build_registry() -> ToolRegistry:
    from ..integrations import optional, services
    from . import (browser_tools, fs_tools, math_tools, memory_tools, research_tools,
                   shell_tools, system_tools, weather_tools, developer_tools, remote_tools,
                   artifact_tools, workflow_tools, mcp_tools, mac_ui_tools, network_tools, health_tools, vision_tools, editor_tools,
                   shortcut_tools, music_tools, notes_tools, mac_system_tools, unit_tools, project_tools,
                   imagegen_tools, camera_watch)
    reg = ToolRegistry()
    for mod in (fs_tools, shell_tools, system_tools, memory_tools, math_tools, research_tools,
                browser_tools, weather_tools, services, optional, developer_tools, remote_tools,
                artifact_tools, workflow_tools, mcp_tools, mac_ui_tools, network_tools, health_tools, vision_tools, editor_tools,
                shortcut_tools, music_tools, notes_tools, mac_system_tools, unit_tools, project_tools,
                imagegen_tools, camera_watch):
        reg.register(*(_flag(t) for t in mod.tools()))
    return reg


# Zentrale Einstufung für den Abfluss-Schutz (registry._exfil_guard):
# PRIVATE liest persönliche Daten, OUTBOUND trägt vom Modell gewählte Texte/Adressen nach außen.
PRIVATE = {"read_file", "find_files", "memory_search", "docs_search", "clipboard", "screen_ocr", "screen_look", "image_look", "camera_look",
           "calendar_upcoming", "git_diff", "analyze_table", "mac_ui_snapshot", "reminder_list",
           "spotlight_search", "notes_search"}
OUTBOUND = {"fetch_url", "web_search", "research", "open_url", "search", "search_open", "play_youtube",
            "open_app", "telegram_send", "discord_send", "github", "shortcut_run", "app_type_text", "git_push",
            "project_install"}


def _flag(tool):
    import dataclasses
    return dataclasses.replace(tool, private=tool.name in PRIVATE, outbound=tool.name in OUTBOUND)
