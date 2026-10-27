"""jarvis_core — schlanker, UI-unabhängiger Kern von J.A.R.V.I.S.

Router (Modellwahl), Persona (System-Prompt), Ollama-Client (Streaming + Swap),
System-Monitor und Konfiguration. Keine Importe aus jarvis_app — der Kern ist
später direkt vom Odysseus-Fork oder dem Sprach-Daemon nutzbar.
"""

__all__ = ["config", "persona", "router", "ollama_client", "sysmon", "chat"]
