#!/usr/bin/env python3
"""GKCC Calibration Center v0.1.1.

A small local web service for guided Klipper and Happy Hare calibration.
The first release is intentionally conservative: it reads status, sends a
small allow-listed set of calibration actions, records operator results, and
produces a printable as-built HTML report. It does not edit printer.cfg or
Happy Hare configuration files.
"""
from __future__ import annotations

import copy
import html
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

APP_DIR = Path(__file__).resolve().parent
# Keep machine-specific configuration and calibration records outside the Git
# checkout. This lets Moonraker update the repository without reporting it as
# modified or replacing the operator's saved data.
LOCAL_ROOT = Path(
    os.environ.get(
        "GKCC_DATA_DIR",
        str(Path.home() / "printer_data" / "config" / "gkcc"),
    )
).expanduser().resolve()
CONFIG_PATH = LOCAL_ROOT / "config.json"
DEFAULT_CONFIG_PATH = APP_DIR / "config.default.json"
WORKFLOWS_PATH = APP_DIR / "workflows.json"
DATA_DIR = LOCAL_ROOT / "data"
PROFILE_PATH = DATA_DIR / "profile.json"
RECORDS_PATH = DATA_DIR / "records.json"
SNAPSHOT_PATH = DATA_DIR / "printer_snapshot.json"
INDEX_PATH = APP_DIR / "index.html"

_state_lock = threading.Lock()
_file_lock = threading.Lock()
_auth_lock = threading.Lock()
_command_lock = threading.Lock()
_tokens: Dict[str, Tuple[float, str]] = {}

_state: Dict[str, Any] = {
    "connected": False,
    "printer_state": "unknown",
    "state_message": "Starting",
    "print_state": "unknown",
    "filename": "",
    "homed_axes": "",
    "position": [0.0, 0.0, 0.0, 0.0],
    "extruder": {},
    "heater_bed": {},
    "sensors": {},
    "mmu": {},
    "available_objects": [],
    "last_update": 0.0,
    "last_action": "",
    "last_result": "",
    "error": None,
    "action_busy": False,
}

DEFAULT_PROFILE: Dict[str, Any] = {
    "printer": {
        "name": "Voron V2.1830",
        "serial": "V2.1830",
        "build_volume": "350 × 350 × 350 mm",
        "controller": "Klipper / Moonraker",
        "notes": ""
    },
    "mmu": {
        "type": "ERCF V2",
        "software": "Happy Hare",
        "gates": 9,
        "notes": ""
    },
    "filaments": [],
    "technician": "Jonathan Easton"
}

DEFAULT_RECORDS: Dict[str, Any] = {
    "version": 1,
    "created_at": None,
    "updated_at": None,
    "workflow_runs": [],
    "purge_matrix": {},
    "maintenance_history": []
}


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    encoded = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
    with _file_lock:
        temp.write_text(encoded + "\n", encoding="utf-8")
        os.replace(temp, path)


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return copy.deepcopy(default)


def ensure_files() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    cfg = read_json(CONFIG_PATH, {})
    profile = read_json(PROFILE_PATH, DEFAULT_PROFILE)
    profile.setdefault("printer", {})["name"] = profile.get("printer", {}).get("name") or cfg.get("printer_name", "Voron")
    atomic_write_json(PROFILE_PATH, profile)
    records = read_json(RECORDS_PATH, DEFAULT_RECORDS)
    if not records.get("created_at"):
        records["created_at"] = now_iso()
    records["updated_at"] = now_iso()
    atomic_write_json(RECORDS_PATH, records)


def config() -> Dict[str, Any]:
    cfg = read_json(CONFIG_PATH, {})
    cfg.setdefault("port", 7128)
    cfg.setdefault("moonraker_url", "http://127.0.0.1:7125")
    cfg.setdefault("screen_pin", "1830")
    cfg.setdefault("poll_seconds", 1.0)
    cfg.setdefault("allow_machine_actions", True)
    return cfg


