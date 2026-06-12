#!/usr/bin/env python3
"""
Bulkhead — Web GUI
Subsea tools & timezone sanity for the North Atlantic EE.
Flask backend. Most logic lives client-side for instant interactivity.
"""

from flask import Flask, render_template, jsonify, request
import math
import threading
import time
import json
import os
import ssl

import websocket

TV_IP = "192.168.2.71"
TV_NAME = "Bulkhead"
TV_TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".tv_token")

# Cached WebSocket connection and auth token
_tv_ws = None
_tv_token = None
_tv_lock = threading.Lock()
_pairing_in_progress = False

# Load saved token if it exists
if os.path.exists(TV_TOKEN_FILE):
    try:
        with open(TV_TOKEN_FILE) as f:
            _tv_token = f.read().strip()
    except Exception:
        pass

def _get_tv_ws():
    """Get or create a WebSocket connection to the TV, handling pairing."""
    global _tv_ws, _tv_token, _pairing_in_progress

    if _tv_ws is not None:
        try:
            _tv_ws.ping()
            return _tv_ws
        except Exception:
            _tv_ws = None

    with _tv_lock:
        if _tv_ws is not None:
            try:
                _tv_ws.ping()
                return _tv_ws
            except Exception:
                _tv_ws = None

        import urllib.parse
        name_encoded = urllib.parse.quote(TV_NAME)
        url = f"wss://{TV_IP}:8002/api/v2/channels/samsung.remote.control?name={name_encoded}"

        # Include token in URL if we have one
        if _tv_token:
            url += "&token=" + _tv_token

        ws = websocket.create_connection(url, sslopt={"cert_reqs": ssl.CERT_NONE}, timeout=5)

        # Read events until we get something meaningful
        event = None
        response = None
        while event is None or event in ("ms.channel.ready",):
            data = ws.recv()
            response = json.loads(data)
            event = response.get("event", "")

        if event == "ms.channel.connect":
            # Successfully connected (already paired)
            new_token = response.get("data", {}).get("token")
            if new_token:
                _tv_token = new_token
                try:
                    with open(TV_TOKEN_FILE, "w") as f:
                        f.write(new_token)
                except Exception:
                    pass
            _tv_ws = ws
            return ws

        elif event == "ms.channel.unauthorized":
            challenge_token = response.get("data", {}).get("token", "")
            # Send pairing request — this triggers the on-TV prompt
            pairing_msg = json.dumps({
                "method": "ms.channel.connect",
                "params": {"token": challenge_token}
            })
            ws.send(pairing_msg)

            # Wait for the user to accept on the TV
            _pairing_in_progress = True
            ws.settimeout(30)  # 30 seconds for user to accept

            try:
                data = ws.recv()
                response = json.loads(data)
                event = response.get("event", "")

                if event == "ms.channel.connect":
                    new_token = response.get("data", {}).get("token")
                    if new_token:
                        _tv_token = new_token
                        try:
                            with open(TV_TOKEN_FILE, "w") as f:
                                f.write(new_token)
                        except Exception:
                            pass
                    _tv_ws = ws
                    ws.settimeout(5)
                    _pairing_in_progress = False
                    return ws
                else:
                    ws.close()
                    _pairing_in_progress = False
                    raise Exception(f"Pairing failed — TV returned: {event}")
            except websocket.WebSocketTimeoutException:
                ws.close()
                _pairing_in_progress = False
                raise Exception("Pairing timed out — did you accept the prompt on the TV?")
        else:
            ws.close()
            raise Exception(f"Unexpected TV response: {event}")

def _is_pairing():
    return _pairing_in_progress

def _send_tv_key(key, cmd="Click"):
    """Send a key press via WebSocket."""
    ws = _get_tv_ws()
    msg = json.dumps({
        "method": "ms.remote.control",
        "params": {
            "Cmd": cmd,
            "DataOfCmd": key,
            "Option": "false",
            "TypeOfRemote": "SendRemoteKey"
        }
    })
    ws.send(msg)

def _launch_tv_app(app_id):
    """Launch an app on the TV."""
    ws = _get_tv_ws()
    msg = json.dumps({
        "method": "ms.channel.emit",
        "params": {
            "event": "ed.apps.launch",
            "to": "host",
            "data": {
                "appId": app_id,
                "action_type": "NATIVE_LAUNCH"
            }
        }
    })
    ws.send(msg)

app = Flask(__name__)

# ── Constants ──────────────────────────────────────────────────────
SALT_PSI_PER_FT = 0.445
FRESH_PSI_PER_FT = 0.433
FT_PER_M = 3.28084

