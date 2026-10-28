"""Lokale Mathematik mit SymPy — im eigenen Prozess mit hartem Zeitlimit."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from .registry import Tool, ToolContext, ToolError, ToolResult

WORKER = Path(__file__).with_name("math_worker.py")
OPS = ["evaluate", "solve", "diff", "integrate", "simplify", "expand", "factor", "limit"]


def calculate(ctx: ToolContext, a: dict) -> ToolResult:
    req = {"op": a.get("op") or "evaluate", "expr": str(a.get("expr", "")), "var": a.get("var") or "x",
           "to": a.get("to", "0")}
    if req["op"] not in OPS:
        raise ToolError(f"op muss eines von {', '.join(OPS)} sein")
    try:
        p = subprocess.run([sys.executable, "-I", str(WORKER)],
                           input=json.dumps(req), capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        raise ToolError("Die Rechnung dauert zu lange (Zeitlimit 15 s) und wurde abgebrochen") from None
    try:
        out = json.loads(p.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise ToolError(f"Rechner lieferte keine Antwort: {(p.stderr or '')[-200:]}") from None
    if not out.get("ok"):
        raise ToolError(f"Nicht berechenbar: {out.get('error')}")
    return ToolResult(True, f"{req['op']}({req['expr']}) = {out['result']}", {"result": out["result"]})


def tools() -> list[Tool]:
    S = {"type": "string"}
    return [Tool("calculate", "math",
                 "Exakte Mathematik (SymPy): rechnen, Gleichungen lösen, ableiten, integrieren, vereinfachen. Potenz mit ^.",
                 {"expr": {**S, "description": "z. B. 2*x+3=11 oder sin(x)^2"}, "op": {**S, "enum": OPS},
                  "var": {**S, "description": "Variable, Standard x"}, "to": S},
                 ("expr",), calculate, announce=lambda a: "Ich rechne das nach, Sir.")]
