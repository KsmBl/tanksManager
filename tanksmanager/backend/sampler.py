"""Sampling backend.

Everything that touches /proc lives here and runs on a worker thread, so the
GTK main loop never blocks.  The worker produces immutable snapshots that the
UI consumes from an idle callback.
"""

from __future__ import annotations

import os
import time
import threading
from dataclasses import dataclass, field

import psutil

from .drives import DriveSampler
from .gpu import GpuSampler

NPROC = psutil.cpu_count(logical=True) or 1
NPROC_PHYS = psutil.cpu_count(logical=False) or NPROC
CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100

_DEAD = (psutil.NoSuchProcess, psutil.ZombieProcess, ProcessLookupError, FileNotFoundError)

# How often the expensive, slow-moving readings are actually taken, whatever
# the refresh speed is set to. Nothing on a desktop changes temperature in a
# tenth of a second.
TEMP_INTERVAL = 2.0
GPU_INTERVAL = 0.5


@dataclass(slots=True)
class ProcInfo:
    pid: int
    ppid: int
    name: str
    username: str
    status: str
    cpu: float          # normalised: 0..100 over the whole machine (XP/7 style)
    cpu_raw: float      # 0..100*ncores (htop style)
    rss: int
    vms: int
    shared: int
    threads: int
    nice: int
    create_time: float
    cpu_time: float
    read_bps: float
    write_bps: float
    cmdline: str
    exe: str
    terminal: str
    unit: str           # systemd unit / cgroup leaf, "" when there is none
    gpu: float          # 0..100 of the busiest engine, 0 unless measured
    is_own: bool


@dataclass(slots=True)
class NicInfo:
    name: str
    speed: int              # Mbit/s, 0 == unknown
    is_up: bool
    duplex: str
    addr: str
    sent: int
    recv: int
    sent_bps: float
    recv_bps: float
    utilisation: float      # 0..100 of link speed


@dataclass(slots=True)
class SwapDev:
    path: str
    kind: str               # "partition" or "file"
    size: int
    used: int
    priority: int
    is_zram: bool


@dataclass(slots=True)
class ZramDev:
    name: str
    disksize: int           # capacity as seen by the kernel, uncompressed
    orig: int               # uncompressed bytes currently stored
    compressed: int         # those bytes after compression
    mem_used: int           # RAM actually occupied, including metadata
    algorithm: str
    used_as: str            # "swap", a mount point, or "unused"

    @property
    def ratio(self) -> float:
        return (self.orig / self.mem_used) if self.mem_used else 0.0


@dataclass(slots=True)
class SystemSnapshot:
    ts: float = 0.0
    interval: float = 1.0
    cpu_total: float = 0.0
    cpu_cores: list = field(default_factory=list)
    cpu_kernel: float = 0.0
    cpu_kernel_cores: list = field(default_factory=list)
    cpu_freq: float = 0.0
    cpu_model: str = ""
    load_avg: tuple = (0.0, 0.0, 0.0)
    mem: object = None
    swap: object = None
    kernel_slab_recl: int = 0
    kernel_slab_unrecl: int = 0
    page_tables: int = 0
    committed: int = 0
    commit_limit: int = 0
    handles: int = 0
    nthreads: int = 0
    nprocs: int = 0
    uptime: float = 0.0
    temps: dict = field(default_factory=dict)
    nics: list = field(default_factory=list)
    disk_read_bps: float = 0.0
    disk_write_bps: float = 0.0
    swaps: list = field(default_factory=list)
    zrams: list = field(default_factory=list)
    zswap_pool: int = 0          # RAM the zswap pool occupies, compressed
    zswapped: int = 0            # original size of what it holds
    zswap_on: bool = False
    zswap_compressor: str = ""
    drives: list = field(default_factory=list)
    gpus: list = field(default_factory=list)


@dataclass(slots=True)
class Snapshot:
    system: SystemSnapshot
    procs: list


def _read_meminfo() -> dict:
    out = {}
    try:
        with open("/proc/meminfo", "rb") as fh:
            for line in fh:
                key, _, rest = line.partition(b":")
                try:
                    out[key.decode()] = int(rest.split()[0]) * 1024
                except (IndexError, ValueError):
                    pass
    except OSError:
        pass
    return out