COLOR_BANDS = [
    {"name": "black",  "digit": 0, "multiplier": 1,       "hex": "#1a1a2e", "text": "#888"},
    {"name": "brown",  "digit": 1, "multiplier": 10,      "hex": "#8B4513", "text": "#fff"},
    {"name": "red",    "digit": 2, "multiplier": 100,     "hex": "#CC0000", "text": "#fff"},
    {"name": "orange", "digit": 3, "multiplier": 1000,    "hex": "#FF8C00", "text": "#000"},
    {"name": "yellow", "digit": 4, "multiplier": 10000,   "hex": "#FFD700", "text": "#000"},
    {"name": "green",  "digit": 5, "multiplier": 100000,  "hex": "#228B22", "text": "#fff"},
    {"name": "blue",   "digit": 6, "multiplier": 1000000, "hex": "#4169E1", "text": "#fff"},
    {"name": "violet", "digit": 7, "multiplier": 10000000,"hex": "#8B008B", "text": "#fff"},
    {"name": "grey",   "digit": 8, "multiplier": 100000000,"hex": "#808080", "text": "#fff"},
    {"name": "white",  "digit": 9, "multiplier": 1000000000,"hex": "#F5F5F5","text": "#000"},
    {"name": "gold",   "digit": -1,"multiplier": 0.1,     "hex": "#DAA520", "text": "#000"},
    {"name": "silver", "digit": -2,"multiplier": 0.01,    "hex": "#C0C0C0", "text": "#000"},
]

TOLERANCE_MAP = {"brown": 1, "red": 2, "green": 0.5, "blue": 0.25, "violet": 0.1, "grey": 0.05, "gold": 5, "silver": 10}
TCR_MAP = {"brown": 100, "red": 50, "orange": 15, "yellow": 25, "blue": 10, "violet": 5}


def format_si(value, unit="Ω"):
    if value == 0:
        return f"0 {unit}"
    for scale, prefix in [(1e12, "T"), (1e9, "G"), (1e6, "M"), (1e3, "k"),
                           (1, ""), (1e-3, "m"), (1e-6, "µ"), (1e-9, "n")]:
        if abs(value) >= scale:
            v = value / scale
            if v >= 100:    return f"{v:.0f} {prefix}{unit}"
            elif v >= 10:   return f"{v:.1f} {prefix}{unit}"
            else:           return f"{v:.2f} {prefix}{unit}"
    return f"{value:.2f} {unit}"


# ── Routes ─────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/depth")
def api_depth():
    """Calculate pressure from depth."""
    try:
        meters = float(request.args.get("meters", 0))
        water = request.args.get("water", "salt")
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid depth"}), 400

    psi_per_ft = SALT_PSI_PER_FT if water == "salt" else FRESH_PSI_PER_FT
    depth_ft = meters * FT_PER_M
    psi = depth_ft * psi_per_ft
    atm = (psi / 14.6959) + 1
    bar = psi * 0.0689476
    kpa = bar * 100

    return jsonify({
        "meters": meters,
        "water": water,
        "psi": round(psi, 1),
        "atm": round(atm, 1),
        "bar": round(bar, 2),
        "kpa": round(kpa, 1),
        "gauge_atm": round(atm - 1, 1),
    })


@app.route("/api/resistor/decode")
def api_resistor_decode():
    """Decode resistor colors to value."""
    colors_str = request.args.get("colors", "")
    bands = int(request.args.get("bands", 4))
    colors = [c.strip().lower() for c in colors_str.split(",") if c.strip()]

    if len(colors) != bands:
        return jsonify({"error": f"Expected {bands} colors, got {len(colors)}"}), 400

    color_lookup = {c["name"]: c for c in COLOR_BANDS}

    try:
        if bands == 4:
            d1 = color_lookup[colors[0]]["digit"]
            d2 = color_lookup[colors[1]]["digit"]
            mult = color_lookup[colors[2]]["multiplier"]
            tol = TOLERANCE_MAP.get(colors[3], 20)
            value = (d1 * 10 + d2) * mult
            tcr = None
        else:  # 5 or 6 band
            d1 = color_lookup[colors[0]]["digit"]
            d2 = color_lookup[colors[1]]["digit"]
            d3 = color_lookup[colors[2]]["digit"]
            mult = color_lookup[colors[3]]["multiplier"]
            tol = TOLERANCE_MAP.get(colors[4], 20)
            value = (d1 * 100 + d2 * 10 + d3) * mult
            tcr = TCR_MAP.get(colors[5]) if bands == 6 else None
    except KeyError as e:
        return jsonify({"error": f"Unknown color: {e}"}), 400

    if d1 < 0 or d2 < 0 or (bands >= 5 and d3 < 0):
        return jsonify({"error": "Gold/silver not valid for digit bands"}), 400

    # Check E-series
    e24 = [1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0,
           3.3, 3.6, 3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1]
    e12 = [1.0, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2]

    norm = value
    while norm >= 10:
        norm /= 10
    while norm < 1:
        norm *= 10
    norm = round(norm, 2)
    eseries = "E24" if norm in e24 else "E12" if norm in e12 else None

    return jsonify({
        "value": value,
        "value_formatted": format_si(value),
        "tolerance": tol,
        "tcr": tcr,
        "range_low": round(value * (1 - tol/100), 2),
        "range_high": round(value * (1 + tol/100), 2),
        "eseries": eseries,
        "colors": colors,
    })


