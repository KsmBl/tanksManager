"""Actions taken against processes. Every one of these can fail for perfectly
normal reasons (permissions, race with exit), so they all report back rather
than raise."""

from __future__ import annotations

import os
import shlex
import signal
import subprocess

import psutil

SIGNALS = [
    ("Terminate", signal.SIGTERM, "Ask the process to exit (SIGTERM)"),
    ("Kill", signal.SIGKILL, "Force the process to exit (SIGKILL)"),
    ("Interrupt", signal.SIGINT, "As if you pressed Ctrl+C (SIGINT)"),
    ("Hang up", signal.SIGHUP, "SIGHUP - many daemons reload their config"),
    ("Quit", signal.SIGQUIT, "SIGQUIT"),
    ("Stop", signal.SIGSTOP, "Suspend the process (SIGSTOP)"),
    ("Continue", signal.SIGCONT, "Resume a suspended process (SIGCONT)"),
]

PRIORITIES = [
    ("Realtime", -20),
    ("High", -10),
    ("Above normal", -5),
    ("Normal", 0),
    ("Below normal", 5),
    ("Low", 19),
]


class ActionError(Exception):
    pass


def _each(pids, fn):
    """Apply fn to every pid, collecting failures instead of stopping."""
    errors = []
    for pid in pids:
        try:
            fn(pid)
        except psutil.NoSuchProcess:
            pass  # already gone: that is the outcome we wanted anyway
        except psutil.AccessDenied:
            errors.append(f"PID {pid}: permission denied")
        except (OSError, psutil.Error) as exc:
            errors.append(f"PID {pid}: {exc}")
    return errors


def send_signal(pids, sig) -> list:
    return _each(pids, lambda pid: psutil.Process(pid).send_signal(sig))


def end_task(pids) -> list:
    return send_signal(pids, signal.SIGTERM)


def _tree_pids(pid) -> list:
    try:
        p = psutil.Process(pid)
        return [c.pid for c in p.children(recursive=True)] + [pid]
    except psutil.Error:
        return [pid]


def end_tree(pids) -> list:
    targets = []
    for pid in pids:
        targets.extend(_tree_pids(pid))
    return send_signal(dict.fromkeys(targets), signal.SIGTERM)


def set_nice(pids, nice) -> list:
    return _each(pids, lambda pid: psutil.Process(pid).nice(nice))


def set_affinity(pids, cpus) -> list:
    return _each(pids, lambda pid: psutil.Process(pid).cpu_affinity(list(cpus)))


def get_affinity(pid):
    try:
        return psutil.Process(pid).cpu_affinity()
    except (psutil.Error, AttributeError):
        return None


def open_location(pid) -> list:
    try:
        exe = psutil.Process(pid).exe()
    except psutil.Error as exc:
        return [str(exc)]
    if not exe:
        return ["This process has no executable on disk."]
    for launcher in (["thunar", "--select"], ["nautilus", "--select"],
                     ["dolphin", "--select"], ["nemo"], ["xdg-open"]):
        try:
            target = exe if launcher[0] != "xdg-open" else os.path.dirname(exe)
            subprocess.Popen(launcher + [target],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return []
        except FileNotFoundError:
            continue
    return ["No file manager found."]


def run_new_task(command: str, as_shell: bool = False, cwd: str | None = None) -> list:
    command = command.strip()
    if not command:
        return ["Nothing to run."]
    try:
        if as_shell:
            argv = [os.environ.get("SHELL", "/bin/sh"), "-c", command]
        else:
            argv = shlex.split(command)
        subprocess.Popen(
            argv, cwd=cwd or os.path.expanduser("~"),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except (OSError, ValueError) as exc:
        return [f"Could not start {command!r}: {exc}"]
    return []