def moonraker_request(path: str, method: str = "GET", payload: Optional[Any] = None, timeout: float = 15.0) -> Any:
    base = str(config()["moonraker_url"]).rstrip("/")
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read()
    decoded = json.loads(raw.decode("utf-8")) if raw else {}
    if isinstance(decoded, dict) and "error" in decoded:
        raise RuntimeError(str(decoded["error"]))
    return decoded


def object_list() -> List[str]:
    reply = moonraker_request("/printer/objects/list")
    result = reply.get("result", reply) if isinstance(reply, dict) else {}
    objects = result.get("objects", []) if isinstance(result, dict) else []
    return [str(item) for item in objects] if isinstance(objects, list) else []


def build_query(objects: List[str], include_config: bool = False) -> Dict[str, Any]:
    requested: Dict[str, Any] = {}
    for name in ("webhooks", "print_stats", "toolhead", "extruder", "heater_bed"):
        if name in objects:
            requested[name] = None
    for name in objects:
        low = name.lower()
        if low == "mmu" or low.startswith("mmu_") or "filament_switch_sensor" in low or "filament_motion_sensor" in low:
            requested[name] = None
        elif low in {"temperature_sensor chamber", "temperature_sensor 2nd_bed"}:
            requested[name] = None
    if include_config and "configfile" in objects:
        requested["configfile"] = ["config", "settings", "save_config_pending"]
    return requested


def query_status(include_config: bool = False) -> Dict[str, Any]:
    objects = object_list()
    requested = build_query(objects, include_config=include_config)
    reply = moonraker_request("/printer/objects/query", method="POST", payload={"objects": requested}, timeout=30.0)
    result = reply.get("result", reply) if isinstance(reply, dict) else {}
    status = result.get("status", {}) if isinstance(result, dict) else {}
    return {"objects": objects, "status": status, "eventtime": result.get("eventtime") if isinstance(result, dict) else None}


def refresh_status() -> None:
    try:
        reply = query_status(include_config=False)
        status = reply["status"]
        sensors: Dict[str, Any] = {}
        mmu: Dict[str, Any] = {}
        for name, value in status.items():
            low = name.lower()
            if low == "mmu" or low.startswith("mmu_"):
                mmu[name] = value
            if "sensor" in low or "encoder" in low:
                sensors[name] = value
        webhooks = status.get("webhooks", {})
        print_stats = status.get("print_stats", {})
        toolhead = status.get("toolhead", {})
        with _state_lock:
            _state.update({
                "connected": True,
                "printer_state": webhooks.get("state", "unknown"),
                "state_message": webhooks.get("state_message", webhooks.get("message", "")),
                "print_state": print_stats.get("state", "unknown"),
                "filename": print_stats.get("filename", ""),
                "homed_axes": toolhead.get("homed_axes", ""),
                "position": toolhead.get("position", [0.0, 0.0, 0.0, 0.0]),
                "extruder": status.get("extruder", {}),
                "heater_bed": status.get("heater_bed", {}),
                "sensors": sensors,
                "mmu": mmu,
                "available_objects": reply["objects"],
                "last_update": time.time(),
                "error": None,
            })
    except Exception as exc:
        with _state_lock:
            _state.update({"connected": False, "error": str(exc), "last_update": time.time()})


def status_worker() -> None:
    while True:
        refresh_status()
        time.sleep(max(0.5, float(config().get("poll_seconds", 1.0))))


def printer_is_idle() -> Tuple[bool, str]:
    with _state_lock:
        connected = bool(_state.get("connected"))
        pstate = str(_state.get("printer_state", "unknown"))
        print_state = str(_state.get("print_state", "unknown"))
    if not connected:
        return False, "Moonraker is not connected"
    if pstate != "ready":
        return False, "Klipper is not ready"
    if print_state in {"printing", "paused"}:
        return False, "A print is active"
    return True, "ready"


