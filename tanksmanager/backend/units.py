"""Human formatting helpers. Kept unit-consistent with the XP/7 original:
memory in the tables is KiB/MiB, network rates are per second."""

from __future__ import annotations

import time


def bytes_h(n, digits=1) -> str:
    if n is None:
        return ""
    n = float(n)
    for unit in ("B", "K", "M", "G", "T"):
        if abs(n) < 1024.0 or unit == "T":
            if unit == "B":
                return f"{int(n)} B"
            return f"{n:.{digits}f} {unit}iB"
        n /= 1024.0
    return f"{n:.{digits}f} TiB"


def kib(n) -> str:
    """Windows Task Manager showed memory as '123,456 K'."""
    if n is None:
        return ""
    return f"{int(n) // 1024:,} K"


def bytes_pair(used, total, digits=1) -> str:
    """'1.9/15.4 GiB' - the unit is only printed once when both values share
    it, which is what makes the reading fit on a card."""
    left, right = bytes_h(used, digits), bytes_h(total, digits)
    lnum, _, lunit = left.rpartition(" ")
    rnum, _, runit = right.rpartition(" ")
    if lunit and lunit == runit:
        return f"{lnum}/{rnum} {runit}"
    return f"{left} / {right}"


def rate(n) -> str:
    if not n:
        return ""
    return bytes_h(n, 1) + "/s"


def bits_rate(n) -> str:
    n = float(n) * 8.0
    for unit in ("bit/s", "Kbit/s", "Mbit/s", "Gbit/s"):
        if abs(n) < 1000.0 or unit == "Gbit/s":
            return f"{n:.1f} {unit}"
        n /= 1000.0
    return f"{n:.1f} Gbit/s"


def percent(v, digits=0) -> str:
    return f"{v:.{digits}f} %"


def duration(seconds) -> str:
    seconds = int(max(0, seconds))
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d:
        return f"{d}:{h:02d}:{m:02d}:{s:02d}"
    return f"{h:02d}:{m:02d}:{s:02d}"


def cpu_time(seconds) -> str:
    seconds = float(max(0.0, seconds))
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


def wallclock(epoch) -> str:
    if not epoch:
        return ""
    lt = time.localtime(epoch)
    if time.time() - epoch < 20 * 3600:
        return time.strftime("%H:%M:%S", lt)
    return time.strftime("%Y-%m-%d %H:%M", lt)


NICE_LABELS = [
    (-20, "Realtime"),
    (-10, "High"),
    (-5, "Above normal"),
    (0, "Normal"),
    (5, "Below normal"),
    (19, "Low"),
]


def nice_label(nice: int) -> str:
    """Map a Unix nice value onto the XP/7 priority vocabulary."""
    if nice <= -15:
        base = "Realtime"
    elif nice < -4:
        base = "High"
    elif nice < 0:
        base = "Above normal"
    elif nice == 0:
        return "Normal"
    elif nice <= 9:
        base = "Below normal"
    else:
        base = "Low"
    return f"{base} ({nice:+d})"
