"""Per-physical-drive statistics for the Windows 10 style Disk cards.

Only real hardware is listed: a /sys/block entry counts as a drive when it has
a `device` symlink, which excludes loop, zram and device-mapper nodes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import psutil

SYS_BLOCK = "/sys/block"


@dataclass(slots=True)
class DriveInfo:
    name: str
    model: str
    kind: str                   # "NVMe SSD", "SSD", "HDD", "SD card"
    size: int
    read_bps: float = 0.0
    write_bps: float = 0.0
    read_iops: float = 0.0
    write_iops: float = 0.0
    active: float = 0.0         # percent of the interval the queue was busy
    response_ms: float = 0.0
    mounts: list = field(default_factory=list)   # (mountpoint, fstype, total, used, pct)

    @property
    def total_bps(self) -> float:
        return self.read_bps + self.write_bps


def _read(path, default=""):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return default


def _read_int(path, default=0):
    try:
        return int(_read(path) or default)
    except ValueError:
        return default


def _partition_map():
    """Partition kernel name -> parent disk name, plus dm node -> disks."""
    parts, dm = {}, {}
    try:
        disks = os.listdir(SYS_BLOCK)
    except OSError:
        return parts, dm
    for disk in disks:
        base = f"{SYS_BLOCK}/{disk}"
        if disk.startswith("dm-"):
            try:
                dm[disk] = os.listdir(f"{base}/slaves")
            except OSError:
                dm[disk] = []
            continue
        try:
            for entry in os.listdir(base):
                if os.path.exists(f"{base}/{entry}/partition"):
                    parts[entry] = disk
        except OSError:
            continue
    return parts, dm


def _resolve_disk(device, parts, dm, disks):
    """Map a mounted device path onto the physical drive underneath it."""
    try:
        name = os.path.basename(os.path.realpath(device))
    except OSError:
        return None
    seen = set()
    stack = [name]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        if current in disks:
            return current
        if current in parts:
            return parts[current]
        if current in dm:
            stack.extend(dm[current])
    return None


class DriveSampler:
    def __init__(self):
        self._prev = {}
        self._drives = {}

    def _detect(self):
        found = {}
        try:
            entries = sorted(os.listdir(SYS_BLOCK))
        except OSError:
            return found
        for name in entries:
            base = f"{SYS_BLOCK}/{name}"
            # No `device` symlink means it is not backed by hardware:
            # loop*, zram*, ram*, dm-* all drop out here.
            if not os.path.exists(f"{base}/device"):
                continue
            # A card reader with no card, or a drive on its way out, reports
            # zero sectors. Treating that as "not there" is what makes media
            # insertion and removal behave like plugging a drive in and out.
            size = _read_int(f"{base}/size") * 512
            if size <= 0:
                continue
            model = (_read(f"{base}/device/model") or _read(f"{base}/device/name")
                     or _read(f"{base}/device/id") or name)
            rotational = _read_int(f"{base}/queue/rotational", 1)
            removable = _read_int(f"{base}/removable", 0)
            if name.startswith("nvme"):
                kind = "NVMe SSD"
            elif name.startswith("mmcblk"):
                kind = "SD card"
            elif removable:
                kind = "Removable drive"
            elif rotational:
                kind = "Hard disk"
            else:
                kind = "SSD"
            found[name] = {"model": model, "kind": kind, "size": size}
        return found

    def sample(self, interval: float) -> list:
        # Re-detected on every sample (about 0.1 ms) so a drive plugged in or
        # pulled out while the window is open turns up, or goes away, on the
        # next tick. Nothing here caches the drive list.
        self._drives = self._detect()
        for gone in set(self._prev) - set(self._drives):
            # Drop the counters too: if the kernel later hands the same name
            # to a different device, a stale baseline would invent a huge
            # first delta for it.
            self._prev.pop(gone, None)
        if not self._drives:
            return []
        try:
            counters = psutil.disk_io_counters(perdisk=True)
        except (OSError, RuntimeError):
            counters = {}

        parts, dm = _partition_map()
        disks = set(self._drives)
        usage = {name: [] for name in self._drives}
        try:
            partitions = psutil.disk_partitions(all=False)
        except OSError:
            partitions = []
        for part in partitions:
            owner = _resolve_disk(part.device, parts, dm, disks)
            if owner is None:
                continue
            try:
                u = psutil.disk_usage(part.mountpoint)
            except OSError:
                continue
            usage[owner].append((part.mountpoint, part.fstype,
                                 u.total, u.used, u.percent))

        out = []
        for name, meta in self._drives.items():
            info = DriveInfo(name=name, model=meta["model"], kind=meta["kind"],
                             size=meta["size"], mounts=sorted(usage[name]))
            counter = counters.get(name)
            if counter is not None:
                prev = self._prev.get(name)
                self._prev[name] = counter
                if prev is not None and interval > 0:
                    info.read_bps = max(0.0, (counter.read_bytes - prev.read_bytes) / interval)
                    info.write_bps = max(0.0, (counter.write_bytes - prev.write_bytes) / interval)
                    reads = max(0, counter.read_count - prev.read_count)
                    writes = max(0, counter.write_count - prev.write_count)
                    info.read_iops = reads / interval
                    info.write_iops = writes / interval
                    busy = max(0, getattr(counter, "busy_time", 0)
                               - getattr(prev, "busy_time", 0))
                    info.active = min(100.0, busy / (interval * 1000.0) * 100.0)
                    served = reads + writes
                    if served:
                        spent = (max(0, counter.read_time - prev.read_time)
                                 + max(0, counter.write_time - prev.write_time))
                        info.response_ms = spent / served
            out.append(info)
        return out