def run_gcode(script: str, action_name: str) -> Any:
    if not config().get("allow_machine_actions", True):
        raise RuntimeError("Machine actions are disabled in config.json")
    ok, reason = printer_is_idle()
    if not ok:
        raise RuntimeError(reason)
    if not _command_lock.acquire(blocking=False):
        raise RuntimeError("Another calibration command is still running")
    with _state_lock:
        _state["action_busy"] = True
        _state["last_action"] = action_name
        _state["last_result"] = "Running"
    try:
        result = moonraker_request("/printer/gcode/script", method="POST", payload={"script": script}, timeout=120.0)
        with _state_lock:
            _state["last_result"] = "Completed"
        return result
    finally:
        with _state_lock:
            _state["action_busy"] = False
        _command_lock.release()


def create_token(client_ip: str) -> str:
    token = secrets.token_urlsafe(24)
    with _auth_lock:
        _tokens[token] = (time.monotonic() + 900.0, client_ip)
    return token


def valid_token(token: Optional[str], client_ip: str) -> bool:
    if not token:
        return False
    now = time.monotonic()
    with _auth_lock:
        for key, (expiry, _) in list(_tokens.items()):
            if expiry < now:
                _tokens.pop(key, None)
        record = _tokens.get(token)
        if not record:
            return False
        expiry, bound_ip = record
        if expiry < now or bound_ip != client_ip:
            _tokens.pop(token, None)
            return False
        _tokens[token] = (now + 900.0, bound_ip)
        return True


def public_config() -> Dict[str, Any]:
    cfg = config()
    return {
        "port": cfg["port"],
        "moonraker_url": cfg["moonraker_url"],
        "printer_name": cfg.get("printer_name", "Voron"),
        "allow_machine_actions": bool(cfg.get("allow_machine_actions", True)),
        "version": "0.1.1",
    }


def capture_snapshot() -> Dict[str, Any]:
    reply = query_status(include_config=True)
    snapshot = {"captured_at": now_iso(), "objects": reply["objects"], "status": reply["status"]}
    atomic_write_json(SNAPSHOT_PATH, snapshot)
    add_record({
        "workflow_id": "printer_snapshot",
        "title": "Printer baseline snapshot",
        "completed_at": now_iso(),
        "results": {"snapshot_file": str(SNAPSHOT_PATH.name), "object_count": len(reply["objects"])},
        "notes": "Captured from live Klipper configuration through Moonraker."
    })
    return {"ok": True, "captured_at": snapshot["captured_at"], "object_count": len(reply["objects"])}


def add_record(record: Dict[str, Any]) -> None:
    records = read_json(RECORDS_PATH, DEFAULT_RECORDS)
    records.setdefault("workflow_runs", []).append(record)
    records["updated_at"] = now_iso()
    atomic_write_json(RECORDS_PATH, records)


def save_run(payload: Dict[str, Any]) -> Dict[str, Any]:
    workflow_id = str(payload.get("workflow_id", "")).strip()
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", workflow_id):
        raise ValueError("Invalid workflow_id")
    results = payload.get("results", {})
    notes = str(payload.get("notes", ""))[:10000]
    record = {
        "workflow_id": workflow_id,
        "title": str(payload.get("title", workflow_id))[:200],
        "completed_at": now_iso(),
        "results": results if isinstance(results, dict) else {},
        "notes": notes,
    }
    add_record(record)
    return {"ok": True, "record": record}