def _cgroup_unit(pid, proc_root="/proc") -> str:
    """The systemd unit a process belongs to, from /proc/PID/cgroup.

    On a systemd machine this answers "what is this thing part of" far better
    than the parent PID does - every user application ends up in its own
    .scope and every daemon in its .service.  Reads the v2 line first and
    falls back to the v1 name=systemd controller.
    """
    try:
        with open(f"{proc_root}/{pid}/cgroup", "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return ""
    path = ""
    for line in lines:
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        if parts[0] == "0" and parts[1] == "":          # cgroup v2
            path = parts[2]
            break
        if parts[1] == "name=systemd":                  # cgroup v1
            path = parts[2]
    if not path or path == "/":
        return ""
    leaf = path.rstrip("/").rsplit("/", 1)[-1]
    if leaf.endswith((".service", ".scope", ".slice", ".mount", ".socket")):
        return leaf
    return ""


def _read_handles() -> int:
    try:
        with open("/proc/sys/fs/file-nr", "rb") as fh:
            allocated, free, _ = fh.read().split()
        return int(allocated) - int(free)
    except (OSError, ValueError):
        return 0


def _read_swaps(path="/proc/swaps") -> list:
    """/proc/swaps. Sizes there are in 1024-byte units, not bytes."""
    out = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            next(fh, None)                      # header
            for line in fh:
                parts = line.split()
                if len(parts) < 5:
                    continue
                path = parts[0]
                try:
                    size, used, prio = int(parts[2]), int(parts[3]), int(parts[4])
                except ValueError:
                    continue
                out.append(SwapDev(
                    path=path, kind=parts[1], size=size * 1024, used=used * 1024,
                    priority=prio, is_zram=path.startswith("/dev/zram"),
                ))
    except (OSError, StopIteration):
        pass
    return out


def _sysfs_int(path) -> int:
    try:
        with open(path, "rb") as fh:
            return int(fh.read().split()[0])
    except (OSError, ValueError, IndexError):
        return 0


def _read_zram(swaps, sys_block="/sys/block", mounts_path="/proc/mounts") -> list:
    """zram devices from sysfs.

    mm_stat is the modern one-line form; the individual attribute files are
    the fallback for kernels that predate it.

    The paths are arguments rather than constants so the tests can point the
    whole thing at a fake sysfs tree; nothing in the app passes them.
    """
    import glob

    swap_paths = {s.path for s in swaps}
    mounts = {}
    try:
        with open(mounts_path, "r", encoding="utf-8") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 2 and parts[0].startswith("/dev/zram"):
                    mounts[parts[0]] = parts[1]
    except OSError:
        pass

    out = []
    for base in sorted(glob.glob(f"{sys_block}/zram*")):
        name = os.path.basename(base)
        disksize = _sysfs_int(f"{base}/disksize")
        if not disksize:
            continue                            # device exists but is not set up
        orig = compressed = mem_used = 0
        try:
            with open(f"{base}/mm_stat", "rb") as fh:
                fields = fh.read().split()
            orig, compressed, mem_used = (int(fields[0]), int(fields[1]),
                                          int(fields[2]))
        except (OSError, ValueError, IndexError):
            orig = _sysfs_int(f"{base}/orig_data_size")
            compressed = _sysfs_int(f"{base}/compr_data_size")
            mem_used = _sysfs_int(f"{base}/mem_used_total")

        algorithm = ""
        try:
            with open(f"{base}/comp_algorithm", "r", encoding="utf-8") as fh:
                for token in fh.read().split():
                    if token.startswith("["):   # the active one is bracketed
                        algorithm = token.strip("[]")
                        break
        except OSError:
            pass

        device = f"/dev/{name}"
        if device in swap_paths:
            used_as = "swap"
        else:
            used_as = mounts.get(device, "unused")
        out.append(ZramDev(name=name, disksize=disksize, orig=orig,
                           compressed=compressed, mem_used=mem_used,
                           algorithm=algorithm, used_as=used_as))
    return out


def _zswap_params(base="/sys/module/zswap/parameters") -> tuple:
    """zswap sits in front of the swap devices: pages land in its RAM pool and
    only reach the device below on overflow. That is why /proc/swaps can show
    slots in use while a zram device reports almost nothing stored."""
    try:
        with open(f"{base}/enabled", "r", encoding="utf-8") as fh:
            enabled = fh.read().strip().upper() in ("Y", "1")
    except OSError:
        return False, ""
    compressor = ""
    try:
        with open(f"{base}/compressor", "r", encoding="utf-8") as fh:
            compressor = fh.read().strip()
    except OSError:
        pass
    return enabled, compressor


def _cpu_model() -> str:
    try:
        with open("/proc/cpuinfo", "rb") as fh:
            for line in fh:
                if line.startswith(b"model name"):
                    return line.partition(b":")[2].strip().decode(errors="replace")
    except OSError:
        pass
    return "CPU"


class ProcessSampler:
    """Keeps psutil.Process objects alive between samples.

    psutil's cpu_percent() is a delta against the previous call on the *same*
    object, so the cache is what makes the CPU column meaningful.  Immutable
    fields (name, exe, cmdline, ...) are cached too - re-reading them every
    second for 500 processes is the single most expensive thing this app
    could do.
    """

    def __init__(self):
        self._proc = {}     # pid -> psutil.Process
        self._static = {}   # pid -> (name, exe, cmdline, username, ctime, terminal, unit)
        self._io = {}       # pid -> (read_bytes, write_bytes)
        self._uid = os.getuid()

    def _static_for(self, p: psutil.Process):
        key = p.pid
        info = self._static.get(key)
        if info is not None and info[4] == p.create_time():
            return info
        try:
            cmdline = " ".join(p.cmdline())
        except (psutil.AccessDenied, *_DEAD):
            cmdline = ""
        try:
            exe = p.exe()
        except (psutil.AccessDenied, *_DEAD):
            exe = ""
        try:
            username = p.username()
        except (psutil.AccessDenied, KeyError, *_DEAD):
            username = str(p.uids().real) if hasattr(p, "uids") else "?"
        try:
            terminal = p.terminal() or ""
        except (psutil.AccessDenied, *_DEAD):
            terminal = ""
        info = (p.name(), exe, cmdline, username, p.create_time(), terminal,
                _cgroup_unit(key))
        self._static[key] = info
        return info

    def sample(self, interval: float) -> list:
        out = []
        alive = set()
        inv = 1.0 / NPROC
        for pid in psutil.pids():
            alive.add(pid)
            p = self._proc.get(pid)
            fresh = False
            if p is None:
                try:
                    p = psutil.Process(pid)
                except _DEAD:
                    continue
                self._proc[pid] = p
                fresh = True
            try:
                with p.oneshot():
                    (name, exe, cmdline, username, ctime, terminal,
                     unit) = self._static_for(p)
                    cpu_raw = p.cpu_percent(None)
                    if fresh:
                        cpu_raw = 0.0
                    mem = p.memory_info()
                    cput = p.cpu_times()
                    info = ProcInfo(
                        pid=pid,
                        ppid=p.ppid(),
                        name=name,
                        username=username,
                        status=p.status(),
                        cpu=cpu_raw * inv,
                        cpu_raw=cpu_raw,
                        rss=mem.rss,
                        vms=mem.vms,
                        shared=getattr(mem, "shared", 0),
                        threads=p.num_threads(),
                        nice=p.nice(),
                        create_time=ctime,
                        cpu_time=cput.user + cput.system,
                        read_bps=0.0,
                        write_bps=0.0,
                        cmdline=cmdline,
                        exe=exe,
                        terminal=terminal,
                        unit=unit,
                        gpu=0.0,
                        is_own=p.uids().real == self._uid,
                    )
                    try:
                        io = p.io_counters()
                    except (psutil.AccessDenied, NotImplementedError, AttributeError):
                        io = None
                if io is not None:
                    prev = self._io.get(pid)
                    self._io[pid] = (io.read_bytes, io.write_bytes)
                    if prev is not None and interval > 0:
                        info.read_bps = max(0.0, (io.read_bytes - prev[0]) / interval)
                        info.write_bps = max(0.0, (io.write_bytes - prev[1]) / interval)
            except (psutil.AccessDenied, *_DEAD):
                # Process vanished mid-read, or we simply may not look at it.
                self._proc.pop(pid, None)
                continue
            out.append(info)

        for pid in list(self._proc):
            if pid not in alive:
                self._proc.pop(pid, None)
                self._static.pop(pid, None)
                self._io.pop(pid, None)
        return out


class SystemSampler:
    def __init__(self):
        self._net = psutil.net_io_counters(pernic=True)
        self._disk = psutil.disk_io_counters()
        self._cpu_model = _cpu_model()
        self._zswap = _zswap_params()
        self._drives = DriveSampler()
        self._gpus = GpuSampler()
        # Two of the readings here cost far more than the rest put together
        # and move far more slowly: reading every hwmon sensor on the box is
        # about 39 ms, and the walk of /proc/*/fdinfo the GPU needs where
        # there is no driver counter is about 22 ms. Refreshing those on
        # every tick made the whole pass too slow to run at Ultra speed, so
        # they keep their own pace and the last answer stands in between.
        self._temps = {}
        self._temps_at = 0.0
        self._gpu_cache = []
        self._gpu_at = 0.0
        psutil.cpu_percent(None, percpu=True)
        psutil.cpu_percent(None)
        psutil.cpu_times_percent(None, percpu=True)

    def sample(self, interval: float, nprocs: int, nthreads: int,
               gpu_per_process: bool = False) -> SystemSnapshot:
        s = SystemSnapshot()
        s.ts = time.monotonic()
        s.interval = interval

        # At most one of the two expensive refreshes per tick. Both falling
        # on the same tick was the only thing that pushed a cycle near the
        # 100 ms an Ultra tick has to fit in; the temperatures are the less
        # urgent of the two, so they give way and go on the next one.
        gpu_due = (s.ts - self._gpu_at >= GPU_INTERVAL) or gpu_per_process
        temps_due = (s.ts - self._temps_at >= TEMP_INTERVAL) and not gpu_due
        s.cpu_cores = psutil.cpu_percent(None, percpu=True)
        s.cpu_total = sum(s.cpu_cores) / len(s.cpu_cores) if s.cpu_cores else 0.0

        # Split each core's busy time into user and kernel, the way Task
        # Manager's "Show kernel times" did. The split is applied as a ratio
        # of the already-known busy figure so the red band can never exceed
        # the height of the bar it sits inside.
        try:
            times = psutil.cpu_times_percent(None, percpu=True)
        except (OSError, RuntimeError):
            times = []
        kernel_cores = []
        for i, t in enumerate(times):
            user = t.user + t.nice + getattr(t, "guest", 0.0) + getattr(t, "guest_nice", 0.0)
            kern = (t.system + getattr(t, "irq", 0.0) + getattr(t, "softirq", 0.0)
                    + getattr(t, "steal", 0.0))
            busy = user + kern
            total = s.cpu_cores[i] if i < len(s.cpu_cores) else busy
            kernel_cores.append(total * (kern / busy) if busy > 0.0 else 0.0)
        s.cpu_kernel_cores = kernel_cores
        s.cpu_kernel = (sum(kernel_cores) / len(kernel_cores)) if kernel_cores else 0.0
        s.cpu_model = self._cpu_model
        try:
            freq = psutil.cpu_freq()
            s.cpu_freq = freq.current if freq else 0.0
        except (OSError, AttributeError):
            s.cpu_freq = 0.0
        try:
            s.load_avg = os.getloadavg()
        except OSError:
            s.load_avg = (0.0, 0.0, 0.0)

        s.mem = psutil.virtual_memory()
        s.swap = psutil.swap_memory()
        s.swaps = _read_swaps()
        s.zrams = _read_zram(s.swaps)
        mi = _read_meminfo()
        s.kernel_slab_recl = mi.get("SReclaimable", 0)
        s.kernel_slab_unrecl = mi.get("SUnreclaim", 0)
        s.page_tables = mi.get("PageTables", 0)
        s.committed = mi.get("Committed_AS", 0)
        s.commit_limit = mi.get("CommitLimit", 0)
        s.zswap_pool = mi.get("Zswap", 0)
        s.zswapped = mi.get("Zswapped", 0)
        s.zswap_on, s.zswap_compressor = self._zswap
        s.handles = _read_handles()
        s.nprocs = nprocs
        s.nthreads = nthreads
        s.uptime = time.time() - psutil.boot_time()

        if temps_due:
            self._temps_at = s.ts
            try:
                self._temps = {k: [(x.label or k, x.current) for x in v]
                               for k, v in psutil.sensors_temperatures().items()}
            except (AttributeError, OSError):
                self._temps = {}
        s.temps = self._temps

        # --- network -------------------------------------------------------
        counters = psutil.net_io_counters(pernic=True)
        try:
            stats = psutil.net_if_stats()
        except OSError:
            stats = {}
        try:
            addrs = psutil.net_if_addrs()
        except OSError:
            addrs = {}
        nics = []
        for name, c in counters.items():
            st = stats.get(name)
            prev = self._net.get(name)
            sent_bps = recv_bps = 0.0
            if prev is not None and interval > 0:
                sent_bps = max(0.0, (c.bytes_sent - prev.bytes_sent) / interval)
                recv_bps = max(0.0, (c.bytes_recv - prev.bytes_recv) / interval)
            speed = st.speed if st else 0
            cap = speed * 125000.0 if speed else 0.0
            util = min(100.0, (sent_bps + recv_bps) / cap * 100.0) if cap else 0.0
            ip = ""
            for a in addrs.get(name, ()):
                if a.family.name == "AF_INET":
                    ip = a.address
                    break
            nics.append(NicInfo(
                name=name, speed=speed,
                is_up=bool(st.isup) if st else False,
                duplex=(st.duplex.name.replace("NIC_DUPLEX_", "").title() if st else ""),
                addr=ip, sent=c.bytes_sent, recv=c.bytes_recv,
                sent_bps=sent_bps, recv_bps=recv_bps, utilisation=util,
            ))
        nics.sort(key=lambda n: (n.name == "lo", n.name))
        s.nics = nics
        self._net = counters

        # --- disk ----------------------------------------------------------
        d = psutil.disk_io_counters()
        if d and self._disk and interval > 0:
            s.disk_read_bps = max(0.0, (d.read_bytes - self._disk.read_bytes) / interval)
            s.disk_write_bps = max(0.0, (d.write_bytes - self._disk.write_bytes) / interval)
        self._disk = d or self._disk

        s.drives = self._drives.sample(interval)
        # The GPU sampler works out its own elapsed time, so leaving it
        # alone for a tick costs nothing but freshness.
        if gpu_due:
            self._gpu_at = s.ts
            self._gpu_cache = self._gpus.sample(per_process=gpu_per_process)
        s.gpus = self._gpu_cache

        return s

    def gpu_proc_busy(self) -> dict:
        """pid -> GPU percent from the last sample, empty unless asked for."""
        return self._gpus.proc_busy


class Sampler(threading.Thread):
    """Worker thread: samples on an interval and hands snapshots to `deliver`.

    `deliver` is called from this thread; the UI wraps it in GLib.idle_add.
    """

    def __init__(self, deliver, interval: float = 1.0):
        super().__init__(daemon=True, name="sampler")
        self._deliver = deliver
        self._interval = interval
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._procs = ProcessSampler()
        self._system = SystemSampler()
        self._paused = False
        self._gpu_per_process = False

    # -- control ------------------------------------------------------------
    def set_interval(self, seconds: float):
        self._interval = seconds
        self._wake.set()

    def set_gpu_per_process(self, enabled: bool):
        """Per-process GPU use costs a full /proc/*/fdinfo walk, so it is only
        measured while the GPU column is actually on screen."""
        self._gpu_per_process = bool(enabled)

    def set_paused(self, paused: bool):
        self._paused = paused
        if not paused:
            self._wake.set()

    def refresh_now(self):
        self._wake.set()

    def stop(self):
        self._stop.set()
        self._wake.set()

    # -- worker -------------------------------------------------------------
    def run(self):
        last = time.monotonic()
        # Prime psutil's per-process deltas so the first visible sample is real.
        self._procs.sample(0.0)
        time.sleep(0.25)
        while not self._stop.is_set():
            now = time.monotonic()
            interval = max(0.05, now - last)
            last = now
            try:
                procs = self._procs.sample(interval)
                nthreads = sum(p.threads for p in procs)
                system = self._system.sample(interval, len(procs), nthreads,
                                             self._gpu_per_process)
                # The GPU walk happens inside the system pass, so the figures
                # are stitched onto the processes afterwards rather than
                # scanning /proc/*/fdinfo a second time.
                if self._gpu_per_process:
                    busy = self._system.gpu_proc_busy()
                    for p in procs:
                        p.gpu = busy.get(p.pid, 0.0)
                self._deliver(Snapshot(system=system, procs=procs))
            except Exception as exc:  # a bad /proc read must not kill the thread
                import traceback
                traceback.print_exception(exc)
            # Wait out what is left of the interval rather than the whole of
            # it: sampling itself takes tens of milliseconds, and adding that
            # on top of every wait made each speed run slower than it said.
            # Ultra asks for ten samples a second and only managed seven.
            if self._paused:
                wait = 3600.0
            else:
                wait = max(0.0, self._interval - (time.monotonic() - now))
            self._wake.wait(wait)
            self._wake.clear()
