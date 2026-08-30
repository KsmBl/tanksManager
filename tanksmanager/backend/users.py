"""Logged-on users, plus per-user resource totals (the Users tab)."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

import psutil


@dataclass(slots=True)
class UserInfo:
    name: str
    terminal: str
    host: str
    started: float
    session_id: str
    seat: str
    kind: str
    procs: int = 0
    cpu: float = 0.0
    rss: int = 0


def _loginctl_sessions() -> dict:
    """terminal/user -> (session id, seat, type). Best effort."""
    if not shutil.which("loginctl"):
        return {}
    try:
        res = subprocess.run(
            ["loginctl", "list-sessions", "--no-pager", "--no-legend", "--output=json"],
            capture_output=True, text=True, timeout=5)
        import json
        data = json.loads(res.stdout or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}
    out = {}
    for row in data:
        out[str(row.get("session", ""))] = (
            row.get("user", ""), row.get("seat") or "", row.get("tty") or "")
    return out


def list_users(procs=()) -> list:
    sessions = _loginctl_sessions()
    by_user = {}
    for p in procs:
        acc = by_user.setdefault(p.username, [0, 0.0, 0])
        acc[0] += 1
        acc[1] += p.cpu
        acc[2] += p.rss

    out = []
    seen = set()
    for u in psutil.users():
        sid = ""
        seat = ""
        for key, (user, st, tty) in sessions.items():
            if user == u.name and (tty == u.terminal or not u.terminal):
                sid, seat = key, st
                break
        totals = by_user.get(u.name, [0, 0.0, 0])
        kind = "Remote" if u.host and u.host not in ("", ":0", "localhost") else "Local"
        out.append(UserInfo(
            name=u.name, terminal=u.terminal or "", host=u.host or "",
            started=u.started, session_id=sid, seat=seat, kind=kind,
            procs=totals[0], cpu=totals[1], rss=totals[2],
        ))
        seen.add((u.name, u.terminal))

    # Users with processes but no login record (system daemons, graphical
    # sessions psutil cannot see) still deserve a row.
    for name, totals in sorted(by_user.items()):
        if any(u.name == name for u in out):
            continue
        out.append(UserInfo(name=name, terminal="", host="", started=0.0,
                            session_id="", seat="", kind="Service",
                            procs=totals[0], cpu=totals[1], rss=totals[2]))
    return out


def logoff(session_id: str) -> list:
    if not session_id:
        return ["That user has no systemd-logind session to end."]
    try:
        res = subprocess.run(["loginctl", "terminate-session", session_id],
                             capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        return [str(exc)]
    return [(res.stderr or "").strip() or "Failed."] if res.returncode else []
