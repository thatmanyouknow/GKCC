#!/usr/bin/env python3
"""GKCC Calibration Center v0.4.3.

A small local web service for guided Klipper and Happy Hare calibration.
The first release is intentionally conservative: it reads status, sends a
small allow-listed set of calibration actions, records operator results, and
produces a printable as-built HTML report. Version 0.4.3 adds safe XYZ jogging and coordinate teaching so physical locations such as a Blobifier brush or Klicky dock can be captured directly into the configuration draft.
"""
from __future__ import annotations

import copy
import difflib
import fnmatch
import hashlib
import html
import io
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
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
BUILDER_SCHEMA_PATH = APP_DIR / "config_builder.json"
ELLIS_GUIDE_PATH = APP_DIR / "ellis_guide.json"
ERFC_GUIDE_PATH = APP_DIR / "erfc_guide.json"
BLOBIFIER_GUIDE_PATH = APP_DIR / "blobifier_guide.json"
BUILDER_PROJECT_PATH = DATA_DIR / "config_builder_project.json"
BACKUP_DIR = LOCAL_ROOT / "backups"

_state_lock = threading.Lock()
_file_lock = threading.Lock()
_auth_lock = threading.Lock()
_command_lock = threading.Lock()
_live_lock = threading.Lock()
_tokens: Dict[str, Tuple[float, str]] = {}