@app.route("/api/resistor/lookup")
def api_resistor_lookup():
    """Reverse lookup: value → colors."""
    val_str = request.args.get("value", "").strip().lower()
    if not val_str:
        return jsonify({"error": "No value provided"}), 400

    # Parse value
    suffixes = {"k": 1e3, "m": 1e6, "g": 1e9, "r": 1, "ω": 1}
    val_clean = val_str.replace(" ", "").replace("ohms", "").replace("ohm", "")

    value = None
    for suffix, mult in suffixes.items():
        if val_clean.endswith(suffix):
            try:
                value = float(val_clean[:-len(suffix)]) * mult
            except ValueError:
                pass
            break
    if value is None:
        try:
            value = float(val_clean)
        except ValueError:
            return jsonify({"error": "Cannot parse value"}), 400

    if value <= 0:
        return jsonify({"error": "Value must be positive"}), 400

    # Find 4-band representation
    if value < 0.01:
        return jsonify({"error": "Value too small"}), 400

    if value >= 1e11:
        return jsonify({"error": "Value too large"}), 400

    if value < 1:
        if value >= 0.1:
            mult_color = "gold"
            sig_val = value * 10
        else:
            mult_color = "silver"
            sig_val = value * 100
        d1 = int(sig_val)
        d2 = int(round((sig_val - d1) * 10))
        if d2 == 10:
            d1 += 1
            d2 = 0
    else:
        exp = 0
        v = value
        while v >= 100:
            v /= 10
            exp += 1
        d1 = int(v / 10)
        d2 = int(round(v % 10))
        if d2 == 10:
            d1 += 1
            d2 = 0
        if d1 >= 10:
            d1 = 1
            d2 = 0
            exp += 1
        exp_map = {0: "black", 1: "brown", 2: "red", 3: "orange", 4: "yellow",
                   5: "green", 6: "blue", 7: "violet", 8: "grey", 9: "white"}
        mult_color = exp_map.get(exp)
        if mult_color is None:
            return jsonify({"error": "Value out of range"}), 400

    digit_lookup = {c["digit"]: c["name"] for c in COLOR_BANDS if c["digit"] >= 0}
    c1 = digit_lookup.get(d1)
    c2 = digit_lookup.get(d2)
    if c1 is None or c2 is None:
        return jsonify({"error": "Cannot encode digits"}), 400

    actual_value = (d1 * 10 + d2) * next(c["multiplier"] for c in COLOR_BANDS if c["name"] == mult_color)
    error_pct = abs(actual_value - value) / value * 100 if value > 0 else 0

    return jsonify({
        "colors_4band": [c1, c2, mult_color, "gold"],
        "value_display": format_si(value),
        "actual_value": format_si(actual_value),
        "error_pct": round(error_pct, 2),
    })


@app.route("/api/color_data")
def api_color_data():
    """Return resistor color data for client-side use."""
    return jsonify({
        "bands": COLOR_BANDS,
        "tolerance": TOLERANCE_MAP,
        "tcr": TCR_MAP,
    })


@app.route("/api/tv/status")
def api_tv_status():
    """Get TV device info via REST API (works even when TV is in standby, no auth needed)."""
    import requests as req
    try:
        r = req.get(f"http://{TV_IP}:8001/api/v2/", timeout=3)
        device = r.json().get("device", {})
        # Check WebSocket connection separately
        ws_connected = _tv_ws is not None
        pairing = _is_pairing()
        return jsonify({
            "model": device.get("modelName", "unknown"),
            "power": device.get("PowerState", "unknown"),
            "os": device.get("OS", "unknown"),
            "resolution": device.get("Resolution", "unknown"),
            "network_type": device.get("NetworkType", "unknown"),
            "connected": ws_connected,
            "pairing": pairing,
        })
    except Exception as e:
        return jsonify({"error": str(e), "power": "unknown"}), 500


