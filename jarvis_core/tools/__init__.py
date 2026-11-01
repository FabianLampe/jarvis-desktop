"""Werkzeugsteuerung: baut die gemeinsame Registry aus allen Werkzeugmodulen."""
from __future__ import annotations

from .registry import ToolRegistry


def build_registry() -> ToolRegistry:
    from ..integrations import optional, services
    from . import (browser_tools, fs_tools, math_tools, memory_tools, research_tools,
                   shell_tools, system_tools, weather_tools, developer_tools, remote_tools,
                   artifact_tools, workflow_tools, mcp_tools, mac_ui_tools)
    reg = ToolRegistry()
    for mod in (fs_tools, shell_tools, system_tools, memory_tools, math_tools, research_tools,
                browser_tools, weather_tools, services, optional, developer_tools, remote_tools,
                artifact_tools, workflow_tools, mcp_tools, mac_ui_tools):
        reg.register(*mod.tools())
    return reg
