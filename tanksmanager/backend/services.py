"""systemd units for the Services tab (system scope and the user session)."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass

HAVE_SYSTEMCTL = bool(shutil.which("systemctl"))


@dataclass(slots=True)
class ServiceInfo:
    unit: str
    name: str
    description: str
    load: str
    active: str
    sub: str
    scope: str          # "system" or "user"
    pid: int = 0


def _systemctl(args, scope="system", timeout=10):
    argv = ["systemctl"]
    if scope == "user":
        argv.append("--user")
    argv += args
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def list_units(scope="system") -> list:
    if not HAVE_SYSTEMCTL:
        return []
    try:
        res = _systemctl(["list-units", "--type=service", "--all",
                          "--no-pager", "--no-legend", "--output=json"], scope)
    except (OSError, subprocess.SubprocessError):
        return []
    # `list-units` exits non-zero when some unit is failed; the JSON is still good.
    try:
        data = json.loads(res.stdout or "[]")
    except ValueError:
        return []
    out = []
    for row in data:
        unit = row.get("unit", "")
        out.append(ServiceInfo(
            unit=unit,
            name=unit[:-8] if unit.endswith(".service") else unit,
            description=row.get("description", ""),
            load=row.get("load", ""),
            active=row.get("active", ""),
            sub=row.get("sub", ""),
            scope=scope,
        ))
    return out


def main_pids(units, scope="system") -> dict:
    """One systemctl call for every running unit's MainPID."""
    if not units or not HAVE_SYSTEMCTL:
        return {}
    try:
        res = _systemctl(["show", "--property=Id", "--property=MainPID"] + list(units), scope)
    except (OSError, subprocess.SubprocessError):
        return {}
    out, current = {}, {}
    for line in res.stdout.splitlines():
        if not line.strip():
            if current.get("Id"):
                try:
                    out[current["Id"]] = int(current.get("MainPID", 0))
                except ValueError:
                    pass
            current = {}
            continue
        key, _, value = line.partition("=")
        current[key] = value
    if current.get("Id"):
        try:
            out[current["Id"]] = int(current.get("MainPID", 0))
        except ValueError:
            pass
    return out


def control(action: str, unit: str, scope="system") -> list:
    """start/stop/restart/enable/disable. Auth goes through polkit, which needs
    an authentication agent running in the session - if there is none, systemd
    says so and we pass that straight through."""
    if not HAVE_SYSTEMCTL:
        return ["systemctl is not available on this system."]
    try:
        res = _systemctl([action, unit], scope, timeout=30)
    except subprocess.TimeoutExpired:
        return [f"'systemctl {action} {unit}' timed out."]
    except OSError as exc:
        return [str(exc)]
    if res.returncode:
        return [(res.stderr or res.stdout).strip() or f"systemctl {action} failed."]
    return []


def unit_status(unit: str, scope="system") -> str:
    try:
        res = _systemctl(["status", "--no-pager", "--lines=40", unit], scope)
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc)
    return res.stdout or res.stderr
