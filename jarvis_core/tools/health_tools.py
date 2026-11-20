"""Rechner-Diagnose: Akku, Speicherplatz, Auslastung, Bremser, Bluetooth, Updates.

Rein lesend. Prozess- und Gerätenamen sind fremde Bezeichnungen und werden auf harmlose
Zeichen gekürzt, bevor das Modell sie sieht."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import psutil

from .network_tools import _run, clean
from .registry import Tool, ToolContext, ToolError, ToolResult

DISK_WARN_PCT = 90
BATTERY_WARN_PCT = 15


def _gb(n: float) -> str:
    return f"{n / 1e9:.1f} GB"


def data_volume() -> Path:
    """macOS legt Nutzerdaten auf ein eigenes APFS-Volume; „/“ allein zeigt nur das Systemabbild."""
    p = Path("/System/Volumes/Data")
    return p if p.exists() else Path(Path.home().anchor or "/")


def disk_status() -> dict:
    u = psutil.disk_usage(str(data_volume()))
    return {"total": u.total, "free": u.free, "used_pct": round(100 * (u.total - u.free) / u.total, 1) if u.total else 0.0}


def battery_status() -> dict:
    b = psutil.sensors_battery()
    if b is None:
        return {}
    out: dict = {"percent": round(b.percent), "plugged": bool(b.power_plugged)}
    if not b.power_plugged and b.secsleft and b.secsleft > 0:
        out["minutes_left"] = int(b.secsleft // 60)
    raw = _run(["system_profiler", "SPPowerDataType", "-json"], 12)
    try:
        for item in json.loads(raw).get("SPPowerDataType", []):
            h = item.get("sppower_battery_health_info")
            if isinstance(h, dict):
                out["cycles"] = h.get("sppower_battery_cycle_count")
                out["condition"] = clean(h.get("sppower_battery_health"), 20) or None
                out["max_capacity"] = clean(h.get("sppower_battery_health_maximum_capacity"), 8) or None
    except ValueError:
        pass
    return out


def top_processes(n: int = 5) -> tuple[list[dict], list[dict]]:
    """(nach CPU, nach Speicher). CPU wird über ein kurzes Messfenster ermittelt."""
    procs = []
    for p in psutil.process_iter(["pid", "name"]):
        try:
            p.cpu_percent(None)
            procs.append(p)
        except (psutil.Error, OSError):
            continue
    time.sleep(0.6)
    rows = []
    for p in procs:
        try:
            rows.append({"pid": p.pid, "name": clean(p.info.get("name"), 32) or "?",
                         "cpu": round(p.cpu_percent(None), 1), "mem": p.memory_info().rss})
        except (psutil.Error, OSError):
            continue
    by_cpu = sorted(rows, key=lambda r: r["cpu"], reverse=True)[:n]
    by_mem = sorted(rows, key=lambda r: r["mem"], reverse=True)[:n]
    return by_cpu, by_mem


def system_health(ctx: ToolContext, a: dict) -> ToolResult:
    disk = disk_status()
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    cores = psutil.cpu_count() or 1
    by_cpu, by_mem = top_processes()
    cpu = round(psutil.cpu_percent(0.3))
    try:
        load = psutil.getloadavg()[0]
    except (OSError, AttributeError):
        load = 0.0
    bat = battery_status()
    up_h = (time.time() - psutil.boot_time()) / 3600
    lines = [
        f"Speicherplatz: {_gb(disk['free'])} frei von {_gb(disk['total'])} ({disk['used_pct']} % belegt)",
        f"Arbeitsspeicher: {round(mem.percent)} % belegt ({_gb(mem.available)} verfügbar von {_gb(mem.total)})"
        + (f", Auslagerung {_gb(swap.used)}" if swap.used > 5e8 else ""),
        f"Prozessor: {cpu} % ausgelastet, Last {load:.1f} bei {cores} Kernen",
    ]
    if bat:
        b = f"Akku: {bat['percent']} %, " + ("am Netzteil" if bat["plugged"] else "im Akkubetrieb")
        if bat.get("minutes_left"):
            b += f", noch etwa {bat['minutes_left'] // 60} h {bat['minutes_left'] % 60} min"
        if bat.get("max_capacity"):
            b += f", maximale Kapazität {bat['max_capacity']}"
        if bat.get("cycles") is not None:
            b += f", {bat['cycles']} Ladezyklen"
        if bat.get("condition"):
            b += f", Zustand {bat['condition']}"
        lines.append(b)
    lines.append(f"Läuft seit: {up_h / 24:.1f} Tagen" if up_h >= 48 else f"Läuft seit: {up_h:.0f} Stunden")
    lines.append("Größte Prozessorlast: " + (", ".join(f"{r['name']} {r['cpu']} %" for r in by_cpu if r["cpu"] >= 1) or "keine"))
    lines.append("Größter Speicherbedarf: " + ", ".join(f"{r['name']} {_gb(r['mem'])}" for r in by_mem))
    notes = []
    if disk["used_pct"] >= DISK_WARN_PCT:
        notes.append("Die Platte ist fast voll.")
    if mem.percent >= 90 or swap.used > 4e9:
        notes.append("Der Arbeitsspeicher ist knapp; das bremst.")
    if load > cores * 1.5:
        notes.append("Der Prozessor ist überlastet.")
    if bat and not bat["plugged"] and bat["percent"] <= BATTERY_WARN_PCT:
        notes.append("Der Akku ist fast leer.")
    lines.append("Auffälligkeiten: " + (" ".join(notes) if notes else "keine"))
    return ToolResult(True, "\n".join(lines), {
        "disk_free_gb": round(disk["free"] / 1e9, 1), "disk_used_pct": disk["used_pct"],
        "mem_pct": round(mem.percent), "cpu_pct": cpu, "battery": bat or None, "notes": len(notes)})


def bluetooth_devices(ctx: ToolContext, a: dict) -> ToolResult:
    raw = _run(["system_profiler", "SPBluetoothDataType", "-json"], 15)
    try:
        data = json.loads(raw).get("SPBluetoothDataType", [])
    except ValueError as exc:
        raise ToolError("Bluetooth-Informationen sind nicht lesbar.") from exc
    lines, count = [], 0
    for ctrl in data:
        state = str((ctrl.get("controller_properties") or {}).get("controller_state", ""))
        if "off" in state.lower():
            return ToolResult(True, "Bluetooth ist ausgeschaltet.")
        for key, title in (("device_connected", "Verbunden"), ("device_not_connected", "Gekoppelt, nicht verbunden")):
            for entry in ctrl.get(key) or []:
                for name, props in entry.items():
                    props = props if isinstance(props, dict) else {}
                    kind = clean(props.get("device_minorType"), 24)
                    bat = [f"{k.split('BatteryLevel')[-1] or 'Akku'} {clean(v, 6)}".strip()
                           for k, v in props.items() if "batteryLevel" in k or "BatteryLevel" in k]
                    lines.append(f"{title}: {clean(name, 40)}" + (f" ({kind})" if kind else "")
                                 + (f", Akku {', '.join(re.sub(r'^Akku ', '', b) for b in bat)}" if bat else ""))
                    count += 1
    if not lines:
        return ToolResult(True, "Es sind keine Bluetooth-Geräte gekoppelt.")
    return ToolResult(True, "\n".join(lines), {"count": count})


def software_updates(ctx: ToolContext, a: dict) -> ToolResult:
    """Fragt Apples Update-Server (dauert bis zu einer Minute). Installiert nichts."""
    out = _run(["softwareupdate", "-l"], 90)
    if not out.strip():
        raise ToolError("Die Updateprüfung lieferte keine Antwort (offline oder Zeitlimit).")
    titles = [clean(t, 70) for t in re.findall(r"Title:\s*([^,\n]+)", out)]
    if not titles:
        return ToolResult(True, "macOS ist auf dem aktuellen Stand; es stehen keine Updates aus.", {"count": 0})
    restart = "restart" in out.lower()
    return ToolResult(True, f"{len(titles)} Update(s) verfügbar: " + "; ".join(titles)
                      + (". Mindestens eines braucht einen Neustart." if restart else "."), {"count": len(titles)})


def tools() -> list[Tool]:
    return [
        Tool("system_health", "health",
             "Zustand dieses Rechners: Speicherplatz, Arbeitsspeicher, Prozessorlast, Akku, Laufzeit, größte Verbraucher, Auffälligkeiten.",
             {}, (), system_health, announce=lambda a: "Ich prüfe den Zustand des Systems, Sir."),
        Tool("bluetooth_devices", "health", "Gekoppelte und verbundene Bluetooth-Geräte samt Akkustand.",
             {}, (), bluetooth_devices),
        Tool("software_updates", "health", "Prüft, ob macOS-Updates ausstehen (installiert nichts, dauert bis zu 1 min).",
             {}, (), software_updates, announce=lambda a: "Ich frage nach Updates, Sir. Das dauert einen Moment."),
    ]