@app.route("/api/tv/key", methods=["POST"])
def api_tv_key():
    """Send a remote key press to the TV."""
    import requests as req

    data = request.get_json(force=True, silent=True) or {}
    key = data.get("key", "").upper()
    if not key:
        return jsonify({"error": "No key provided"}), 400

    # Check if TV is on first
    try:
        r = req.get(f"http://{TV_IP}:8001/api/v2/", timeout=2)
        power = r.json().get("device", {}).get("PowerState", "unknown")
        if power != "on":
            return jsonify({
                "ok": False,
                "error": f"TV is in {power} mode. Turn it on first.",
                "power": power
            })
    except Exception:
        return jsonify({"ok": False, "error": "TV unreachable — is it on the network?"})

    # TV is on, try WebSocket
    try:
        if key == "POWER":
            _send_tv_key("KEY_POWER")
            time.sleep(0.1)
            _send_tv_key("KEY_POWER")
        elif key == "HOME":
            _send_tv_key("KEY_HOME")
        elif key == "RETURN":
            _send_tv_key("KEY_RETURN")
        elif key == "EXIT":
            _send_tv_key("KEY_EXIT")
        elif key == "SETTINGS":
            _send_tv_key("KEY_MENU")
        elif key == "NETFLIX":
            _launch_tv_app("3201807016597")
        elif key == "YOUTUBE":
            _launch_tv_app("2997603")
        elif key == "AMAZON":
            _launch_tv_app("3201512006785")
        elif key == "DISNEY":
            _launch_tv_app("3201901017640")
        else:
            ws_key = f"KEY_{key}" if not key.startswith("KEY_") else key
            _send_tv_key(ws_key)
        return jsonify({"ok": True, "key": key})
    except Exception as e:
        err = str(e)
        if "Pairing timed out" in err:
            return jsonify({"ok": False, "error": "Pairing timed out — accept the prompt on the TV screen and try again"})
        elif "Pairing failed" in err:
            return jsonify({"ok": False, "error": err})
        return jsonify({"ok": False, "error": f"TV error: {err}"})