def report_html() -> bytes:
    profile = read_json(PROFILE_PATH, DEFAULT_PROFILE)
    records = read_json(RECORDS_PATH, DEFAULT_RECORDS)
    snapshot = read_json(SNAPSHOT_PATH, {})
    printer = profile.get("printer", {})
    mmu = profile.get("mmu", {})
    runs = records.get("workflow_runs", [])

    def esc(value: Any) -> str:
        return html.escape(str(value if value is not None else ""))

    run_rows = []
    for run in runs:
        result_text = json.dumps(run.get("results", {}), ensure_ascii=False, indent=2)
        run_rows.append(
            "<section class='record'><h3>{}</h3><div class='meta'>{}</div><pre>{}</pre><p>{}</p></section>".format(
                esc(run.get("title", run.get("workflow_id", "Calibration"))),
                esc(run.get("completed_at", "")),
                esc(result_text),
                esc(run.get("notes", "")),
            )
        )
    if not run_rows:
        run_rows.append("<p>No completed calibration records yet.</p>")

    config_status = snapshot.get("status", {}).get("configfile", {}) if isinstance(snapshot, dict) else {}
    captured = snapshot.get("captured_at", "Not captured") if isinstance(snapshot, dict) else "Not captured"
    doc = f"""<!doctype html><html><head><meta charset='utf-8'><title>{esc(printer.get('name','Voron'))} Calibration Manual</title>
<style>
body{{font:15px system-ui,-apple-system,Segoe UI,sans-serif;color:#1d232b;max-width:980px;margin:35px auto;padding:0 24px;line-height:1.45}}
h1{{font-size:34px;margin-bottom:4px}}h2{{border-bottom:2px solid #222;padding-bottom:6px;margin-top:34px}}
.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}}.card,.record{{border:1px solid #b8bec7;border-radius:10px;padding:14px;break-inside:avoid}}
.meta{{color:#5d6672;font-size:13px}}pre{{white-space:pre-wrap;background:#f3f5f7;padding:10px;border-radius:8px;font-size:12px}}
.status{{font-weight:700}}@media print{{body{{margin:0;max-width:none}}button{{display:none}}}}
</style></head><body>
<button onclick='print()'>Print / Save as PDF</button>
<h1>{esc(printer.get('name','Voron'))}</h1><div class='meta'>Configuration and Calibration Record · Generated {esc(now_iso())}</div>
<h2>As-built overview</h2><div class='grid'>
<div class='card'><strong>Printer</strong><br>Serial: {esc(printer.get('serial'))}<br>Build volume: {esc(printer.get('build_volume'))}<br>Controller: {esc(printer.get('controller'))}</div>
<div class='card'><strong>MMU</strong><br>Type: {esc(mmu.get('type'))}<br>Software: {esc(mmu.get('software'))}<br>Gates: {esc(mmu.get('gates'))}</div>
</div>
<h2>Live configuration snapshot</h2><p>Captured: <span class='status'>{esc(captured)}</span></p><pre>{esc(json.dumps(config_status, ensure_ascii=False, indent=2)[:50000])}</pre>
<h2>Calibration records</h2>{''.join(run_rows)}
<h2>Notes</h2><p>{esc(printer.get('notes',''))}</p>
</body></html>"""
    return doc.encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "GKCC/0.1.1"

    @property
    def client_ip(self) -> str:
        return str(self.client_address[0])

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[web] {self.client_ip} - {fmt % args}")

    def send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 1_000_000:
            raise ValueError("Request too large")
        raw = self.rfile.read(length)
        value = json.loads(raw.decode("utf-8")) if raw else {}
        if not isinstance(value, dict):
            raise ValueError("JSON object required")
        return value

    def require_token(self) -> bool:
        if valid_token(self.headers.get("X-Calibration-Token"), self.client_ip):
            return True
        self.send_json({"ok": False, "error": "Machine controls are locked"}, 403)
        return False

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path in {"/", "/index.html"}:
            body = INDEX_PATH.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/status":
            with _state_lock:
                payload = copy.deepcopy(_state)
            payload["controls_unlocked"] = valid_token(self.headers.get("X-Calibration-Token"), self.client_ip)
            self.send_json(payload)
            return
        if path == "/api/config":
            self.send_json(public_config())
            return
        if path == "/api/profile":
            self.send_json(read_json(PROFILE_PATH, DEFAULT_PROFILE))
            return
        if path == "/api/workflows":
            self.send_json(read_json(WORKFLOWS_PATH, {"workflows": []}))
            return
        if path == "/api/records":
            self.send_json(read_json(RECORDS_PATH, DEFAULT_RECORDS))
            return
        if path == "/api/export":
            payload = {
                "exported_at": now_iso(),
                "profile": read_json(PROFILE_PATH, DEFAULT_PROFILE),
                "records": read_json(RECORDS_PATH, DEFAULT_RECORDS),
                "snapshot": read_json(SNAPSHOT_PATH, {}),
            }
            body = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Disposition", "attachment; filename=voron-calibration-export.json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/report":
            body = report_html()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/api/unlock":
                supplied = str(self.read_json().get("pin", ""))
                if not secrets.compare_digest(supplied, str(config().get("screen_pin", "1830"))):
                    self.send_json({"ok": False, "error": "Incorrect PIN"}, 403)
                    return
                self.send_json({"ok": True, "token": create_token(self.client_ip), "expires_minutes": 15})
                return
            if path == "/api/profile":
                if not self.require_token():
                    return
                incoming = self.read_json()
                atomic_write_json(PROFILE_PATH, incoming)
                self.send_json({"ok": True})
                return
            if path == "/api/snapshot":
                if not self.require_token():
                    return
                self.send_json(capture_snapshot())
                return
            if path == "/api/run":
                if not self.require_token():
                    return
                self.send_json(save_run(self.read_json()))
                return
            if path == "/api/action/mmu_move":
                if not self.require_token():
                    return
                data = self.read_json()
                move = float(data.get("move", 0.0))
                speed = float(data.get("speed", 5.0))
                motor = str(data.get("motor", "extruder"))
                if not (-100.0 <= move <= 100.0) or abs(move) < 0.001:
                    raise ValueError("Move must be between -100 and 100 mm")
                if not (0.1 <= speed <= 100.0):
                    raise ValueError("Speed must be between 0.1 and 100 mm/s")
                if motor not in {"gear", "extruder", "gear+extruder", "extruder+gear"}:
                    raise ValueError("Invalid motor mode")
                script = f"MMU_TEST_MOVE MOVE={move:.3f} SPEED={speed:.3f} MOTOR={motor}"
                self.send_json({"ok": True, "result": run_gcode(script, f"MMU move {move:+.3f} mm"), "script": script})
                return
            if path == "/api/action/hotend":
                if not self.require_token():
                    return
                target = float(self.read_json().get("target", 0.0))
                if not (0 <= target <= 290):
                    raise ValueError("Hotend target must be 0–290°C")
                script = f"M104 S{target:.1f}"
                self.send_json({"ok": True, "result": run_gcode(script, f"Hotend {target:.1f}°C"), "script": script})
                return
            if path == "/api/action/status":
                if not self.require_token():
                    return
                self.send_json({"ok": True, "result": run_gcode("MMU_STATUS", "Happy Hare status")})
                return
            if path == "/api/action/heaters_off":
                if not self.require_token():
                    return
                self.send_json({"ok": True, "result": run_gcode("TURN_OFF_HEATERS", "Turn off heaters")})
                return
            self.send_error(404)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            self.send_json({"ok": False, "error": f"Moonraker HTTP {exc.code}", "detail": detail}, 502)
        except Exception as exc:
            with _state_lock:
                _state["last_result"] = f"Failed: {exc}"
            self.send_json({"ok": False, "error": str(exc)}, 400)


def main() -> None:
    ensure_files()
    threading.Thread(target=status_worker, name="moonraker-status", daemon=True).start()
    port = int(config()["port"])
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"GKCC Calibration Center v0.1.1: http://0.0.0.0:{port}")
    print("Configuration writes are disabled by design in this release.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
