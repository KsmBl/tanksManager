"""GPU telemetry.

Linux has no single source for this, so three are tried in order:

  1. ``device/gpu_busy_percent``  - amdgpu exposes it directly, no root needed
  2. DRM ``fdinfo`` accounting    - i915/xe/amdgpu/panfrost and friends publish
                                    per-engine busy nanoseconds per open client
  3. ``nvidia-smi``               - the proprietary stack

The fdinfo route only sees processes this user is allowed to inspect, which on
a desktop is the interesting ones. A full scan of /proc measures about 7ms, so
it is cheap enough to do on every sample from the worker thread.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field

VENDORS = {
    "0x8086": "Intel", "0x1002": "AMD", "0x1022": "AMD",
    "0x10de": "NVIDIA", "0x15ad": "VMware", "0x1af4": "Red Hat",
    "0x1414": "Microsoft", "0x13b5": "ARM", "0x5143": "Qualcomm",
}

# Windows names the engines 3D / Copy / Video Decode / Video Encode; the
# kernel uses the driver's own vocabulary.
ENGINE_LABELS = {
    "render": "3D", "rcs": "3D", "gfx": "3D",
    "copy": "Copy", "bcs": "Copy", "blitter": "Copy",
    "video": "Video Decode", "vcs": "Video Decode", "dec": "Video Decode",
    "video-enhance": "Video Enhance", "vecs": "Video Enhance",
    "enc": "Video Encode", "compute": "Compute", "ccs": "Compute",
}


@dataclass(slots=True)
class GpuInfo:
    key: str
    name: str
    driver: str
    pdev: str
    busy: float = 0.0                       # headline utilisation, 0..100
    engines: dict = field(default_factory=dict)   # label -> percent
    mem_used: int = 0
    mem_total: int = 0
    freq: float = 0.0
    freq_max: float = 0.0
    temp: float = 0.0
    clients: int = 0
    source: str = ""                        # how busy was measured
    note: str = ""


def _read(path, default=""):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return default


def _read_int(path, default=0):
    value = _read(path)
    try:
        return int(value.split()[0])
    except (ValueError, IndexError):
        return default


def _lspci_names() -> dict:
    """PCI address -> human readable device name."""
    if not shutil.which("lspci"):
        return {}
    try:
        out = subprocess.run(["lspci", "-mm", "-D"], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    names = {}
    for line in out.splitlines():
        try:
            parts = shlex.split(line)
        except ValueError:
            continue
        if len(parts) < 4:
            continue
        address, _cls, vendor, device = parts[0], parts[1], parts[2], parts[3]
        vendor = vendor.replace(" Corporation", "").replace(" Inc.", "")
        names[address] = f"{vendor} {device}"
    return names


def _engine_label(raw: str) -> str:
    key = raw.lower()
    if key in ENGINE_LABELS:
        return ENGINE_LABELS[key]
    for prefix, label in ENGINE_LABELS.items():
        if key.startswith(prefix):
            return label
    return raw.replace("-", " ").title()


def scan_fdinfo() -> dict:
    """pdev -> {"engines": {name: ns}, "mem": bytes, "clients": n}.

    Several file descriptors can share one drm-client-id and each reports the
    same running totals, so clients are de-duplicated before summing.
    """
    per_dev = {}
    seen = {}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        directory = f"/proc/{pid}/fdinfo"
        try:
            entries = os.listdir(directory)
        except OSError:
            continue                        # gone, or not ours to look at
        for fd in entries:
            try:
                with open(f"{directory}/{fd}", "rb") as fh:
                    blob = fh.read(8192)
            except OSError:
                continue
            if b"drm-engine" not in blob:
                continue
            pdev = ""
            client = ""
            engines = {}
            memory = 0
            for line in blob.decode("utf-8", "replace").splitlines():
                key, _, value = line.partition(":")
                value = value.strip()
                if key == "drm-pdev":
                    pdev = value
                elif key == "drm-client-id":
                    client = value
                elif key.startswith("drm-engine-"):
                    try:
                        engines[key[len("drm-engine-"):]] = int(value.split()[0])
                    except (ValueError, IndexError):
                        pass
                elif key.startswith("drm-resident-"):
                    try:
                        memory += int(value.split()[0])
                    except (ValueError, IndexError):
                        pass
            if not pdev or not engines:
                continue
            ident = (pdev, client or f"{pid}/{fd}")
            if ident in seen:
                continue
            seen[ident] = True
            bucket = per_dev.setdefault(pdev, {"engines": {}, "mem": 0, "clients": 0})
            bucket["clients"] += 1
            bucket["mem"] += memory
            for name, ns in engines.items():
                bucket["engines"][name] = bucket["engines"].get(name, 0) + ns
    return per_dev


class GpuSampler:
    def __init__(self):
        self._names = _lspci_names()
        self._names_stale = False
        self._cards = self._detect()
        self._prev = {}                     # pdev -> {engine: ns}
        self._prev_ts = time.monotonic()
        self._nvidia = shutil.which("nvidia-smi")

    def _detect(self):
        cards = []
        self._names_stale = False
        try:
            entries = sorted(os.listdir("/sys/class/drm"))
        except OSError:
            return cards
        for entry in entries:
            # Connectors are named card1-HDMI-A-1 and are not devices.
            if not entry.startswith("card") or "-" in entry:
                continue
            base = f"/sys/class/drm/{entry}"
            try:
                pdev = os.path.basename(os.path.realpath(f"{base}/device"))
            except OSError:
                continue
            driver = ""
            try:
                driver = os.path.basename(os.path.realpath(f"{base}/device/driver"))
            except OSError:
                pass
            vendor = _read(f"{base}/device/vendor")
            if pdev not in self._names and not self._names_stale:
                # An adapter we have never seen: refresh the PCI name table
                # once so a hotplugged card gets its real name, not "AMD GPU".
                self._names_stale = True
                self._names = _lspci_names()
            name = self._names.get(pdev) or f"{VENDORS.get(vendor, 'Unknown')} GPU"
            cards.append({"card": entry, "base": base, "pdev": pdev,
                          "driver": driver, "name": name})
        return cards

    def _hwmon_temp(self, base):
        hwmon = f"{base}/device/hwmon"
        try:
            for entry in os.listdir(hwmon):
                value = _read_int(f"{hwmon}/{entry}/temp1_input")
                if value:
                    return value / 1000.0
        except OSError:
            pass
        return 0.0

    def _nvidia_stats(self):
        if not self._nvidia:
            return {}
        try:
            out = subprocess.run(
                [self._nvidia, "--query-gpu=pci.bus_id,utilization.gpu,"
                 "memory.used,memory.total,clocks.sm,temperature.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            return {}
        stats = {}
        for line in out.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 6:
                continue
            try:
                stats[parts[0].lower()] = {
                    "busy": float(parts[1]),
                    "mem_used": int(float(parts[2])) * 1024 * 1024,
                    "mem_total": int(float(parts[3])) * 1024 * 1024,
                    "freq": float(parts[4]),
                    "temp": float(parts[5]),
                }
            except ValueError:
                continue
        return stats

    def sample(self) -> list:
        # Re-detected every sample (well under a millisecond) so an adapter
        # that appears or disappears - an eGPU, a USB display adapter - is
        # picked up the same way a hotplugged drive is.
        self._cards = self._detect()
        live = {card["pdev"] for card in self._cards}
        for gone in set(self._prev) - live:
            self._prev.pop(gone, None)
        if not self._cards:
            return []
        now = time.monotonic()
        elapsed = max(1e-6, now - self._prev_ts)
        self._prev_ts = now

        need_fdinfo = any(
            not os.path.exists(f"{c['base']}/device/gpu_busy_percent")
            and c["driver"] != "nvidia" for c in self._cards)
        fdinfo = scan_fdinfo() if need_fdinfo else {}
        nvidia = self._nvidia_stats() if any(
            c["driver"] == "nvidia" for c in self._cards) else {}

        out = []
        for card in self._cards:
            base, pdev = card["base"], card["pdev"]
            info = GpuInfo(key=pdev or card["card"], name=card["name"],
                           driver=card["driver"] or "unknown", pdev=pdev)
            info.freq = float(_read_int(f"{base}/gt_act_freq_mhz")
                              or _read_int(f"{base}/device/pp_dpm_sclk", 0))
            info.freq_max = float(_read_int(f"{base}/gt_max_freq_mhz"))
            info.temp = self._hwmon_temp(base)

            busy_path = f"{base}/device/gpu_busy_percent"
            nv = nvidia.get(pdev.lower())
            if os.path.exists(busy_path):
                info.busy = float(_read_int(busy_path))
                info.source = "gpu_busy_percent"
                info.mem_used = _read_int(f"{base}/device/mem_info_vram_used")
                info.mem_total = _read_int(f"{base}/device/mem_info_vram_total")
            elif nv:
                info.busy = nv["busy"]
                info.mem_used, info.mem_total = nv["mem_used"], nv["mem_total"]
                info.freq = info.freq or nv["freq"]
                info.temp = info.temp or nv["temp"]
                info.source = "nvidia-smi"
            else:
                bucket = fdinfo.get(pdev)
                info.source = "drm fdinfo"
                info.note = ("Engine time is summed from the processes this "
                             "user may inspect.")
                if bucket:
                    prev = self._prev.get(pdev, {})
                    engines = {}
                    for name, ns in bucket["engines"].items():
                        delta = ns - prev.get(name, ns)
                        if delta < 0:           # a client exited; totals reset
                            delta = 0
                        pct = min(100.0, delta / (elapsed * 1e9) * 100.0)
                        engines[_engine_label(name)] = max(
                            engines.get(_engine_label(name), 0.0), pct)
                    info.engines = engines
                    info.busy = max(engines.values()) if engines else 0.0
                    info.mem_used = bucket["mem"]
                    info.clients = bucket["clients"]
                    self._prev[pdev] = dict(bucket["engines"])
                else:
                    self._prev.pop(pdev, None)
            out.append(info)
        return out
