"""The 'Applications' tab data source: open windows, not processes.

Backends, in order of preference:
  * i3/sway IPC   - works on Wayland (sway) and X11 (i3), no extra deps
  * wmctrl        - any EWMH-compliant X11 window manager
Anything else reports "unsupported" and the tab explains why.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import struct
import subprocess
from dataclasses import dataclass

MAGIC = b"i3-ipc"
RUN_COMMAND = 0
GET_TREE = 4


@dataclass(slots=True)
class WindowInfo:
    handle: str          # backend-specific identifier used by activate/close
    title: str
    pid: int
    app_id: str
    workspace: str
    focused: bool
    urgent: bool
    minimised: bool


class Backend:
    name = "none"
    available = False
    reason = "No supported window manager was detected."

    def list_windows(self) -> list:
        return []

    def activate(self, handle) -> list:
        return ["Not supported."]

    def close(self, handle) -> list:
        return ["Not supported."]


class SwayBackend(Backend):
    name = "sway/i3"

    def __init__(self):
        self.path = os.environ.get("SWAYSOCK") or os.environ.get("I3SOCK") or ""
        if not self.path and shutil.which("i3"):
            try:
                self.path = subprocess.run(["i3", "--get-socketpath"],
                                           capture_output=True, text=True,
                                           timeout=2).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                self.path = ""
        self.available = bool(self.path) and os.path.exists(self.path)
        self.reason = "" if self.available else "No sway/i3 IPC socket in the environment."

    def _request(self, mtype: int, payload: bytes = b"") -> object:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(2.0)
            sock.connect(self.path)
            sock.sendall(MAGIC + struct.pack("=II", len(payload), mtype) + payload)
            header = b""
            while len(header) < 14:
                chunk = sock.recv(14 - len(header))
                if not chunk:
                    raise OSError("IPC connection closed")
                header += chunk
            length, _rtype = struct.unpack("=II", header[6:14])
            body = b""
            while len(body) < length:
                chunk = sock.recv(length - len(body))
                if not chunk:
                    raise OSError("IPC connection closed")
                body += chunk
        return json.loads(body or b"null")

    def list_windows(self) -> list:
        try:
            tree = self._request(GET_TREE)
        except (OSError, ValueError):
            return []
        out = []

        def walk(node, workspace):
            if node.get("type") == "workspace":
                workspace = node.get("name", workspace)
            is_window = node.get("pid") is not None and (
                node.get("app_id") or node.get("window_properties") or node.get("window"))
            if is_window and node.get("type") in ("con", "floating_con"):
                props = node.get("window_properties") or {}
                out.append(WindowInfo(
                    handle=str(node.get("id")),
                    title=node.get("name") or "(untitled)",
                    pid=int(node.get("pid") or 0),
                    app_id=node.get("app_id") or props.get("class") or "",
                    workspace=workspace,
                    focused=bool(node.get("focused")),
                    urgent=bool(node.get("urgent")),
                    minimised=bool(node.get("scratchpad_state", "none") != "none"),
                ))
            for key in ("nodes", "floating_nodes"):
                for child in node.get(key) or ():
                    walk(child, workspace)

        walk(tree or {}, "")
        return out

    def _command(self, cmd: str) -> list:
        try:
            reply = self._request(RUN_COMMAND, cmd.encode())
        except (OSError, ValueError) as exc:
            return [str(exc)]
        errors = []
        for item in reply or ():
            if not item.get("success", True):
                errors.append(item.get("error", "command failed"))
        return errors

    def activate(self, handle) -> list:
        return self._command(f"[con_id={handle}] focus")

    def close(self, handle) -> list:
        return self._command(f"[con_id={handle}] kill")


class WmctrlBackend(Backend):
    name = "wmctrl"

    def __init__(self):
        self.available = bool(shutil.which("wmctrl")) and bool(os.environ.get("DISPLAY"))
        self.reason = "" if self.available else "wmctrl is not installed, or this is not an X11 session."

    def list_windows(self) -> list:
        try:
            raw = subprocess.run(["wmctrl", "-lpx"], capture_output=True,
                                 text=True, timeout=3).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        out = []
        for line in raw.splitlines():
            parts = line.split(None, 5)
            if len(parts) < 6:
                continue
            wid, desktop, pid, wmclass, _host, title = parts
            out.append(WindowInfo(
                handle=wid, title=title, pid=int(pid) if pid.isdigit() else 0,
                app_id=wmclass.split(".")[-1], workspace=desktop,
                focused=False, urgent=False, minimised=desktop == "-1",
            ))
        return out

    def _run(self, args) -> list:
        try:
            res = subprocess.run(["wmctrl"] + args, capture_output=True,
                                 text=True, timeout=3)
        except (OSError, subprocess.SubprocessError) as exc:
            return [str(exc)]
        return [res.stderr.strip()] if res.returncode else []

    def activate(self, handle) -> list:
        return self._run(["-i", "-a", handle])

    def close(self, handle) -> list:
        return self._run(["-i", "-c", handle])


def detect() -> Backend:
    for cls in (SwayBackend, WmctrlBackend):
        backend = cls()
        if backend.available:
            return backend
    fallback = Backend()
    if os.environ.get("XDG_SESSION_TYPE") == "wayland":
        fallback.reason = (
            "Wayland compositors do not let an application enumerate other "
            "windows. Tanks Manager supports sway and i3 through their IPC "
            "socket; on X11 install wmctrl. Everything else in this window "
            "works regardless."
        )
    return fallback
