"""Gemeinsamer Aufbau von Werkzeugkontext, Gedächtnis und Agent (App, CLI, Tests)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import AppConfig
from .ollama_client import OllamaClient
from .toolagent import ToolAgent
from .tools import build_registry
from .tools.memory_tools import get_memory
from .tools.registry import ConfirmationManager, ToolContext, ToolRegistry


@dataclass
class Runtime:
    cfg: AppConfig
    ctx: ToolContext
    registry: ToolRegistry
    confirms: ConfirmationManager
    agent: ToolAgent

    def facts(self, text: str) -> list[str]:
        """Bis zu 3 passende gespeicherte Fakten für den Prompt (nur wenn vorhanden)."""
        db = self.ctx.data_dir / "memory.db"
        if not db.exists():
            return []
        return [r["text"] for r in get_memory(self.ctx).search(text, 3)]


def spawn_agent(rt: "Runtime", interactive: bool = True) -> ToolAgent:
    """Neuer, unabhängiger Agent (eigener Kontext, eigener Abbruch, eigene Prozesse) für eine parallele
    Aufgabe. Browser, Wissensbasis, Gedächtnis, Erinnerungen werden mit den anderen Aufgaben geteilt."""
    ctx = ToolContext(rt.cfg, workspace=rt.ctx.workspace, data_dir=rt.ctx.data_dir, shared=rt.ctx.services)
    return ToolAgent(rt.cfg, rt.agent.client, rt.registry, ctx, rt.confirms, interactive=interactive,
                     facts=rt.facts, reusable=False)


def build_runtime(cfg: AppConfig, client: Optional[OllamaClient] = None, *, interactive: bool = True,
                  workspace: Optional[Path] = None, data_dir: Optional[Path] = None) -> Runtime:
    # None ⇒ Standard (Arbeitsordner ~/Jarvis bzw. Plattform-Datenordner, siehe ToolContext.__post_init__)
    ctx = ToolContext(cfg, workspace=workspace, data_dir=data_dir)  # pyright: ignore[reportArgumentType]
    reg = build_registry()
    confirms = ConfirmationManager(cfg.tools.confirm_ttl_s)
    rt = Runtime(cfg, ctx, reg, confirms, None)  # type: ignore[arg-type]
    rt.agent = ToolAgent(cfg, client if client is not None else OllamaClient(cfg.ollama_host),
                         reg, ctx, confirms, interactive=interactive, facts=rt.facts)
    return rt
