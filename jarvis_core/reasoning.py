"""Gemeinsame Reasoning-Optionen für Chat und Werkzeuge (keine Modellnamen-Heuristik)."""
from __future__ import annotations

import json

from .config import AppConfig


def generation_options(client, cfg: AppConfig, model: str, enabled: bool) -> dict:
    """Alte Profile/Clients ohne Reasoning-Konfiguration behalten ihren API-Vertrag."""
    if not cfg.profile.reasoning:
        return {}
    options = {"num_predict": min(cfg.reasoning.max_tokens, max(128, cfg.profile.num_ctx // 3)),
               "max_time_s": cfg.reasoning.timeout_s}
    discover = getattr(client, "thinking_control", None)
    if discover is not None:
        control = discover(model, enabled)
        if control is not None:
            options["think"] = control
    return options


def fit_tool_context(messages: list[dict], tools: list[dict], num_ctx: int,
                     output_tokens: int) -> bool:
    """Antwortplatz reservieren; bei Platzmangel zuerst ältere Denktexte entfernen.

    Werkzeugergebnisse und Nutzerziel bleiben vollständig. Die Schätzung entspricht
    der bestehenden deutschen Chat-Schätzung; sie ist kein exakter Tokenizer.
    """
    def estimate():
        return int(len(json.dumps([messages, tools], ensure_ascii=False)) / 3.3) + 256

    budget = num_ctx - output_tokens
    if estimate() <= budget:
        return True
    for message in messages:
        if "thinking" in message:
            del message["thinking"]
            if estimate() <= budget:
                return True
    return False
