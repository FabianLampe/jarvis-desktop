"""Netzwerk-Werkzeuge: Überblick, Geräte im Heimnetz, Geschwindigkeit, offene Ports.

Alles ist lesend. Gerätenamen und Herstellerangaben stammen von fremden Geräten bzw. aus
einer heruntergeladenen Liste — sie werden auf harmlose Zeichen reduziert, bevor das Modell
sie sieht. Portprüfungen gehen ausschließlich an private Adressen im eigenen Netz.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import re
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from .registry import ALLOW, CONFIRM, Assessment, Tool, ToolContext, ToolError, ToolResult

log = logging.getLogger("jarvis.network")

MAX_SWEEP_HOSTS = 1024
MAX_LISTED = 40
OUI_URL = "https://www.wireshark.org/download/automated/data/manuf"
OUI_MAX_AGE_S = 90 * 86400
COMMON_PORTS = {
    21: "FTP", 22: "SSH", 23: "Telnet", 25: "SMTP", 53: "DNS", 80: "HTTP", 88: "Kerberos",
    110: "POP3", 139: "NetBIOS", 143: "IMAP", 443: "HTTPS", 445: "SMB (Dateifreigabe)",
    515: "Drucker (LPD)", 548: "AFP", 554: "RTSP (Kamera/Stream)", 631: "Drucker (IPP)",
    1883: "MQTT", 3000: "Web-App", 3306: "MySQL", 3389: "Remote Desktop", 5000: "AirPlay/UPnP",
    5353: "mDNS", 5432: "PostgreSQL", 5900: "Bildschirmfreigabe (VNC)", 7000: "AirPlay",
    8000: "Web", 8080: "Web (alternativ)", 8123: "Home Assistant", 8443: "HTTPS (alternativ)",
    8883: "MQTT (TLS)", 9000: "Web", 9100: "Drucker (RAW)", 11434: "Ollama", 32400: "Plex",
    49152: "UPnP", 62078: "iPhone-Sync",
}
_SAFE = re.compile(r"[^A-Za-z0-9ÄÖÜäöüß ._\-()+&/]")


def clean(value, limit: int = 48) -> str:
    """Fremde Bezeichnungen (Gerätename, Hersteller) auf unverfängliche Zeichen kürzen."""
    text = re.sub(r"\s", " ", str(value or ""))          # auch geschützte Leerzeichen bleiben Wortgrenzen
    return re.sub(r" +", " ", _SAFE.sub("", text)).strip()[:limit]


def _run(argv: list[str], timeout: float = 10.0) -> str:
    """Fester, lesender Systembefehl. Fehlt das Programm oder läuft es zu lang: leerer Text."""
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
        return r.stdout or ""
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("%s nicht ausführbar: %s", argv[0], exc)
        return ""


def norm_mac(mac: str) -> str:
    parts = re.split(r"[:\-]", mac.strip().lower())
    if len(parts) != 6 or any(not re.fullmatch(r"[0-9a-f]{1,2}", p) for p in parts):
        return ""
    return ":".join(p.zfill(2) for p in parts)


def is_private_mac(mac: str) -> bool:
    """Lokal vergebene (zufällige) Adresse — typisch für Handys/Tablets mit „privater WLAN-Adresse“."""
    try:
        return bool(int(mac.split(":")[0], 16) & 0b10)
    except (ValueError, IndexError):
        return False


# ------------------------------------------------------------------ Lage

def default_route() -> dict:
    out = _run(["route", "-n", "get", "default"], 5)
    gw = re.search(r"gateway:\s*(\S+)", out)
    iface = re.search(r"interface:\s*(\S+)", out)
    return {"gateway": gw.group(1) if gw else None, "interface": iface.group(1) if iface else None}


def interface_info(iface: Optional[str]) -> dict:
    """IP, Netzmaske und MAC der Schnittstelle (psutil, plattformneutral)."""
    import psutil
    info: dict = {"interface": iface, "ip": None, "netmask": None, "mac": None}
    addrs = psutil.net_if_addrs()
    names = [iface] if iface in addrs else [n for n in addrs if not n.startswith(("lo", "utun", "awdl", "llw", "bridge"))]
    for name in names:
        ip = next((a for a in addrs[name] if a.family == socket.AF_INET
                   and not a.address.startswith(("127.", "169.254."))), None)
        if ip is None:
            continue
        mac = next((a.address for a in addrs[name] if a.family == psutil.AF_LINK), "")
        info.update(interface=name, ip=ip.address, netmask=ip.netmask, mac=norm_mac(mac) or None)
        break
    return info


def local_network(info: dict) -> Optional[ipaddress.IPv4Network]:
    if not info.get("ip") or not info.get("netmask"):
        return None
    try:
        return ipaddress.IPv4Network(f"{info['ip']}/{info['netmask']}", strict=False)
    except ValueError:
        return None


def dns_servers() -> list[str]:
    out = _run(["scutil", "--dns"], 5)
    found = list(dict.fromkeys(re.findall(r"nameserver\[\d+\]\s*:\s*(\S+)", out)))
    if not found:
        try:
            found = re.findall(r"^nameserver\s+(\S+)", Path("/etc/resolv.conf").read_text(), re.M)
        except OSError:
            found = []
    return found[:4]


def link_type(iface: Optional[str]) -> str:
    out = _run(["networksetup", "-listallhardwareports"], 5)
    m = re.search(r"Hardware Port:\s*(.+)\nDevice:\s*" + re.escape(iface or "") + r"\b", out)
    if not m:
        return ""
    port = m.group(1).strip()
    return "WLAN" if re.search(r"wi-?fi|airport|wlan", port, re.I) else clean(port, 40)


def wifi_details(iface: Optional[str]) -> dict:
    """Kanal, Standard, Signal, Datenrate, Verschlüsselung der aktuellen WLAN-Verbindung (macOS)."""
    raw = _run(["system_profiler", "SPAirPortDataType", "-json"], 15)
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    for group in data.get("SPAirPortDataType", []):
        for i in group.get("spairport_airport_interfaces", []):
            cur = i.get("spairport_current_network_information")
            if not isinstance(cur, dict) or (iface and i.get("_name") != iface):
                continue
            name = str(cur.get("_name") or "")
            sig = re.search(r"(-?\d+)\s*dBm\s*/\s*(-?\d+)", str(cur.get("spairport_signal_noise") or ""))
            sec = str(cur.get("spairport_security_mode") or "").replace("spairport_security_mode_", "")
            return {
                # Ohne Ortungsfreigabe verbirgt macOS den Netzwerknamen.
                "ssid": None if not name or "redacted" in name.lower() else clean(name, 40),
                "channel": clean(cur.get("spairport_network_channel"), 30) or None,
                "standard": clean(cur.get("spairport_network_phymode"), 30) or None,
                "signal_dbm": int(sig.group(1)) if sig else None,
                "noise_dbm": int(sig.group(2)) if sig else None,
                "rate_mbit": cur.get("spairport_network_rate"),
                "security": clean(sec.replace("_", " ").upper(), 30) or None,
                "nearby_networks": len(i.get("spairport_airport_other_local_wireless_networks") or []),
            }
    return {}


def signal_word(dbm: Optional[int]) -> str:
    if dbm is None:
        return ""
    return ("ausgezeichnet" if dbm >= -55 else "gut" if dbm >= -67 else
            "mittel" if dbm >= -75 else "schwach")


def internet_latency(host: str = "1.1.1.1", port: int = 443, timeout: float = 3.0) -> Optional[float]:
    """Millisekunden bis zum TCP-Aufbau ins Internet; None = keine Verbindung."""
    start = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return round((time.monotonic() - start) * 1000, 1)
    except OSError:
        return None


def network_overview(ctx: ToolContext, a: dict) -> ToolResult:
    route = default_route()
    info = interface_info(route["interface"])
    if not info["ip"]:
        raise ToolError("Dieser Rechner hat gerade keine Netzwerkverbindung (keine IPv4-Adresse).")
    net = local_network(info)
    kind = link_type(info["interface"])
    wifi = wifi_details(info["interface"]) if kind == "WLAN" else {}
    dns = dns_servers()
    latency = internet_latency()
    data = {**info, "gateway": route["gateway"], "network": str(net) if net else None,
            "hosts": net.num_addresses - 2 if net and net.prefixlen < 31 else None,
            "link": kind or None, "dns": dns, "internet_ms": latency, "wifi": wifi}
    lines = [f"Verbindung: {kind or 'unbekannt'} über {info['interface']}",
             f"Eigene Adresse: {info['ip']}" + (f" im Netz {net} (Platz für {data['hosts']} Geräte)" if net else ""),
             f"Router/Gateway: {route['gateway'] or 'unbekannt'}",
             f"DNS-Server: {', '.join(dns) or 'unbekannt'}",
             f"Eigene MAC-Adresse: {info['mac'] or 'unbekannt'}",
             f"Internet: {'erreichbar, ' + str(latency) + ' ms' if latency is not None else 'NICHT erreichbar'}"]
    if wifi:
        lines.append("WLAN: " + ", ".join(x for x in (
            f"Name {wifi['ssid']}" if wifi.get("ssid") else
            "Name vom System verborgen (Ortungsdienste für Jarvis/Terminal nicht freigegeben)",
            f"Kanal {wifi['channel']}" if wifi.get("channel") else "",
            wifi.get("standard") or "",
            f"Signal {wifi['signal_dbm']} dBm ({signal_word(wifi['signal_dbm'])})" if wifi.get("signal_dbm") is not None else "",
            f"{wifi['rate_mbit']} Mbit/s Verbindungsrate" if wifi.get("rate_mbit") else "",
            f"Verschlüsselung {wifi['security']}" if wifi.get("security") else "",
            f"{wifi['nearby_networks']} weitere WLANs in Reichweite" if wifi.get("nearby_networks") else "") if x))
    if a.get("public_ip"):
        lines.append(f"Öffentliche Adresse: {public_ip() or 'nicht ermittelbar'}")
    return ToolResult(True, "\n".join(lines), data)


def public_ip() -> Optional[str]:
    import requests
    try:
        r = requests.get("https://api.ipify.org", timeout=6)
        ip = r.text.strip()
        ipaddress.ip_address(ip)
        return ip
    except (requests.RequestException, ValueError):
        return None


# ---------------------------------------------------------------- Geräte

def read_arp(iface: Optional[str] = None) -> dict[str, str]:
    """{ip: mac} aus der ARP-Tabelle; unvollständige und Sammeladressen entfallen."""
    out = _run(["arp", "-an"], 8)
    table = {}
    for m in re.finditer(r"\(([\d.]+)\) at ([0-9a-fA-F:]+) on (\S+)", out):
        mac = norm_mac(m.group(2))
        if not mac or mac == "ff:ff:ff:ff:ff:ff" or (iface and m.group(3) != iface):
            continue
        try:
            ip = ipaddress.ip_address(m.group(1))
        except ValueError:
            continue
        if ip.is_multicast or m.group(1).endswith(".255"):
            continue
        table[m.group(1)] = mac
    return table


def sweep(net: ipaddress.IPv4Network, own_ip: str, cancel: Optional[threading.Event] = None) -> int:
    """Weckt das Netz: ein leeres UDP-Paket je Adresse lässt das System per ARP nach dem Gerät fragen.
    Es wird kein Dienst angesprochen (Port 9 = discard). Große Netze: nur der eigene /24-Bereich."""
    if net.num_addresses - 2 > MAX_SWEEP_HOSTS:
        net = ipaddress.IPv4Network(f"{own_ip}/24", strict=False)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    sent = 0
    try:
        for host in net.hosts():
            if cancel is not None and cancel.is_set():
                break
            try:
                sock.sendto(b"", (str(host), 9))
                sent += 1
            except OSError:
                continue
            if sent % 64 == 0:
                time.sleep(0.02)
    finally:
        sock.close()
    return sent


def _reverse_name(ip: str) -> str:
    try:
        return socket.gethostbyaddr(ip)[0]
    except (OSError, UnicodeError):
        return ""


def resolve_names(ips: list[str], timeout: float = 4.0) -> dict[str, str]:
    """Rückwärtsauflösung (DNS/Bonjour) parallel, mit hartem Zeitlimit."""
    names: dict[str, str] = {}
    if not ips:
        return names
    pool = ThreadPoolExecutor(max_workers=min(32, len(ips)), thread_name_prefix="jarvis-rdns")
    futures = {pool.submit(_reverse_name, ip): ip for ip in ips}
    deadline = time.monotonic() + timeout
    for fut, ip in futures.items():
        try:
            name = fut.result(timeout=max(0.0, deadline - time.monotonic()))
        except Exception:  # noqa: BLE001 — Zeitlimit oder Auflösungsfehler: Gerät bleibt namenlos
            name = ""
        if name and name != ip:
            names[ip] = clean(name.removesuffix(".local").removesuffix(".fritz.box"), 40)
    pool.shutdown(wait=False, cancel_futures=True)
    return names


class Vendors:
    """Herstellerpräfixe (IEEE-OUI) aus einer einmal heruntergeladenen, lokal gecachten Liste."""

    def __init__(self, data_dir: Path, enabled: bool = True):
        self.path = Path(data_dir) / "oui.txt"
        self.enabled = enabled
        self._map: Optional[dict[str, str]] = None

    def _download(self) -> None:
        import requests
        try:
            r = requests.get(OUI_URL, timeout=20, headers={"User-Agent": "Jarvis/1.0"})
            r.raise_for_status()
            if len(r.content) > 200_000:
                self.path.write_bytes(r.content)
        except (requests.RequestException, OSError) as exc:
            log.info("Herstellerliste nicht ladbar: %s", exc)

    def _load(self) -> dict[str, str]:
        if self._map is not None:
            return self._map
        self._map = {}
        if not self.enabled:
            return self._map
        try:
            stale = time.time() - self.path.stat().st_mtime > OUI_MAX_AGE_S
        except OSError:
            stale = True
        if stale:
            self._download()
        try:
            text = self.path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return self._map
        for line in text.splitlines():
            if not line or line.startswith("#"):
                continue
            cols = [c.strip() for c in line.split("\t")]
            if len(cols) < 2 or len(cols[0]) != 8:          # nur 24-Bit-Präfixe (AA:BB:CC)
                continue
            self._map[cols[0].lower()] = clean(cols[2] if len(cols) > 2 and cols[2] else cols[1], 40)
        return self._map

    def lookup(self, mac: str) -> str:
        if is_private_mac(mac):
            return ""
        return self._load().get(mac[:8], "")


class DeviceBook:
    """Bekannte Geräte je Netz (Schlüssel: MAC des Routers) mit erstem/letztem Auftreten und Namen."""

    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / "network_devices.json"
        self._lock = threading.Lock()

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, data: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def update(self, net_key: str, devices: list[dict]) -> list[dict]:
        """Trägt die Sichtung ein, ergänzt label/first_seen/new. Das erste Abbild eines Netzes gilt als bekannt."""
        now = time.strftime("%Y-%m-%d %H:%M")
        with self._lock:
            data = self._read()
            first_scan = net_key not in data
            known = data.setdefault(net_key, {})
            for d in devices:
                entry = known.get(d["mac"])
                d["new"] = entry is None and not first_scan
                if entry is None:
                    entry = known[d["mac"]] = {"first_seen": now, "label": ""}
                entry.update(last_seen=now, ip=d["ip"], name=d.get("name") or entry.get("name", ""),
                             vendor=d.get("vendor") or entry.get("vendor", ""))
                d["label"] = entry.get("label", "")
                d["first_seen"] = entry["first_seen"]
            self._write(data)
        return devices

    def label(self, ident: str, label: str) -> Optional[dict]:
        ident = ident.strip().lower()
        mac = norm_mac(ident)
        with self._lock:
            data = self._read()
            for known in data.values():
                for m, entry in known.items():
                    if m == mac or entry.get("ip") == ident or (entry.get("name") or "").lower() == ident:
                        entry["label"] = clean(label, 40)
                        self._write(data)
                        return {"mac": m, **entry}
        return None


def scan_devices(ctx: ToolContext, *, active: bool = True) -> dict:
    """Gemeinsamer Scan für Werkzeug und Hintergrundwächter."""
    route = default_route()
    info = interface_info(route["interface"])
    net = local_network(info)
    if not info["ip"] or net is None:
        raise ToolError("Keine Netzwerkverbindung — ich kann das Netz nicht durchsuchen.")
    partial = net.num_addresses - 2 > MAX_SWEEP_HOSTS
    if active:
        sweep(net, info["ip"], ctx.cancel)
        time.sleep(1.5)                              # ARP-Antworten abwarten
    table = {ip: mac for ip, mac in read_arp(info["interface"]).items()
             if ipaddress.ip_address(ip) in net}
    names = resolve_names(list(table))
    net_cfg = getattr(ctx.cfg, "network", None)
    vendors: Vendors = ctx.service("oui", lambda: Vendors(
        ctx.data_dir, enabled=bool(getattr(net_cfg, "vendor_lookup", True))))
    devices = []
    for ip, mac in sorted(table.items(), key=lambda kv: ipaddress.ip_address(kv[0])):
        devices.append({"ip": ip, "mac": mac, "name": names.get(ip, ""), "vendor": vendors.lookup(mac),
                        "private_mac": is_private_mac(mac), "role": "Router" if ip == route["gateway"] else ""})
    if info["mac"]:
        devices.append({"ip": info["ip"], "mac": info["mac"], "name": clean(socket.gethostname().removesuffix(".local"), 40),
                        "vendor": vendors.lookup(info["mac"]), "private_mac": is_private_mac(info["mac"]),
                        "role": "dieser Mac"})
    gw_mac = next((d["mac"] for d in devices if d["role"] == "Router"), "") or f"net:{net}"
    book: DeviceBook = ctx.service("device_book", lambda: DeviceBook(ctx.data_dir))
    devices = book.update(gw_mac, devices)
    return {"network": str(net), "gateway": route["gateway"], "partial": partial, "devices": devices}


def describe_device(d: dict) -> str:
    what = d.get("label") or d.get("name") or "ohne Namen"
    extra = [x for x in (d.get("role"), d.get("vendor"),
                         "private (zufällige) Adresse, meist Handy/Tablet"
                         if d.get("private_mac") and not d.get("vendor") and not d.get("role") else "",
                         "NEU" if d.get("new") else "") if x]
    return f"{d['ip']:<15} {what}" + (f" — {', '.join(extra)}" if extra else "") + f" [{d['mac']}]"


def network_devices(ctx: ToolContext, a: dict) -> ToolResult:
    res = scan_devices(ctx, active=a.get("active", True) is not False)
    devs = res["devices"]
    new = [d for d in devs if d.get("new")]
    head = f"{len(devs)} Geräte im Netz {res['network']} (Router {res['gateway']})"
    if res["partial"]:
        head += " — großes Netz: aktiv durchsucht wurde nur der eigene /24-Bereich"
    shown = devs
    if len(devs) > MAX_LISTED:
        # Sehr große (geteilte) Netze: Router, dieser Mac, benannte und neue Geräte zuerst, dann auffüllen.
        key = [d for d in devs if d.get("role") or d.get("label") or d.get("name") or d.get("new")]
        rest = [d for d in devs if d not in key]
        shown = (key + rest)[:MAX_LISTED]
    lines = [head + ":"] + [describe_device(d) for d in shown]
    if len(shown) < len(devs):
        anon = sum(1 for d in devs if d.get("private_mac") and not d.get("label"))
        lines.append(f"… und {len(devs) - len(shown)} weitere. Insgesamt {anon} Geräte mit privater (zufälliger) Adresse, "
                     "also vor allem Handys und Tablets. So ein großes Netz ist vermutlich ein geteiltes Netz, kein Heimnetz.")
    if new:
        lines.append(f"Neu seit dem letzten Scan ({len(new)}): {', '.join(d['ip'] for d in new[:15])}"
                     + (" …" if len(new) > 15 else ""))
    lines.append("Hinweis: Geräte im Tiefschlaf oder mit Firewall antworten nicht immer.")
    return ToolResult(True, "\n".join(lines), {**res, "count": len(devs), "new": len(new)})


def network_name_device(ctx: ToolContext, a: dict) -> ToolResult:
    book: DeviceBook = ctx.service("device_book", lambda: DeviceBook(ctx.data_dir))
    entry = book.label(str(a.get("device", "")), str(a.get("label", "")))
    if entry is None:
        raise ToolError("Dieses Gerät kenne ich nicht — zuerst network_devices ausführen und IP oder MAC-Adresse angeben.")
    return ToolResult(True, f"Gemerkt: {entry.get('ip')} [{entry['mac']}] heißt jetzt „{entry['label']}“.")


# -------------------------------------------------------- Geschwindigkeit

def network_speedtest(ctx: ToolContext, a: dict) -> ToolResult:
    """Apples networkQuality: Download, Upload, Reaktionsfähigkeit (dauert rund 15–25 s)."""
    raw = _run(["networkQuality", "-c"], 60)
    try:
        d = json.loads(raw)
    except ValueError as exc:
        raise ToolError("Die Geschwindigkeitsmessung lieferte kein Ergebnis (networkQuality fehlt oder keine Verbindung).") from exc
    down = round(float(d.get("dl_throughput") or 0) / 1e6, 1)
    up = round(float(d.get("ul_throughput") or 0) / 1e6, 1)
    rpm = d.get("responsiveness")
    rtt = d.get("base_rtt")
    if not down and not up:
        raise ToolError("Die Geschwindigkeitsmessung brach ohne Messwerte ab.")
    text = f"Download {down} Mbit/s, Upload {up} Mbit/s"
    if rtt:
        text += f", Grundlatenz {round(float(rtt))} ms"
    if rpm:
        rpm = round(float(rpm))
        text += f", Reaktionsfähigkeit {rpm} RPM ({'hoch' if rpm >= 1000 else 'mittel' if rpm >= 300 else 'niedrig'})"
    return ToolResult(True, text + ".", {"download_mbit": down, "upload_mbit": up, "rtt_ms": rtt, "rpm": rpm})


# ------------------------------------------------------------------ Ports

def _port_target(a: dict) -> str:
    host = str(a.get("host", "")).strip()
    try:
        ip = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ToolError("host muss eine IP-Adresse aus dem eigenen Netz sein (z. B. 192.168.178.20)") from exc
    if not (ip.is_private or ip.is_loopback) or ip.is_multicast or ip.version != 4:
        raise ToolError("Ports prüfe ich nur an privaten Adressen im eigenen Netz, nicht im Internet.")
    return host


def _assess_ports(ctx: ToolContext, a: dict) -> Assessment:
    host = _port_target(a)
    if ctx.tainted:
        return Assessment(CONFIRM, f"Nach Lesen fremder Inhalte: offene Ports von {host} prüfen")
    return Assessment(ALLOW)


def _open(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def network_ports(ctx: ToolContext, a: dict) -> ToolResult:
    host = _port_target(a)
    ports = a.get("ports") or sorted(COMMON_PORTS)
    ports = sorted({int(p) for p in ports if 0 < int(p) < 65536})[:200]
    with ThreadPoolExecutor(max_workers=32, thread_name_prefix="jarvis-ports") as pool:
        state = list(pool.map(lambda p: _open(host, p, 0.8), ports))
    found = [p for p, ok in zip(ports, state) if ok]
    if not found:
        return ToolResult(True, f"An {host} ist keiner der {len(ports)} geprüften Ports offen "
                                "(Gerät aus, schlafend oder durch Firewall geschützt).", {"host": host, "open": []})
    lines = [f"Offene Ports an {host} ({len(ports)} geprüft):"] + [
        f"{p} — {COMMON_PORTS.get(p, 'unbekannter Dienst')}" for p in found]
    return ToolResult(True, "\n".join(lines), {"host": host, "open": found})


def tools() -> list[Tool]:
    S, B = {"type": "string"}, {"type": "boolean"}
    return [
        Tool("network_overview", "network",
             "Aufbau des eigenen Netzwerks: Verbindungsart, eigene IP, Netzbereich, Router, DNS, WLAN-Kanal/Signal, "
             "Internet-Erreichbarkeit. public_ip=true nur setzen, wenn Sir ausdrücklich nach der öffentlichen Adresse fragt.",
             {"public_ip": B}, (), network_overview,
             announce=lambda a: "Ich sehe mir das Netzwerk an, Sir."),
        Tool("network_devices", "network",
             "Listet alle Geräte im Heimnetz mit IP, Name, Hersteller und MAC; markiert Router, diesen Mac und neue Geräte.",
             {"active": {**B, "description": "false = nur bereits bekannte Nachbarn, ohne das Netz abzufragen"}}, (),
             network_devices, announce=lambda a: "Ich durchsuche das Netz nach Geräten, Sir."),
        Tool("network_name_device", "network",
             "Gibt einem gefundenen Gerät einen eigenen Namen (device = IP oder MAC, label = z. B. „Fernseher“).",
             {"device": S, "label": S}, ("device", "label"), network_name_device),
        Tool("network_speedtest", "network", "Misst Download, Upload und Latenz der Internetverbindung (ca. 20 s).",
             {}, (), network_speedtest, announce=lambda a: "Ich messe die Geschwindigkeit, Sir. Einen Moment."),
        Tool("network_ports", "network",
             "Prüft, welche Dienste (Ports) an einem Gerät im eigenen Netz erreichbar sind. Nur private IP-Adressen.",
             {"host": S, "ports": {"type": "array", "items": {"type": "integer"}}}, ("host",), network_ports,
             _assess_ports, announce=lambda a: f"Ich prüfe die Dienste an {a.get('host', '')}, Sir."),
    ]