_state: Dict[str, Any] = {
    "connected": False,
    "printer_state": "unknown",
    "state_message": "Starting",
    "print_state": "unknown",
    "filename": "",
    "homed_axes": "",
    "position": [0.0, 0.0, 0.0, 0.0],
    "gcode_position": [0.0, 0.0, 0.0, 0.0],
    "homing_origin": [0.0, 0.0, 0.0, 0.0],
    "axis_minimum": [0.0, 0.0, 0.0],
    "axis_maximum": [0.0, 0.0, 0.0],
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


DEFAULT_BUILDER_PROJECT: Dict[str, Any] = {
    "version": 3,
    "updated_at": None,
    "meta": {
        "printer_name": "",
        "project_name": "New Klipper build",
        "source_filename": "",
        "source_kind": "blank",
        "source_imported_at": None,
        "notes": ""
    },
    "includes": [],
    "sections": {},
    "section_order": [],
    "wizard": {"ellis": {}, "erfc": {}, "blobifier": {}},
    "import_warnings": [],
    "taught_locations": [],
    "live": {
        "baseline_at": None,
        "source_files": {},
        "section_files": {},
        "baseline_sections": {},
        "baseline_includes": {},
        "file_order": []
    }
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
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
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
    project = normalize_builder_project(read_json(BUILDER_PROJECT_PATH, DEFAULT_BUILDER_PROJECT))
    project["updated_at"] = project.get("updated_at") or now_iso()
    atomic_write_json(BUILDER_PROJECT_PATH, project)


def config() -> Dict[str, Any]:
    cfg = read_json(CONFIG_PATH, {})
    cfg.setdefault("port", 7128)
    cfg.setdefault("moonraker_url", "http://127.0.0.1:7125")
    cfg.setdefault("screen_pin", "1830")
    cfg.setdefault("poll_seconds", 1.0)
    cfg.setdefault("allow_machine_actions", True)
    cfg.setdefault("allow_live_config_writes", True)
    cfg.setdefault("live_restart_timeout_seconds", 45.0)
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




def moonraker_request_bytes(
    path: str,
    method: str = "GET",
    body: Optional[bytes] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 30.0,
) -> Tuple[bytes, Dict[str, str], int]:
    """Make a Moonraker HTTP request without assuming a JSON response."""
    base = str(config()["moonraker_url"]).rstrip("/")
    request_headers = {"Accept": "*/*"}
    if headers:
        request_headers.update(headers)
    req = urllib.request.Request(base + path, data=body, headers=request_headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read()
        response_headers = {str(k): str(v) for k, v in response.headers.items()}
        status = int(getattr(response, "status", 200))
    return raw, response_headers, status


def unwrap_result(payload: Any) -> Any:
    if isinstance(payload, dict) and "result" in payload:
        return payload["result"]
    return payload


def normalize_config_relpath(value: str) -> str:
    candidate = str(value).strip().replace("\\", "/")
    while candidate.startswith("./"):
        candidate = candidate[2:]
    if not candidate or candidate.startswith("/") or "\x00" in candidate:
        raise ValueError("Invalid configuration path")
    parts = [part for part in candidate.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        raise ValueError("Configuration paths may not leave the config root")
    normalized = "/".join(parts)
    if len(normalized) > 500:
        raise ValueError("Configuration path is too long")
    if not normalized.lower().endswith(".cfg"):
        raise ValueError("Only .cfg files may be changed live")
    return normalized


def moonraker_config_roots() -> List[Dict[str, Any]]:
    reply = moonraker_request("/server/files/roots", timeout=20.0)
    result = unwrap_result(reply)
    return result if isinstance(result, list) else []


def moonraker_list_config_files() -> List[str]:
    query = urllib.parse.urlencode({"root": "config"})
    reply = moonraker_request(f"/server/files/list?{query}", timeout=30.0)
    result = unwrap_result(reply)
    rows = result if isinstance(result, list) else []
    paths: List[str] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        raw_path = str(item.get("path", ""))
        if not raw_path.lower().endswith(".cfg"):
            continue
        try:
            paths.append(normalize_config_relpath(raw_path))
        except ValueError:
            continue
    return paths


def live_config_root_status() -> Dict[str, Any]:
    roots = moonraker_config_roots()
    config_root = next((item for item in roots if isinstance(item, dict) and item.get("name") == "config"), None)
    permissions = str(config_root.get("permissions", "")) if config_root else ""
    return {
        "available": config_root is not None,
        "writable": "w" in permissions,
        "permissions": permissions,
        "path": config_root.get("path") if config_root else None,
    }


def moonraker_download_config(path: str) -> Optional[bytes]:
    normalized = normalize_config_relpath(path)
    encoded = urllib.parse.quote(normalized, safe="/")
    try:
        raw, _, _ = moonraker_request_bytes(f"/server/files/config/{encoded}", timeout=30.0)
        return raw
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def multipart_form(fields: Dict[str, str], filename: str, content: bytes) -> Tuple[bytes, str]:
    boundary = "----GKCC" + secrets.token_hex(16)
    chunks: List[bytes] = []
    for key, value in fields.items():
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode(),
            str(value).encode("utf-8"),
            b"\r\n",
        ])
    safe_filename = Path(filename).name.replace('"', "")
    chunks.extend([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{safe_filename}"\r\n'.encode(),
        b"Content-Type: application/octet-stream\r\n\r\n",
        content,
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    return b"".join(chunks), boundary


def moonraker_upload_config(path: str, content: bytes) -> Dict[str, Any]:
    normalized = normalize_config_relpath(path)
    parent = str(Path(normalized).parent).replace("\\", "/")
    fields = {
        "root": "config",
        "checksum": hashlib.sha256(content).hexdigest(),
    }
    if parent not in {"", "."}:
        fields["path"] = parent
    body, boundary = multipart_form(fields, Path(normalized).name, content)
    raw, _, _ = moonraker_request_bytes(
        "/server/files/upload",
        method="POST",
        body=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        timeout=60.0,
    )
    return json.loads(raw.decode("utf-8")) if raw else {}


def moonraker_delete_config(path: str) -> Any:
    normalized = normalize_config_relpath(path)
    encoded = urllib.parse.quote(normalized, safe="/")
    try:
        raw, _, _ = moonraker_request_bytes(
            f"/server/files/config/{encoded}", method="DELETE", timeout=30.0
        )
        return json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {"ok": True, "already_missing": True}
        raise


def moonraker_printer_info() -> Dict[str, Any]:
    reply = moonraker_request("/printer/info", timeout=10.0)
    result = unwrap_result(reply)
    return result if isinstance(result, dict) else {}


def restart_klipper_and_wait(timeout: Optional[float] = None) -> Dict[str, Any]:
    wait_seconds = float(timeout or config().get("live_restart_timeout_seconds", 45.0))
    moonraker_request("/printer/restart", method="POST", payload={}, timeout=15.0)
    deadline = time.monotonic() + max(10.0, wait_seconds)
    last_state = "restarting"
    last_message = "Klipper restart requested"
    saw_disconnect = False
    while time.monotonic() < deadline:
        time.sleep(1.0)
        try:
            info = moonraker_printer_info()
            last_state = str(info.get("state", "unknown"))
            last_message = str(info.get("state_message", info.get("message", "")))
            if last_state == "ready":
                refresh_status()
                return {"ok": True, "state": last_state, "message": last_message}
            if last_state in {"error", "shutdown"}:
                return {"ok": False, "state": last_state, "message": last_message}
        except Exception as exc:
            saw_disconnect = True
            last_message = str(exc)
    return {
        "ok": False,
        "state": last_state,
        "message": last_message,
        "saw_disconnect": saw_disconnect,
        "timeout_seconds": wait_seconds,
    }


def sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()

def object_list() -> List[str]:
    reply = moonraker_request("/printer/objects/list")
    result = reply.get("result", reply) if isinstance(reply, dict) else {}
    objects = result.get("objects", []) if isinstance(result, dict) else []
    return [str(item) for item in objects] if isinstance(objects, list) else []


def build_query(objects: List[str], include_config: bool = False) -> Dict[str, Any]:
    requested: Dict[str, Any] = {}
    for name in ("webhooks", "print_stats", "toolhead", "gcode_move", "extruder", "heater_bed"):
        if name in objects:
            requested[name] = None
    for name in objects:
        low = name.lower()
        if low == "mmu" or low.startswith("mmu_") or low.startswith("gcode_button ") or "filament_switch_sensor" in low or "filament_motion_sensor" in low:
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
            if "sensor" in low or "encoder" in low or low.startswith("gcode_button "):
                sensors[name] = value
        webhooks = status.get("webhooks", {})
        print_stats = status.get("print_stats", {})
        toolhead = status.get("toolhead", {})
        gcode_move = status.get("gcode_move", {})
        machine_position = toolhead.get("position", gcode_move.get("position", [0.0, 0.0, 0.0, 0.0]))
        gcode_position = gcode_move.get("gcode_position", machine_position)
        with _state_lock:
            _state.update({
                "connected": True,
                "printer_state": webhooks.get("state", "unknown"),
                "state_message": webhooks.get("state_message", webhooks.get("message", "")),
                "print_state": print_stats.get("state", "unknown"),
                "filename": print_stats.get("filename", ""),
                "homed_axes": toolhead.get("homed_axes", ""),
                "position": machine_position,
                "gcode_position": gcode_position,
                "homing_origin": gcode_move.get("homing_origin", [0.0, 0.0, 0.0, 0.0]),
                "axis_minimum": toolhead.get("axis_minimum", [0.0, 0.0, 0.0]),
                "axis_maximum": toolhead.get("axis_maximum", [0.0, 0.0, 0.0]),
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


def position_snapshot() -> Dict[str, Any]:
    """Return coordinates used by G1 plus the machine coordinate and travel limits."""
    objects = object_list()
    requested: Dict[str, Any] = {}
    if "gcode_move" in objects:
        requested["gcode_move"] = ["gcode_position", "position", "homing_origin"]
    if "toolhead" in objects:
        requested["toolhead"] = ["position", "homed_axes", "axis_minimum", "axis_maximum"]
    reply = moonraker_request(
        "/printer/objects/query",
        method="POST",
        payload={"objects": requested},
        timeout=15.0,
    )
    result = reply.get("result", reply) if isinstance(reply, dict) else {}
    status = result.get("status", {}) if isinstance(result, dict) else {}
    toolhead = status.get("toolhead", {}) if isinstance(status, dict) else {}
    gcode_move = status.get("gcode_move", {}) if isinstance(status, dict) else {}
    machine = toolhead.get("position", gcode_move.get("position", [0.0, 0.0, 0.0, 0.0]))
    gcode = gcode_move.get("gcode_position", machine)
    return {
        "captured_at": now_iso(),
        "gcode_position": list(gcode) if isinstance(gcode, (list, tuple)) else [0.0, 0.0, 0.0, 0.0],
        "machine_position": list(machine) if isinstance(machine, (list, tuple)) else [0.0, 0.0, 0.0, 0.0],
        "homing_origin": list(gcode_move.get("homing_origin", [0.0, 0.0, 0.0, 0.0])),
        "homed_axes": str(toolhead.get("homed_axes", "")),
        "axis_minimum": list(toolhead.get("axis_minimum", [0.0, 0.0, 0.0])),
        "axis_maximum": list(toolhead.get("axis_maximum", [0.0, 0.0, 0.0])),
    }


def require_homed_axes(required: str = "xyz") -> None:
    with _state_lock:
        homed = str(_state.get("homed_axes", "")).lower()
    missing = [axis.upper() for axis in required.lower() if axis not in homed]
    if missing:
        raise RuntimeError("Home " + ", ".join(missing) + " before moving or teaching a location")


def jog_axis(axis: str, distance: float, speed: float) -> Dict[str, Any]:
    axis = axis.lower().strip()
    if axis not in {"x", "y", "z"}:
        raise ValueError("Axis must be X, Y, or Z")
    if not (-25.0 <= distance <= 25.0) or abs(distance) < 0.0005:
        raise ValueError("Jog distance must be between -25 and 25 mm")
    max_speed = 50.0 if axis in {"x", "y"} else 10.0
    if not (0.1 <= speed <= max_speed):
        raise ValueError(f"{axis.upper()} jog speed must be between 0.1 and {max_speed:g} mm/s")
    ok, reason = printer_is_idle()
    if not ok:
        raise RuntimeError(reason)
    require_homed_axes("xyz")
    current = position_snapshot()
    machine = current.get("machine_position", [0.0, 0.0, 0.0])
    minimum = current.get("axis_minimum", [0.0, 0.0, 0.0])
    maximum = current.get("axis_maximum", [0.0, 0.0, 0.0])
    index = {"x": 0, "y": 1, "z": 2}[axis]
    try:
        target = float(machine[index]) + distance
        low = float(minimum[index])
        high = float(maximum[index])
    except (IndexError, TypeError, ValueError):
        raise RuntimeError("Klipper did not provide usable axis limits")
    if target < low - 0.001 or target > high + 0.001:
        raise ValueError(
            f"Jog would move {axis.upper()} to {target:.3f} mm, outside {low:.3f}–{high:.3f} mm"
        )
    feed = speed * 60.0
    script = "\n".join([
        "SAVE_GCODE_STATE NAME=GKCC_LOCATION_JOG",
        "G91",
        f"G1 {axis.upper()}{distance:.4f} F{feed:.1f}",
        "M400",
        "RESTORE_GCODE_STATE NAME=GKCC_LOCATION_JOG",
    ])
    run_gcode(script, f"Teach-location jog {axis.upper()} {distance:+.3f} mm")
    return {"ok": True, "script": script, "position": position_snapshot()}


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
        "allow_live_config_writes": bool(cfg.get("allow_live_config_writes", True)),
        "version": "0.4.3",
    }



def strip_inline_comment(value: str) -> str:
    """Remove a Klipper inline comment while preserving pin modifiers."""
    return re.split(r"\s+#", value, maxsplit=1)[0].rstrip()


def empty_live_project() -> Dict[str, Any]:
    return {
        "baseline_at": None,
        "source_files": {},
        "section_files": {},
        "baseline_sections": {},
        "baseline_includes": {},
        "file_order": [],
    }


def clean_cfg_path_for_import(filename: str) -> str:
    raw = str(filename or "printer.cfg").strip().replace("\\", "/")
    raw = raw.split("/")[-1] if raw.startswith("C:/") else raw
    try:
        return normalize_config_relpath(raw)
    except ValueError:
        base = Path(raw).name or "printer.cfg"
        if not base.lower().endswith(".cfg"):
            base += ".cfg"
        return normalize_config_relpath(base)


def parse_klipper_cfg(text: str, filename: str = "") -> Dict[str, Any]:
    if len(text.encode("utf-8")) > 2_000_000:
        raise ValueError("Configuration file is larger than 2 MB")
    source_path = clean_cfg_path_for_import(filename or "printer.cfg")
    sections: Dict[str, Dict[str, str]] = {}
    section_order: List[str] = []
    includes: List[str] = []
    warnings: List[str] = []
    current: Optional[str] = None
    ignored_save_config = False
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if line.startswith("#*#"):
            ignored_save_config = True
            continue
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1].strip()
            if name.lower().startswith("include "):
                includes.append(name[8:].strip())
                current = None
                continue
            current = name
            if current not in sections:
                sections[current] = {}
                section_order.append(current)
            continue
        if current is None:
            warnings.append(f"Line {lineno}: value outside a section was ignored by the form parser (raw text is preserved)")
            continue
        if raw[:1].isspace():
            # Indented lines are continuations (for example gcode/Jinja) and are
            # preserved only in the raw source baseline.
            continue
        match = re.match(r"^([^:=\s][^:=]*?)\s*[:=]\s*(.*)$", raw.strip())
        if not match:
            continue
        option = match.group(1).strip()
        value = strip_inline_comment(match.group(2).strip())
        sections[current][option] = value
    if ignored_save_config:
        warnings.append("The generated SAVE_CONFIG block is preserved in the raw baseline but is not represented as editable fields.")
    project = copy.deepcopy(DEFAULT_BUILDER_PROJECT)
    project["updated_at"] = now_iso()
    project["meta"].update({
        "project_name": Path(source_path).stem if source_path else "Imported Klipper build",
        "source_filename": source_path,
        "source_kind": "imported",
        "source_imported_at": now_iso(),
    })
    project["includes"] = includes
    project["sections"] = sections
    project["section_order"] = section_order
    project["import_warnings"] = warnings
    project["live"] = empty_live_project()
    project["live"].update({
        "baseline_at": now_iso(),
        "source_files": {source_path: text},
        "section_files": {section: source_path for section in sections},
        "baseline_sections": copy.deepcopy(sections),
        "baseline_includes": {source_path: list(includes)},
        "file_order": [source_path],
    })
    return project


def merge_builder_projects(projects: List[Dict[str, Any]], filenames: List[str]) -> Dict[str, Any]:
    merged = copy.deepcopy(DEFAULT_BUILDER_PROJECT)
    merged["updated_at"] = now_iso()
    merged["meta"].update({
        "project_name": Path(filenames[0]).stem if len(filenames) == 1 else "Imported Klipper configuration set",
        "source_filename": ", ".join(filenames)[:1000],
        "source_kind": "imported",
        "source_imported_at": now_iso(),
    })
    merged["live"] = empty_live_project()
    merged["live"]["baseline_at"] = now_iso()
    for index, project in enumerate(projects):
        project_live = project.get("live", {}) if isinstance(project.get("live", {}), dict) else {}
        project_paths = list(project_live.get("file_order", []))
        project_path = project_paths[0] if project_paths else clean_cfg_path_for_import(filenames[index])
        if project_path == "printer.cfg" or (not merged["includes"] and index == 0):
            merged["includes"] = list(project.get("includes", []))
        for section in project.get("section_order", []):
            if section not in merged["section_order"]:
                merged["section_order"].append(section)
        for section, options in project.get("sections", {}).items():
            if section in merged["sections"]:
                merged["import_warnings"].append(
                    f"Section [{section}] appeared in more than one imported file; the later file won."
                )
            merged["sections"].setdefault(section, {}).update(options)
        merged["import_warnings"].extend(project.get("import_warnings", []))
        live = project_live
        for path, content in live.get("source_files", {}).items():
            merged["live"]["source_files"][path] = content
            if path not in merged["live"]["file_order"]:
                merged["live"]["file_order"].append(path)
        for section, path in live.get("section_files", {}).items():
            merged["live"]["section_files"][section] = path
        for section, options in live.get("baseline_sections", {}).items():
            merged["live"]["baseline_sections"][section] = copy.deepcopy(options)
        for path, includes in live.get("baseline_includes", {}).items():
            merged["live"]["baseline_includes"][path] = list(includes)
    return merged


def normalize_section_map(value: Any) -> Dict[str, Dict[str, str]]:
    if not isinstance(value, dict):
        return {}
    clean_sections: Dict[str, Dict[str, str]] = {}
    for section, options in list(value.items())[:500]:
        section_name = str(section).strip()[:200]
        if not section_name or not isinstance(options, dict):
            continue
        clean_options: Dict[str, str] = {}
        for option, option_value in list(options.items())[:1000]:
            option_name = str(option).strip()[:200]
            if option_name:
                clean_options[option_name] = str(option_value)[:20000]
        clean_sections[section_name] = clean_options
    return clean_sections


def normalize_builder_project(payload: Dict[str, Any]) -> Dict[str, Any]:
    project = copy.deepcopy(DEFAULT_BUILDER_PROJECT)
    incoming = payload.get("project", payload)
    if not isinstance(incoming, dict):
        raise ValueError("Builder project must be an object")
    meta = incoming.get("meta", {})
    if isinstance(meta, dict):
        project["meta"].update({str(k): str(v)[:10000] if v is not None else "" for k, v in meta.items()})
    includes = incoming.get("includes", [])
    if isinstance(includes, list):
        project["includes"] = [str(x).strip()[:500] for x in includes if str(x).strip()][:200]
    sections = incoming.get("sections", {})
    if not isinstance(sections, dict):
        raise ValueError("sections must be an object")
    project["sections"] = normalize_section_map(sections)
    order = incoming.get("section_order", [])
    if isinstance(order, list):
        project["section_order"] = [str(x) for x in order if str(x) in project["sections"]]
    for section in project["sections"]:
        if section not in project["section_order"]:
            project["section_order"].append(section)
    wizard = incoming.get("wizard", {})
    if isinstance(wizard, dict):
        project["wizard"] = {
            "ellis": wizard.get("ellis", {}) if isinstance(wizard.get("ellis", {}), dict) else {},
            "erfc": wizard.get("erfc", {}) if isinstance(wizard.get("erfc", {}), dict) else {},
            "blobifier": wizard.get("blobifier", {}) if isinstance(wizard.get("blobifier", {}), dict) else {},
        }
    warnings = incoming.get("import_warnings", [])
    if isinstance(warnings, list):
        project["import_warnings"] = [str(x)[:1000] for x in warnings[:200]]
    taught_locations = incoming.get("taught_locations", [])
    if isinstance(taught_locations, list):
        normalized_locations: List[Dict[str, Any]] = []
        for item in taught_locations[-200:]:
            if not isinstance(item, dict):
                continue
            clean: Dict[str, Any] = {
                "id": str(item.get("id", ""))[:120],
                "name": str(item.get("name", ""))[:200],
                "preset": str(item.get("preset", "notebook"))[:80],
                "captured_at": str(item.get("captured_at", ""))[:100],
                "notes": str(item.get("notes", ""))[:2000],
            }
            for key in ("gcode_position", "machine_position", "axis_minimum", "axis_maximum"):
                values = item.get(key, [])
                if isinstance(values, (list, tuple)):
                    clean[key] = [float(v) for v in list(values)[:6] if isinstance(v, (int, float))]
            mappings = item.get("mappings", [])
            if isinstance(mappings, list):
                clean["mappings"] = [str(v)[:300] for v in mappings[:20]]
            normalized_locations.append(clean)
        project["taught_locations"] = normalized_locations
    live_in = incoming.get("live", {}) if isinstance(incoming.get("live", {}), dict) else {}
    live = empty_live_project()
    baseline_at = live_in.get("baseline_at")
    live["baseline_at"] = str(baseline_at)[:100] if baseline_at else None
    source_files = live_in.get("source_files", {})
    if isinstance(source_files, dict):
        for raw_path, raw_content in list(source_files.items())[:30]:
            try:
                cfg_path = normalize_config_relpath(str(raw_path))
            except ValueError:
                continue
            content = str(raw_content)
            if len(content.encode("utf-8")) <= 2_000_000:
                live["source_files"][cfg_path] = content
    live["baseline_sections"] = normalize_section_map(live_in.get("baseline_sections", {}))
    section_files = live_in.get("section_files", {})
    if isinstance(section_files, dict):
        for section, raw_path in section_files.items():
            section_name = str(section)
            if section_name not in project["sections"] and section_name not in live["baseline_sections"]:
                continue
            try:
                live["section_files"][section_name] = normalize_config_relpath(str(raw_path))
            except ValueError:
                continue
    baseline_includes = live_in.get("baseline_includes", {})
    if isinstance(baseline_includes, dict):
        for raw_path, values in baseline_includes.items():
            try:
                cfg_path = normalize_config_relpath(str(raw_path))
            except ValueError:
                continue
            if isinstance(values, list):
                live["baseline_includes"][cfg_path] = [str(x).strip()[:500] for x in values if str(x).strip()][:200]
    file_order = live_in.get("file_order", [])
    if isinstance(file_order, list):
        for raw_path in file_order:
            try:
                cfg_path = normalize_config_relpath(str(raw_path))
            except ValueError:
                continue
            if cfg_path in live["source_files"] and cfg_path not in live["file_order"]:
                live["file_order"].append(cfg_path)
    for cfg_path in live["source_files"]:
        if cfg_path not in live["file_order"]:
            live["file_order"].append(cfg_path)
    project["live"] = live
    project["version"] = 3
    project["updated_at"] = now_iso()
    return project


def builder_schema() -> Dict[str, Any]:
    return read_json(BUILDER_SCHEMA_PATH, {"groups": []})


def builder_missing_required(project: Dict[str, Any]) -> List[str]:
    missing: List[str] = []
    for group in builder_schema().get("groups", []):
        for field in group.get("fields", []):
            if not field.get("required"):
                continue
            if field.get("section"):
                value = project.get("sections", {}).get(field["section"], {}).get(field["option"], "")
            else:
                value = project.get("meta", {}).get(field.get("key", "").split(".", 1)[-1], "")
            if str(value).strip() == "":
                missing.append(str(field.get("label", field.get("key", "Required value"))))
    return missing


def render_cfg_sections(project: Dict[str, Any], selected: Optional[List[str]] = None) -> str:
    sections: Dict[str, Dict[str, str]] = project.get("sections", {})
    order = [s for s in project.get("section_order", []) if s in sections]
    for section in sections:
        if section not in order:
            order.append(section)
    if selected is not None:
        allowed = set(selected)
        order = [s for s in order if s in allowed]
    lines: List[str] = []
    for section in order:
        options = sections.get(section, {})
        if not options:
            continue
        lines.append(f"[{section}]")
        for option, value in options.items():
            if str(value).strip() == "":
                continue
            rendered = str(value).replace("\r\n", "\n").replace("\r", "\n")
            if "\n" in rendered:
                lines.append(f"{option}:")
                lines.extend(f"    {part}" for part in rendered.split("\n") if part.strip())
            else:
                lines.append(f"{option}: {rendered}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def generated_files(project: Dict[str, Any]) -> Dict[str, bytes]:
    missing = builder_missing_required(project)
    header = [
        "# GKCC configuration builder draft",
        f"# Generated: {now_iso()}",
        "# Review against the current Klipper Config Reference before use.",
        "# Export copy. Live application requires PIN unlock, diff review, backup, and operator confirmation.",
    ]
    if missing:
        header.append("# INCOMPLETE - required guided fields still missing:")
        header.extend(f"#   - {item}" for item in missing)
    header.append("")
    include_lines = [f"[include {item}]" for item in project.get("includes", [])]
    all_sections = list(project.get("sections", {}).keys())
    blobifier_hw_sections = [name for name in all_sections if is_blobifier_hardware_section(name)]
    blobifier_sections = [name for name in all_sections if is_blobifier_section(name)]
    mmu_sections = [
        name for name in all_sections
        if is_mmu_hardware_section(name) or name in {"mmu_parameters", "mmu_macro_vars"}
    ]
    printer_sections = [name for name in all_sections if name not in set(mmu_sections + blobifier_hw_sections + blobifier_sections)]
    printer_cfg = "\n".join(header + include_lines + ([""] if include_lines else [])) + render_cfg_sections(project, printer_sections)
    hardware_names = [name for name in mmu_sections if name not in {"mmu_parameters", "mmu_macro_vars"}]
    mmu_hardware = "\n".join(header) + render_cfg_sections(project, hardware_names)
    mmu_parameters = "\n".join(header) + render_cfg_sections(project, ["mmu_parameters"])
    mmu_macro_vars = "\n".join(header) + render_cfg_sections(project, ["mmu_macro_vars"])
    blobifier_cfg = "\n".join(header) + render_cfg_sections(project, blobifier_sections)
    blobifier_hw = "\n".join(header) + render_cfg_sections(project, blobifier_hw_sections)
    notes = {
        "generated_at": now_iso(),
        "project": project.get("meta", {}),
        "missing_required": missing,
        "import_warnings": project.get("import_warnings", []),
        "ellis_progress": project.get("wizard", {}).get("ellis", {}),
        "erfc_progress": project.get("wizard", {}).get("erfc", {}),
        "blobifier_progress": project.get("wizard", {}).get("blobifier", {}),
        "safety": [
            "Review all generated values before applying them.",
            "GKCC creates a ZIP backup of every file changed through the live apply workflow.",
            "Use the Happy Hare installer to create version-matched base files, then merge the GKCC draft values.",
            "Restart Klipper only after reviewing pins, thermistors, travel limits, and heater safety values."
        ]
    }
    files: Dict[str, bytes] = {
        "printer.cfg": printer_cfg.encode("utf-8"),
        "mmu/base/mmu_hardware.cfg": mmu_hardware.encode("utf-8"),
        "mmu/base/mmu_parameters.cfg": mmu_parameters.encode("utf-8"),
        "mmu/base/mmu_macro_vars.cfg": mmu_macro_vars.encode("utf-8"),
        "GKCC_BUILD_NOTES.json": json.dumps(notes, indent=2, ensure_ascii=False).encode("utf-8"),
        "GKCC_PROJECT.json": json.dumps(project, indent=2, ensure_ascii=False).encode("utf-8"),
    }
    # Blobifier's official macro is large and version coupled to Happy Hare. Never
    # export an incomplete replacement. Patch and include it only when the live/imported
    # official file is present in the project baseline.
    raw_sources = project.get("live", {}).get("source_files", {}) if isinstance(project.get("live", {}), dict) else {}
    schema = builder_schema()
    guided_blobifier_options: Dict[str, set[str]] = {}
    for group in schema.get("groups", []):
        if not str(group.get("id", "")).startswith("blobifier_"):
            continue
        for field in group.get("fields", []):
            section = field.get("section")
            option = field.get("option")
            if section and option:
                guided_blobifier_options.setdefault(str(section), set()).add(str(option))

    def blobifier_guided_updates(section_names: List[str]) -> Dict[str, Dict[str, str]]:
        updates: Dict[str, Dict[str, str]] = {}
        for name in section_names:
            allowed = guided_blobifier_options.get(name, set())
            values = project.get("sections", {}).get(name, {})
            chosen = {option: str(values[option]) for option in allowed if option in values}
            if chosen:
                updates[name] = chosen
        return updates

    if "mmu/addons/blobifier.cfg" in raw_sources:
        files["mmu/addons/blobifier.cfg"] = patch_cfg_text(
            str(raw_sources["mmu/addons/blobifier.cfg"]),
            blobifier_guided_updates(blobifier_sections),
            {},
            [],
        ).encode("utf-8")
    if "mmu/addons/blobifier_hw.cfg" in raw_sources:
        files["mmu/addons/blobifier_hw.cfg"] = patch_cfg_text(
            str(raw_sources["mmu/addons/blobifier_hw.cfg"]),
            blobifier_guided_updates(blobifier_hw_sections),
            {},
            [],
        ).encode("utf-8")
    return files


def builder_export_zip(project: Dict[str, Any]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, body in generated_files(project).items():
            archive.writestr(name, body)
    return buf.getvalue()




def is_blobifier_hardware_section(section: str) -> bool:
    low = section.lower().strip()
    return low in {"mmu_servo blobifier", "gcode_button bucket"}


def is_blobifier_section(section: str) -> bool:
    low = section.lower().strip()
    return "blobifier" in low and not is_blobifier_hardware_section(section)


def is_mmu_hardware_section(section: str) -> bool:
    low = section.lower()
    return (
        low.startswith("mmu_")
        or low.startswith("stepper_mmu_")
        or (low.startswith("tmc") and "mmu_" in low)
        or low == "mcu mmu"
    ) and not is_blobifier_hardware_section(section)


def canonical_target_for_section(section: str) -> str:
    low = section.lower()
    if low == "mmu_parameters":
        return "mmu/base/mmu_parameters.cfg"
    if low == "mmu_macro_vars":
        return "mmu/base/mmu_macro_vars.cfg"
    if is_blobifier_hardware_section(section):
        return "mmu/addons/blobifier_hw.cfg"
    if is_blobifier_section(section):
        return "mmu/addons/blobifier.cfg"
    if is_mmu_hardware_section(section):
        return "mmu/base/mmu_hardware.cfg"
    return "printer.cfg"


def render_cfg_file(
    project: Dict[str, Any],
    sections: List[str],
    includes: Optional[List[str]] = None,
    live_header: bool = False,
) -> bytes:
    header = [
        "# Generated by GKCC",
        f"# Generated: {now_iso()}",
    ]
    if live_header:
        header.append("# Applied through Moonraker after backup and operator approval.")
    lines = header + [""]
    for include in includes or []:
        lines.append(f"[include {include}]")
    if includes:
        lines.append("")
    body = render_cfg_sections(project, sections)
    return ("\n".join(lines) + body).encode("utf-8")


def cfg_option_header(line: str) -> Optional[re.Match[str]]:
    if line[:1].isspace() or line.lstrip().startswith(("#", ";", "[")):
        return None
    return re.match(r"^(\s*)([^:=\s][^:=]*?)(\s*[:=]\s*)(.*)$", line)


def cfg_structure(text: str) -> Dict[str, Any]:
    lines = text.splitlines()
    section_starts: List[Tuple[str, int]] = []
    includes: List[Tuple[int, str]] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            name = stripped[1:-1].strip()
            if name.lower().startswith("include "):
                includes.append((index, name[8:].strip()))
            else:
                section_starts.append((name, index))
    sections: Dict[str, Dict[str, Any]] = {}
    for pos, (name, start) in enumerate(section_starts):
        end = section_starts[pos + 1][1] if pos + 1 < len(section_starts) else len(lines)
        # An include header between normal sections also ends the current section.
        include_after = [idx for idx, _ in includes if start < idx < end]
        if include_after:
            end = min(include_after)
        option_headers: List[Tuple[str, int, re.Match[str]]] = []
        for idx in range(start + 1, end):
            match = cfg_option_header(lines[idx])
            if match:
                option_headers.append((match.group(2).strip(), idx, match))
        options: Dict[str, Dict[str, Any]] = {}
        for option_pos, (option, option_start, match) in enumerate(option_headers):
            option_end = option_headers[option_pos + 1][1] if option_pos + 1 < len(option_headers) else end
            while option_end > option_start + 1 and (
                not lines[option_end - 1].strip()
                or lines[option_end - 1].lstrip().startswith(("#", ";"))
            ):
                option_end -= 1
            options[option] = {
                "start": option_start,
                "end": option_end,
                "match": match,
            }
        insert_at = end
        while insert_at > start + 1 and (
            not lines[insert_at - 1].strip()
            or lines[insert_at - 1].lstrip().startswith(("#", ";"))
        ):
            insert_at -= 1
        sections[name] = {"start": start, "end": end, "insert": insert_at, "options": options}
    return {"lines": lines, "sections": sections, "includes": includes}


def render_option_replacement(option: str, value: str, match: Optional[re.Match[str]] = None) -> List[str]:
    rendered = str(value).replace("\r\n", "\n").replace("\r", "\n")
    indent = match.group(1) if match else ""
    separator = match.group(3) if match else ": "
    if "\n" not in rendered:
        comment = ""
        if match:
            old_tail = match.group(4)
            comment_match = re.search(r"(\s+#.*)$", old_tail)
            if comment_match:
                comment = comment_match.group(1)
        return [f"{indent}{option}{separator}{rendered}{comment}".rstrip()]
    result = [f"{indent}{option}:"]
    result.extend(f"{indent}    {part}" for part in rendered.split("\n") if part.strip())
    return result


def patch_cfg_text(
    raw_text: str,
    updates: Dict[str, Dict[str, str]],
    delete_options: Dict[str, List[str]],
    delete_sections: List[str],
    includes: Optional[List[str]] = None,
    patch_includes: bool = False,
) -> str:
    structure = cfg_structure(raw_text)
    lines: List[str] = list(structure["lines"])
    edits: List[Tuple[int, int, List[str]]] = []
    sections = structure["sections"]

    for section in delete_sections:
        info = sections.get(section)
        if info:
            start, end = int(info["start"]), int(info["end"])
            while end < len(lines) and not lines[end].strip():
                end += 1
            edits.append((start, end, []))

    for section, options in delete_options.items():
        info = sections.get(section)
        if not info:
            continue
        for option in options:
            option_info = info["options"].get(option)
            if option_info:
                edits.append((int(option_info["start"]), int(option_info["end"]), []))

    new_sections: List[Tuple[str, Dict[str, str]]] = []
    for section, options in updates.items():
        info = sections.get(section)
        if not info:
            new_sections.append((section, options))
            continue
        existing_options = info["options"]
        additions: List[str] = []
        for option, value in options.items():
            option_info = existing_options.get(option)
            if option_info:
                edits.append((
                    int(option_info["start"]),
                    int(option_info["end"]),
                    render_option_replacement(option, value, option_info["match"]),
                ))
            else:
                additions.extend(render_option_replacement(option, value))
        if additions:
            insert_at = int(info.get("insert", info["end"]))
            payload: List[str] = []
            payload.extend(additions)
            edits.append((insert_at, insert_at, payload))

    if patch_includes:
        include_rows = structure["includes"]
        new_lines = [f"[include {value}]" for value in includes or []]
        if include_rows:
            first = min(index for index, _ in include_rows)
            for index, _ in include_rows:
                edits.append((index, index + 1, []))
            edits.append((first, first, new_lines + ([""] if new_lines else [])))
        elif new_lines:
            insert_at = 0
            while insert_at < len(lines) and (not lines[insert_at].strip() or lines[insert_at].lstrip().startswith(("#", ";"))):
                insert_at += 1
            edits.append((insert_at, insert_at, new_lines + [""]))

    for start, end, replacement in sorted(edits, key=lambda item: (item[0], item[1]), reverse=True):
        lines[start:end] = replacement

    if new_sections:
        if lines and lines[-1].strip():
            lines.append("")
        for section, options in new_sections:
            lines.append(f"[{section}]")
            for option, value in options.items():
                lines.extend(render_option_replacement(option, value))
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def live_file_targets(project: Dict[str, Any]) -> List[str]:
    live = project.get("live", {}) if isinstance(project.get("live", {}), dict) else {}
    paths: List[str] = []
    for path in live.get("file_order", []):
        try:
            normalized = normalize_config_relpath(path)
        except ValueError:
            continue
        if normalized not in paths:
            paths.append(normalized)
    for section in project.get("section_order", []):
        target = live.get("section_files", {}).get(section) or canonical_target_for_section(section)
        try:
            normalized = normalize_config_relpath(target)
        except ValueError:
            continue
        if normalized not in paths:
            paths.append(normalized)
    if project.get("includes") and "printer.cfg" not in paths:
        paths.insert(0, "printer.cfg")
    return paths


def resolve_live_include_paths(current_path: str, pattern: str, available: List[str]) -> List[str]:
    raw = str(pattern).strip().replace("\\", "/")
    if not raw or raw.startswith("/") or "\x00" in raw:
        return []
    parts = [part for part in raw.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        return []
    normalized_pattern = "/".join(parts)
    patterns = [normalized_pattern]
    parent = str(Path(current_path).parent).replace("\\", "/")
    if parent not in {"", "."}:
        relative_pattern = f"{parent}/{normalized_pattern}"
        if relative_pattern not in patterns:
            patterns.append(relative_pattern)
    matches: List[str] = []
    for candidate in available:
        if any(fnmatch.fnmatchcase(candidate, item) for item in patterns):
            if candidate not in matches:
                matches.append(candidate)
    return matches


def rebase_project_from_live(project: Dict[str, Any], paths: Optional[List[str]] = None) -> Dict[str, Any]:
    requested = paths or [
        "printer.cfg",
        "mmu/base/mmu_hardware.cfg",
        "mmu/base/mmu_parameters.cfg",
        "mmu/base/mmu_macro_vars.cfg",
    ]
    try:
        available = moonraker_list_config_files()
    except Exception:
        available = []
    queue: List[str] = []
    for path in requested[:30]:
        normalized = normalize_config_relpath(path)
        if normalized not in queue:
            queue.append(normalized)
    for optional_path in ("mmu/addons/blobifier.cfg", "mmu/addons/blobifier_hw.cfg"):
        if optional_path in available and optional_path not in queue:
            queue.append(optional_path)
    parsed: List[Dict[str, Any]] = []
    names: List[str] = []
    missing: List[str] = []
    unmatched_includes: List[str] = []
    visited: set[str] = set()
    total_bytes = 0

    while queue and len(visited) < 80:
        normalized = queue.pop(0)
        if normalized in visited:
            continue
        visited.add(normalized)
        content = moonraker_download_config(normalized)
        if content is None:
            if normalized in requested:
                missing.append(normalized)
            continue
        total_bytes += len(content)
        if total_bytes > 20_000_000:
            raise RuntimeError("Live configuration include set exceeds the 20 MB safety limit")
        text = content.decode("utf-8", errors="replace")
        parsed_project = parse_klipper_cfg(text, normalized)
        parsed.append(parsed_project)
        names.append(normalized)
        if not available:
            continue
        for include_pattern in parsed_project.get("includes", []):
            matches = resolve_live_include_paths(normalized, include_pattern, available)
            if not matches:
                unmatched_includes.append(f"{normalized}: [include {include_pattern}]")
            for match in matches:
                if match not in visited and match not in queue:
                    queue.append(match)

    if not parsed:
        raise RuntimeError("None of the requested live configuration files could be read")
    baseline = merge_builder_projects(parsed, names)
    current = normalize_builder_project(project)
    # Preserve the user's current edits and progress while adding a fresh raw baseline.
    for section, options in current.get("sections", {}).items():
        baseline["sections"].setdefault(section, {}).update(options)
        if section not in baseline["section_order"]:
            baseline["section_order"].append(section)
    old_live = current.get("live", {}) if isinstance(current.get("live", {}), dict) else {}
    old_printer_includes = old_live.get("baseline_includes", {}).get("printer.cfg", [])
    includes_were_edited = bool(old_live.get("baseline_at")) and list(current.get("includes", [])) != list(old_printer_includes)
    if includes_were_edited:
        baseline["includes"] = list(current["includes"])
    baseline["wizard"] = copy.deepcopy(current.get("wizard", {"ellis": {}, "erfc": {}, "blobifier": {}}))
    baseline["meta"].update(current.get("meta", {}))
    baseline["meta"]["source_kind"] = "live_rebase"
    baseline["meta"]["source_filename"] = ", ".join(names)[:10000]
    baseline["meta"]["source_imported_at"] = now_iso()
    baseline["import_warnings"].extend(f"Live file not found: {item}" for item in missing)
    baseline["import_warnings"].extend(f"Include did not match a readable .cfg file: {item}" for item in unmatched_includes[:100])
    baseline["updated_at"] = now_iso()
    return normalize_builder_project(baseline)


def project_change_sets(project: Dict[str, Any], apply_deletions: bool) -> Dict[str, Dict[str, Any]]:
    live = project.get("live", {}) if isinstance(project.get("live", {}), dict) else {}
    baseline_sections = live.get("baseline_sections", {}) if isinstance(live.get("baseline_sections", {}), dict) else {}
    current_sections = project.get("sections", {})
    section_files = live.get("section_files", {}) if isinstance(live.get("section_files", {}), dict) else {}
    changes: Dict[str, Dict[str, Any]] = {}

    all_sections = set(current_sections) | (set(baseline_sections) if apply_deletions else set())
    for section in all_sections:
        target = section_files.get(section) or canonical_target_for_section(section)
        target = normalize_config_relpath(target)
        file_change = changes.setdefault(target, {"updates": {}, "delete_options": {}, "delete_sections": []})
        current_options = current_sections.get(section)
        baseline_options = baseline_sections.get(section, {})
        if current_options is None:
            if apply_deletions and section in baseline_sections:
                file_change["delete_sections"].append(section)
            continue
        updates: Dict[str, str] = {}
        for option, value in current_options.items():
            if str(value) != str(baseline_options.get(option, "")) or option not in baseline_options:
                updates[option] = str(value)
        if updates:
            file_change["updates"][section] = updates
        if apply_deletions:
            removed = [option for option in baseline_options if option not in current_options]
            if removed:
                file_change["delete_options"][section] = removed

    baseline_includes = live.get("baseline_includes", {}) if isinstance(live.get("baseline_includes", {}), dict) else {}
    printer_baseline_includes = baseline_includes.get("printer.cfg", [])
    if list(project.get("includes", [])) != list(printer_baseline_includes):
        file_change = changes.setdefault("printer.cfg", {"updates": {}, "delete_options": {}, "delete_sections": []})
        file_change["patch_includes"] = True
        file_change["includes"] = list(project.get("includes", []))
    return changes


def full_generated_cfg_targets(project: Dict[str, Any]) -> Dict[str, bytes]:
    by_target: Dict[str, List[str]] = {}
    for section in project.get("section_order", []):
        options = project.get("sections", {}).get(section, {})
        if not any(str(value).strip() for value in options.values()):
            continue
        by_target.setdefault(canonical_target_for_section(section), []).append(section)
    result: Dict[str, bytes] = {}
    for target, sections in by_target.items():
        if target in {"mmu/addons/blobifier.cfg", "mmu/addons/blobifier_hw.cfg"}:
            # These files must come from the installed Happy Hare version. A generated
            # variable-only replacement would omit required macros and button actions.
            continue
        include_values = project.get("includes", []) if target == "printer.cfg" else []
        result[target] = render_cfg_file(project, sections, include_values, live_header=True)
    if project.get("includes") and "printer.cfg" not in result:
        result["printer.cfg"] = render_cfg_file(project, [], project.get("includes", []), live_header=True)
    return result


def unified_diff_text(path: str, before: bytes, after: bytes) -> str:
    before_text = before.decode("utf-8", errors="replace").splitlines()
    after_text = after.decode("utf-8", errors="replace").splitlines()
    return "\n".join(difflib.unified_diff(
        before_text,
        after_text,
        fromfile=f"live/{path}",
        tofile=f"proposed/{path}",
        lineterm="",
        n=4,
    ))


def build_live_plan(project: Dict[str, Any], apply_deletions: bool = False) -> Dict[str, Any]:
    project = normalize_builder_project(project)
    live = project.get("live", {})
    source_files = live.get("source_files", {}) if isinstance(live.get("source_files", {}), dict) else {}
    entries: List[Dict[str, Any]] = []
    conflicts: List[str] = []
    current_states: Dict[str, Dict[str, Any]] = {}

    if source_files:
        change_sets = project_change_sets(project, apply_deletions)
        candidate_paths = list(change_sets)
        mode = "patch"
        for path in candidate_paths:
            baseline_text = source_files.get(path)
            current = moonraker_download_config(path)
            current_states[path] = {"exists": current is not None, "content": current or b""}
            if baseline_text is not None:
                baseline_bytes = baseline_text.encode("utf-8")
                if current is None:
                    conflicts.append(f"{path} was present in the baseline but is now missing")
                    continue
                if sha256_hex(current) != sha256_hex(baseline_bytes):
                    conflicts.append(f"{path} changed on the printer after the GKCC baseline was loaded")
                    continue
                raw_text = baseline_text
            else:
                if path in {"mmu/addons/blobifier.cfg", "mmu/addons/blobifier_hw.cfg"} and current is None:
                    conflicts.append(
                        f"{path} is missing. Install or copy the version-matched Happy Hare Blobifier addon file, then rebase before editing it"
                    )
                    continue
                raw_text = current.decode("utf-8", errors="replace") if current is not None else ""
            change = change_sets[path]
            candidate_text = patch_cfg_text(
                raw_text,
                change.get("updates", {}),
                change.get("delete_options", {}),
                change.get("delete_sections", []),
                includes=change.get("includes"),
                patch_includes=bool(change.get("patch_includes")),
            )
            candidate = candidate_text.encode("utf-8")
            before = current or b""
            if candidate == before:
                continue
            entries.append({
                "path": path,
                "operation": "modify" if current is not None else "create",
                "mode": mode,
                "before_sha256": sha256_hex(before) if current is not None else None,
                "after_sha256": sha256_hex(candidate),
                "before_size": len(before),
                "after_size": len(candidate),
                "diff": unified_diff_text(path, before, candidate),
                "content": candidate,
                "current_exists": current is not None,
                "current_content": before,
            })
    else:
        mode = "full_replace"
        for path, candidate in full_generated_cfg_targets(project).items():
            current = moonraker_download_config(path)
            before = current or b""
            current_states[path] = {"exists": current is not None, "content": before}
            if candidate == before:
                continue
            entries.append({
                "path": path,
                "operation": "modify" if current is not None else "create",
                "mode": mode,
                "before_sha256": sha256_hex(before) if current is not None else None,
                "after_sha256": sha256_hex(candidate),
                "before_size": len(before),
                "after_size": len(candidate),
                "diff": unified_diff_text(path, before, candidate),
                "content": candidate,
                "current_exists": current is not None,
                "current_content": before,
            })

    return {
        "generated_at": now_iso(),
        "mode": mode,
        "entries": entries,
        "conflicts": conflicts,
        "missing_required": builder_missing_required(project),
        "baseline_at": live.get("baseline_at"),
        "config_root": live_config_root_status(),
    }


def public_live_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    clean = {key: value for key, value in plan.items() if key != "entries"}
    clean["entries"] = [
        {key: value for key, value in entry.items() if key not in {"content", "current_content"}}
        for entry in plan.get("entries", [])
    ]
    clean["changed_files"] = len(clean["entries"])
    return clean


def backup_identifier() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)


def create_live_backup(entries: List[Dict[str, Any]], reason: str, project: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    backup_id = backup_identifier()
    zip_path = BACKUP_DIR / f"{backup_id}.zip"
    manifest = {
        "backup_id": backup_id,
        "created_at": now_iso(),
        "reason": reason,
        "files": [],
    }
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for entry in entries:
            path = normalize_config_relpath(entry["path"])
            existed = bool(entry.get("current_exists"))
            content = bytes(entry.get("current_content", b""))
            manifest["files"].append({
                "path": path,
                "existed": existed,
                "sha256": sha256_hex(content) if existed else None,
                "size": len(content) if existed else 0,
            })
            if existed:
                archive.writestr(f"config/{path}", content)
            if "content" in entry:
                archive.writestr(f"proposed/{path}", bytes(entry["content"]))
        if project is not None:
            archive.writestr("GKCC_PROJECT.json", json.dumps(project, indent=2, ensure_ascii=False).encode("utf-8"))
        archive.writestr("MANIFEST.json", json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8"))
    manifest["archive"] = zip_path.name
    return manifest


def read_backup_manifest(backup_id: str) -> Tuple[Path, Dict[str, Any]]:
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{6}", str(backup_id)):
        raise ValueError("Invalid backup identifier")
    path = BACKUP_DIR / f"{backup_id}.zip"
    if not path.exists():
        raise FileNotFoundError("Backup not found")
    with zipfile.ZipFile(path, "r") as archive:
        manifest = json.loads(archive.read("MANIFEST.json").decode("utf-8"))
    return path, manifest


def list_live_backups() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for path in sorted(BACKUP_DIR.glob("*.zip"), reverse=True)[:100]:
        try:
            with zipfile.ZipFile(path, "r") as archive:
                manifest = json.loads(archive.read("MANIFEST.json").decode("utf-8"))
            rows.append({
                "backup_id": manifest.get("backup_id", path.stem),
                "created_at": manifest.get("created_at"),
                "reason": manifest.get("reason", "Configuration backup"),
                "files": manifest.get("files", []),
                "size": path.stat().st_size,
            })
        except Exception:
            continue
    return rows


def restore_backup_files(backup_id: str) -> Dict[str, Any]:
    path, manifest = read_backup_manifest(backup_id)
    restored: List[str] = []
    with zipfile.ZipFile(path, "r") as archive:
        for item in manifest.get("files", []):
            cfg_path = normalize_config_relpath(item["path"])
            if item.get("existed"):
                content = archive.read(f"config/{cfg_path}")
                moonraker_upload_config(cfg_path, content)
            else:
                moonraker_delete_config(cfg_path)
            restored.append(cfg_path)
    return {"ok": True, "backup_id": backup_id, "restored": restored}


def live_write_is_safe(require_ready: bool = True) -> Tuple[bool, str]:
    with _state_lock:
        connected = bool(_state.get("connected"))
        printer_state = str(_state.get("printer_state", "unknown"))
        print_state = str(_state.get("print_state", "unknown"))
    if print_state in {"printing", "paused"}:
        return False, "A print is active"
    if require_ready and (not connected or printer_state != "ready"):
        return False, "Klipper must be ready before applying new configuration"
    root = live_config_root_status()
    if not root.get("writable"):
        return False, "Moonraker's config root is not writable"
    return True, "ready"


def apply_live_plan(
    project: Dict[str, Any],
    restart: bool,
    apply_deletions: bool,
    allow_incomplete: bool,
    allow_full_replace: bool = False,
) -> Dict[str, Any]:
    if not config().get("allow_live_config_writes", True):
        raise RuntimeError("Live configuration writes are disabled in config.json")
    ok, reason = live_write_is_safe(require_ready=True)
    if not ok:
        raise RuntimeError(reason)
    if not _live_lock.acquire(blocking=False):
        raise RuntimeError("Another live configuration operation is still running")
    try:
        plan = build_live_plan(project, apply_deletions=apply_deletions)
        if plan["conflicts"]:
            raise RuntimeError("Live files changed after the baseline was loaded. Rebase from the printer before applying.")
        if plan["mode"] == "full_replace" and not allow_full_replace:
            raise RuntimeError("No raw live baseline is loaded. Rebase first or explicitly allow full-file replacement.")
        if plan["missing_required"] and not allow_incomplete:
            raise RuntimeError("Guided required values are still missing. Review them or explicitly allow an incomplete apply.")
        if not plan["entries"]:
            return {"ok": True, "changed_files": 0, "message": "No live file changes detected"}
        backup = create_live_backup(plan["entries"], "Before GKCC live apply", project)
        written: List[str] = []
        try:
            for entry in plan["entries"]:
                latest = moonraker_download_config(entry["path"])
                expected_exists = bool(entry.get("current_exists"))
                if (latest is not None) != expected_exists or (latest or b"") != bytes(entry.get("current_content", b"")):
                    raise RuntimeError(
                        f"{entry['path']} changed after the live plan was prepared; transaction cancelled"
                    )
                moonraker_upload_config(entry["path"], entry["content"])
                written.append(entry["path"])
        except Exception as exc:
            restore_result = restore_backup_files(backup["backup_id"])
            add_record({
                "workflow_id": "live_config_apply",
                "title": "Live configuration upload rolled back",
                "completed_at": now_iso(),
                "results": {
                    "backup_id": backup["backup_id"],
                    "files_written_before_failure": written,
                    "restore": restore_result,
                    "error": str(exc),
                },
                "notes": "GKCC restored the transaction backup after an upload or last-moment conflict failure.",
            })
            return {
                "ok": False,
                "rolled_back": True,
                "backup_id": backup["backup_id"],
                "changed_files": len(written),
                "files": written,
                "error": f"Live upload failed and the backup was restored: {exc}",
            }

        restart_result: Optional[Dict[str, Any]] = None
        rolled_back = False
        if restart:
            restart_result = restart_klipper_and_wait()
            if not restart_result.get("ok"):
                restore_backup_files(backup["backup_id"])
                rolled_back = True
                rollback_restart = restart_klipper_and_wait()
                add_record({
                    "workflow_id": "live_config_apply",
                    "title": "Live configuration apply rolled back",
                    "completed_at": now_iso(),
                    "results": {
                        "backup_id": backup["backup_id"],
                        "files": written,
                        "failed_restart": restart_result,
                        "rollback_restart": rollback_restart,
                    },
                    "notes": "GKCC automatically restored every changed file after Klipper failed to return ready.",
                })
                return {
                    "ok": False,
                    "rolled_back": True,
                    "backup_id": backup["backup_id"],
                    "changed_files": len(written),
                    "restart": restart_result,
                    "rollback_restart": rollback_restart,
                    "error": "Klipper rejected the proposed configuration; the backup was restored automatically.",
                }

        add_record({
            "workflow_id": "live_config_apply",
            "title": "Live configuration applied",
            "completed_at": now_iso(),
            "results": {
                "backup_id": backup["backup_id"],
                "files": written,
                "restart": restart_result,
                "mode": plan["mode"],
            },
            "notes": "Each changed file was backed up before upload through Moonraker.",
        })
        return {
            "ok": True,
            "rolled_back": rolled_back,
            "backup_id": backup["backup_id"],
            "changed_files": len(written),
            "files": written,
            "restart": restart_result,
        }
    finally:
        _live_lock.release()

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
    builder = read_json(BUILDER_PROJECT_PATH, DEFAULT_BUILDER_PROJECT)
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
    builder_missing = builder_missing_required(builder)
    builder_sections = len(builder.get("sections", {}))
    ellis_done = sum(1 for item in builder.get("wizard", {}).get("ellis", {}).values() if isinstance(item, dict) and item.get("done"))
    erfc_done = sum(1 for item in builder.get("wizard", {}).get("erfc", {}).values() if isinstance(item, dict) and item.get("done"))
    blobifier_done = sum(1 for item in builder.get("wizard", {}).get("blobifier", {}).values() if isinstance(item, dict) and item.get("done"))
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
<h2>Configuration builder</h2><div class='grid'>
<div class='card'><strong>Project</strong><br>{esc(builder.get('meta',{}).get('project_name',''))}<br>Source: {esc(builder.get('meta',{}).get('source_filename',''))}<br>Sections preserved: {esc(builder_sections)}</div>
<div class='card'><strong>Readiness</strong><br>Missing guided required values: {esc(len(builder_missing))}<br>Ellis steps complete: {esc(ellis_done)}<br>ERCF steps complete: {esc(erfc_done)}<br>Blobifier steps complete: {esc(blobifier_done)}</div>
</div><pre>{esc(json.dumps({'missing_required': builder_missing, 'import_warnings': builder.get('import_warnings', [])}, ensure_ascii=False, indent=2))}</pre>
<h2>Live configuration snapshot</h2><p>Captured: <span class='status'>{esc(captured)}</span></p><pre>{esc(json.dumps(config_status, ensure_ascii=False, indent=2)[:50000])}</pre>
<h2>Calibration records</h2>{''.join(run_rows)}
<h2>Notes</h2><p>{esc(printer.get('notes',''))}</p>
</body></html>"""
    return doc.encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "GKCC/0.4.3"

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
        if length > 32_000_000:
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
        if path == "/api/builder/schema":
            self.send_json(builder_schema())
            return
        if path == "/api/builder/ellis":
            self.send_json(read_json(ELLIS_GUIDE_PATH, {"steps": []}))
            return
        if path == "/api/builder/erfc":
            self.send_json(read_json(ERFC_GUIDE_PATH, {"steps": []}))
            return
        if path == "/api/builder/blobifier":
            self.send_json(read_json(BLOBIFIER_GUIDE_PATH, {"steps": []}))
            return
        if path == "/api/builder/project":
            project = read_json(BUILDER_PROJECT_PATH, DEFAULT_BUILDER_PROJECT)
            self.send_json({"project": project, "missing_required": builder_missing_required(project)})
            return
        if path == "/api/live/status":
            try:
                root_status = live_config_root_status()
            except Exception as exc:
                root_status = {
                    "available": False,
                    "writable": False,
                    "permissions": "",
                    "path": None,
                    "error": str(exc),
                }
            self.send_json({
                "config_root": root_status,
                "allow_live_config_writes": bool(config().get("allow_live_config_writes", True)),
                "backups": len(list_live_backups()),
            })
            return
        if path == "/api/live/backups":
            if not self.require_token():
                return
            self.send_json({"backups": list_live_backups()})
            return
        if path == "/api/live/backup":
            if not self.require_token():
                return
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            backup_id = str(query.get("backup_id", [""])[0])
            try:
                backup_path, _ = read_backup_manifest(backup_id)
            except (ValueError, FileNotFoundError) as exc:
                self.send_json({"ok": False, "error": str(exc)}, 404)
                return
            body = backup_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f"attachment; filename=GKCC-backup-{backup_id}.zip")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/export":
            payload = {
                "exported_at": now_iso(),
                "profile": read_json(PROFILE_PATH, DEFAULT_PROFILE),
                "records": read_json(RECORDS_PATH, DEFAULT_RECORDS),
                "snapshot": read_json(SNAPSHOT_PATH, {}),
                "builder_project": read_json(BUILDER_PROJECT_PATH, DEFAULT_BUILDER_PROJECT),
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
            if path == "/api/builder/import":
                data = self.read_json()
                incoming_files = data.get("files")
                if isinstance(incoming_files, list) and incoming_files:
                    parsed: List[Dict[str, Any]] = []
                    filenames: List[str] = []
                    for item in incoming_files[:20]:
                        if not isinstance(item, dict):
                            continue
                        filename = str(item.get("filename", "config.cfg"))[:255]
                        content = str(item.get("content", ""))
                        parsed.append(parse_klipper_cfg(content, filename))
                        filenames.append(filename)
                    if not parsed:
                        raise ValueError("No readable configuration files supplied")
                    project = merge_builder_projects(parsed, filenames)
                else:
                    text = str(data.get("content", ""))
                    filename = str(data.get("filename", "printer.cfg"))[:255]
                    project = parse_klipper_cfg(text, filename)
                atomic_write_json(BUILDER_PROJECT_PATH, project)
                self.send_json({"ok": True, "project": project, "missing_required": builder_missing_required(project)})
                return
            if path == "/api/builder/project":
                project = normalize_builder_project(self.read_json())
                atomic_write_json(BUILDER_PROJECT_PATH, project)
                self.send_json({"ok": True, "project": project, "missing_required": builder_missing_required(project)})
                return
            if path == "/api/builder/reset":
                project = copy.deepcopy(DEFAULT_BUILDER_PROJECT)
                project["updated_at"] = now_iso()
                atomic_write_json(BUILDER_PROJECT_PATH, project)
                self.send_json({"ok": True, "project": project, "missing_required": builder_missing_required(project)})
                return
            if path == "/api/builder/export":
                project = normalize_builder_project(self.read_json())
                atomic_write_json(BUILDER_PROJECT_PATH, project)
                body = builder_export_zip(project)
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", "attachment; filename=GKCC-config-draft.zip")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/api/builder/live/rebase":
                if not self.require_token():
                    return
                data = self.read_json()
                project = normalize_builder_project(data)
                requested_paths = data.get("paths")
                paths = [str(item) for item in requested_paths] if isinstance(requested_paths, list) else None
                project = rebase_project_from_live(project, paths)
                atomic_write_json(BUILDER_PROJECT_PATH, project)
                self.send_json({
                    "ok": True,
                    "project": project,
                    "missing_required": builder_missing_required(project),
                    "baseline_at": project.get("live", {}).get("baseline_at"),
                    "files": project.get("live", {}).get("file_order", []),
                })
                return
            if path == "/api/builder/live/preview":
                if not self.require_token():
                    return
                data = self.read_json()
                project = normalize_builder_project(data)
                atomic_write_json(BUILDER_PROJECT_PATH, project)
                plan = build_live_plan(project, apply_deletions=bool(data.get("apply_deletions", False)))
                self.send_json({"ok": True, "plan": public_live_plan(plan)})
                return
            if path == "/api/builder/live/apply":
                if not self.require_token():
                    return
                data = self.read_json()
                if str(data.get("confirmation", "")) != "APPLY":
                    raise ValueError("Type APPLY to confirm the live configuration update")
                project = normalize_builder_project(data)
                atomic_write_json(BUILDER_PROJECT_PATH, project)
                result = apply_live_plan(
                    project,
                    restart=bool(data.get("restart", True)),
                    apply_deletions=bool(data.get("apply_deletions", False)),
                    allow_incomplete=bool(data.get("allow_incomplete", False)),
                    allow_full_replace=bool(data.get("allow_full_replace", False)),
                )
                self.send_json(result, 200 if result.get("ok") else 409)
                return
            if path == "/api/live/restore":
                if not self.require_token():
                    return
                data = self.read_json()
                if str(data.get("confirmation", "")) != "RESTORE":
                    raise ValueError("Type RESTORE to confirm backup restoration")
                ok, reason = live_write_is_safe(require_ready=False)
                if not ok:
                    raise RuntimeError(reason)
                backup_id = str(data.get("backup_id", ""))
                _, manifest = read_backup_manifest(backup_id)
                current_entries: List[Dict[str, Any]] = []
                for item in manifest.get("files", []):
                    cfg_path = normalize_config_relpath(item["path"])
                    current = moonraker_download_config(cfg_path)
                    current_entries.append({
                        "path": cfg_path,
                        "current_exists": current is not None,
                        "current_content": current or b"",
                    })
                safety_backup = create_live_backup(current_entries, f"Before restoring {backup_id}")
                restored = restore_backup_files(backup_id)
                restart_result = restart_klipper_and_wait() if bool(data.get("restart", True)) else None
                add_record({
                    "workflow_id": "live_config_restore",
                    "title": "Live configuration backup restored",
                    "completed_at": now_iso(),
                    "results": {
                        "restored_backup_id": backup_id,
                        "safety_backup_id": safety_backup["backup_id"],
                        "files": restored.get("restored", []),
                        "restart": restart_result,
                    },
                    "notes": "GKCC created a new safety backup before restoring the selected archive.",
                })
                self.send_json({
                    "ok": True,
                    "restored_backup_id": backup_id,
                    "safety_backup_id": safety_backup["backup_id"],
                    "files": restored.get("restored", []),
                    "restart": restart_result,
                })
                return
            if path == "/api/action/capture_position":
                if not self.require_token():
                    return
                ok, reason = printer_is_idle()
                if not ok:
                    raise RuntimeError(reason)
                require_homed_axes("xyz")
                self.send_json({"ok": True, "position": position_snapshot()})
                return
            if path == "/api/action/jog_xyz":
                if not self.require_token():
                    return
                data = self.read_json()
                axis = str(data.get("axis", ""))
                distance = float(data.get("distance", 0.0))
                speed = float(data.get("speed", 5.0))
                self.send_json(jog_axis(axis, distance, speed))
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
            if path == "/api/action/blobifier":
                if not self.require_token():
                    return
                data = self.read_json()
                action = str(data.get("action", "")).strip().lower()
                if action == "servo_in":
                    script, name = "BLOBIFIER_SERVO POS=in", "Blobifier tray in"
                elif action == "servo_out":
                    script, name = "BLOBIFIER_SERVO POS=out", "Blobifier tray out"
                elif action == "clean":
                    script, name = "BLOBIFIER_CLEAN", "Blobifier brush test"
                elif action == "shake":
                    shakes = int(data.get("shakes", 2))
                    if not 1 <= shakes <= 10:
                        raise ValueError("Shake count must be between 1 and 10")
                    script, name = f"BLOBIFIER_SHAKE_BUCKET SHAKES={shakes}", "Blobifier bucket shake"
                elif action == "test_blob":
                    purge_length = float(data.get("purge_length", 30))
                    if not 5 <= purge_length <= 200:
                        raise ValueError("Test purge length must be between 5 and 200 mm")
                    if str(data.get("confirmation", "")) != "BLOB":
                        raise ValueError("Type BLOB to confirm a heated purge and machine movement")
                    script, name = f"BLOBIFIER PURGE_LENGTH={purge_length:.1f}", "Blobifier supervised test blob"
                else:
                    raise ValueError("Unknown Blobifier action")
                self.send_json({"ok": True, "result": run_gcode(script, name), "script": script})
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
    print(f"GKCC Calibration Center v0.4.3: http://0.0.0.0:{port}")
    print("Live configuration writes use backup, diff review, restart validation, and rollback.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