@app.route("/api/tv/apps")
def api_tv_apps():
    """Get installed apps list."""
    try:
        ws = _get_tv_ws()
        msg = json.dumps({
            "method": "ms.channel.emit",
            "params": {"event": "ed.installedApp.get", "to": "host"}
        })
        ws.send(msg)
        return jsonify({"ok": True, "data": "requested"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/pcb/trace")
def api_pcb_trace():
    """IPC-2221 trace width calculator."""
    try:
        current = float(request.args.get("current", 1))
        temp_rise = float(request.args.get("temp_rise", 10))
        copper_oz = float(request.args.get("copper", 1))
        layer = request.args.get("layer", "external")
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid parameters"}), 400

    if current <= 0 or temp_rise <= 0 or copper_oz <= 0:
        return jsonify({"error": "All values must be positive"}), 400

    # IPC-2221 constants
    if layer == "external":
        k = 0.048
        b = 0.44
        c = 0.725
        label = "External"
    else:
        k = 0.024
        b = 0.44
        c = 0.725
        label = "Internal"

    # Cross-sectional area in square mils
    area_sq_mils = (current / (k * (temp_rise ** b))) ** (1 / c)

    # Copper thickness: 1 oz = 1.37 mils
    thickness_mils = copper_oz * 1.37
    width_mils = area_sq_mils / thickness_mils
    width_mm = width_mils * 0.0254

    # Resistance and voltage drop (1 inch trace for reference)
    resistivity = 0.678  # µΩ-in for copper at 25°C, adjusted
    r_per_inch = resistivity / (width_mils * thickness_mils)  # Ω/inch
    r_per_inch = max(r_per_inch, 0.000001)  # avoid div by zero
    v_drop_per_inch = current * r_per_inch
    power_per_inch = current * v_drop_per_inch

    # Recommended width for common currents
    common_currents = {}
    for test_a in [0.5, 1, 2, 3, 5, 10]:
        test_area = (test_a / (k * (temp_rise ** b))) ** (1 / c)
        test_width = test_area / thickness_mils
        common_currents[f"{test_a}A"] = round(test_width, 2)

    return jsonify({
        "current": current,
        "temp_rise": temp_rise,
        "copper_oz": copper_oz,
        "layer": label,
        "width_mils": round(width_mils, 2),
        "width_mm": round(width_mm, 3),
        "resistance_per_inch": round(r_per_inch * 1000, 3),  # mΩ/in
        "v_drop_per_inch": round(v_drop_per_inch * 1000, 3),  # mV/in
        "power_per_inch": round(power_per_inch * 1000, 3),    # mW/in
        "recommended": common_currents,
    })


@app.route("/api/tv/wake", methods=["POST"])
def api_tv_wake():
    """Send Wake-on-LAN magic packet to the TV."""
    import struct
    import socket

    mac = "B8:B4:09:E0:18:E0"
    try:
        mac_bytes = bytes(int(b, 16) for b in mac.split(":"))
        magic = b"\xff" * 6 + mac_bytes * 16

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(magic, ("192.168.2.255", 9))
        sock.close()

        return jsonify({"ok": True, "message": "Wake packet sent — TV should be turning on"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ── Config ─────────────────────────────────────────────────────────

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

DEFAULT_CONFIG = {
    "tabs": {
        "clock":     {"enabled": True, "nav": "tools",       "label": "🕐 World Clock"},
        "depth":     {"enabled": True, "nav": "tools",       "label": "🌊 Depth ↔ Pressure"},
        "resistor":  {"enabled": True, "nav": "electronics", "label": "⚡ Resistor Decoder"},
        "tv":        {"enabled": True, "nav": "tools",       "label": "📺 TV Remote"},
        "pcb":       {"enabled": True, "nav": "electronics", "label": "🔌 PCB Trace"},
        "geo":       {"enabled": True, "nav": "tools",       "label": "🌍 GeoGuessr"},
        "battery":   {"enabled": True, "nav": "tools",       "label": "🔋 Battery Pack",     "iframe": "/static/battery-solver.html"},
        "gauge":     {"enabled": True, "nav": "electronics", "label": "🧮 Wire Gauge",       "iframe": "/static/wire-gauge.html"},
        "tline":     {"enabled": True, "nav": "electronics", "label": "〰️ T-Line Z"},
        "ohmslaw":   {"enabled": True, "nav": "electronics", "label": "🔌 Ohm's Law"},
        "rcalc":     {"enabled": True, "nav": "electronics", "label": "⫼ R / LED Calc"},
        "db":        {"enabled": True, "nav": "electronics", "label": "📶 dB/dBm"},
        "freq":      {"enabled": True, "nav": "electronics", "label": "∿ RC/LC/Freq"},
    },
    "navigation": {
        "electronics": "⚡ Electronics",
        "tools": "🛠️ Tools",
    },
    "timezones": [
        {"city": "St. John's", "tz": "America/St_Johns", "label": "NDT"},
        {"city": "Collingwood", "tz": "America/Toronto", "label": "ET"},
        {"city": "Vancouver", "tz": "America/Vancouver", "label": "PT"},
        {"city": "UTC", "tz": "UTC", "label": "UTC"},
    ]
}

def _load_config():
    if not os.path.exists(CONFIG_FILE):
        return json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
        # Deep merge: preserve all default keys, overlay saved values
        merged = json.loads(json.dumps(DEFAULT_CONFIG))
        if "tabs" in cfg:
            for k, v in cfg["tabs"].items():
                if k in merged["tabs"]:
                    if isinstance(v, dict):
                        merged["tabs"][k].update(v)
                    else:
                        merged["tabs"][k]["enabled"] = bool(v)
        if "timezones" in cfg:
            merged["timezones"] = cfg["timezones"]
        if "navigation" in cfg:
            merged["navigation"].update(cfg["navigation"])
        return merged
    except Exception:
        return json.loads(json.dumps(DEFAULT_CONFIG))

def _save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

@app.route("/api/config")
def api_get_config():
    return jsonify(_load_config())

@app.route("/api/config", methods=["POST"])
def api_set_config():
    try:
        new_cfg = request.get_json(force=True)
    except Exception:
        return jsonify({"error": "Invalid JSON"}), 400

    current = _load_config()
    if "tabs" in new_cfg:
        for k, v in new_cfg["tabs"].items():
            if k in current["tabs"]:
                current["tabs"][k]["enabled"] = bool(v)
    if "timezones" in new_cfg:
        if isinstance(new_cfg["timezones"], list) and len(new_cfg["timezones"]) > 0:
            current["timezones"] = new_cfg["timezones"]

    _save_config(current)
    return jsonify({"ok": True, "config": current})


# ── Main ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Bulkhead web GUI")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address")
    parser.add_argument("--port", type=int, default=5050, help="Port")
    parser.add_argument("--debug", action="store_true", help="Debug mode")
    args = parser.parse_args()
    print(f"🌊 Bulkhead → http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug)
