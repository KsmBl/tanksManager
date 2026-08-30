"""Detail panes for the Performance tab - one per card.

The layout follows Windows 10 (a big graph up top, read-outs underneath) while
the graphs themselves keep the XP palette.
"""

from __future__ import annotations

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Pango, GLib  # noqa: E402

from .graph import (HistoryGraph, Meter, MeterGrid, CoreGrid,
                    XP_GREEN, XP_RED, XP_YELLOW)
from ..backend.sampler import NPROC, NPROC_PHYS
from ..backend.units import bytes_h, bits_rate, duration, rate

CPU_SPLIT_COLOURS = [XP_RED, XP_GREEN]
CPU_PLAIN_COLOURS = [XP_GREEN]
MEM_COLOURS = [XP_GREEN]
SWAP_COLOURS = [XP_YELLOW, XP_GREEN]     # drive swap, zram swap
ZRAM_COLOURS = [XP_YELLOW, XP_GREEN]     # stored data, and its cost in RAM
NET_COLOURS = [XP_GREEN, XP_YELLOW]      # receive, send
DISK_COLOURS = [XP_GREEN]
XFER_COLOURS = [XP_GREEN, XP_YELLOW]     # read, write
GPU_COLOURS = [XP_GREEN]


def frame(title, child, padding=8):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    box.set_border_width(padding)
    box.pack_start(child, True, True, 0)
    widget = Gtk.Frame(label=f" {title} ")
    widget.add(box)
    return widget


def key_swatch(colour, text):
    """A colour swatch followed by a label, for the graph legends."""
    return (f'<span background="{colour}" foreground="{colour}">  </span> '
            f'{GLib.markup_escape_text(text)}')


def set_row_visible(row, visible):
    """Rows that depend on hardware are built up front and revealed on demand.
    show_all() skips a widget flagged no-show-all, so the flag has to come off
    before the row can appear."""
    if visible:
        if not row.get_visible():
            row.set_no_show_all(False)
            row.show_all()
    elif row.get_visible():
        row.hide()


class AutoScale:
    """A slowly decaying peak with a floor, so a nearly idle device still gets
    a sensible axis instead of sitting flat on zero."""

    def __init__(self, floor):
        self.floor = float(floor)
        self.peak = 0.0

    def __call__(self, value, cap=0):
        peak = max(self.peak * 0.995, float(value), self.floor)
        if cap:
            peak = min(peak, float(cap))
        self.peak = peak
        scale = peak * 1.25
        if cap:
            scale = min(scale, float(cap))
        return scale or 1.0


class InfoFrame(Gtk.Frame):
    """One of the little bordered read-outs."""

    def __init__(self, title, keys):
        super().__init__(label=f" {title} ")
        grid = Gtk.Grid(row_spacing=2, column_spacing=12)
        grid.set_border_width(8)
        self.labels = {}
        for row, name in enumerate(keys):
            caption = Gtk.Label(label=name, xalign=0.0)
            caption.get_style_context().add_class("dim-label")
            value = Gtk.Label(label="-", xalign=1.0)
            value.set_hexpand(True)
            value.set_selectable(True)
            value.set_ellipsize(Pango.EllipsizeMode.END)
            grid.attach(caption, 0, row, 1, 1)
            grid.attach(value, 1, row, 1, 1)
            self.labels[name] = value
        self.add(grid)

    def set(self, name, text):
        label = self.labels.get(name)
        if label is not None and label.get_text() != text:
            label.set_text(text)


class Pane(Gtk.ScrolledWindow):
    """Common scaffolding: a scrolling column of frames."""

    def __init__(self):
        super().__init__()
        self.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.body.set_border_width(10)
        self.add(self.body)
        self._graphs = []
        self._meters = []

    def register(self, *widgets):
        for widget in widgets:
            if isinstance(widget, (HistoryGraph, Meter)):
                self._graphs.append(widget)
            else:
                self._meters.append(widget)
        return widgets[0] if len(widgets) == 1 else widgets

    def set_classic(self, classic):
        for widget in self._graphs:
            widget.classic = classic
            widget.queue_draw()
        for widget in self._meters:
            widget.set_classic(classic)

    def legend(self):
        label = Gtk.Label(label="", xalign=0.0)
        label.get_style_context().add_class("dim-label")
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_use_markup(True)
        return label

    @staticmethod
    def stack(graph, legend):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        box.pack_start(graph, True, True, 0)
        box.pack_start(legend, False, False, 0)
        return box

    def readouts(self, *frames):
        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_homogeneous(True)
        flow.set_min_children_per_line(1)
        flow.set_max_children_per_line(2)
        flow.set_column_spacing(10)
        flow.set_row_spacing(10)
        for widget in frames:
            flow.add(widget)
        return flow


# --------------------------------------------------------------------------
class CpuPane(Pane):
    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        classic = bool(cfg["classic_graphs"])

        self.meters = MeterGrid(NPROC, classic=classic)
        self.total_label = Gtk.Label(label="Total 0 %", xalign=0.5)
        self.total_label.get_style_context().add_class("dim-label")
        meter_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        meter_box.pack_start(self.meters, False, False, 0)
        meter_box.pack_start(self.total_label, False, False, 0)

        self.history = HistoryGraph(capacity=150, height=150, classic=classic)
        self.cores = CoreGrid(NPROC, classic=classic)
        self.view = Gtk.Stack()
        self.view.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.view.add_named(self.history, "single")
        self.view.add_named(self.cores, "cores")
        self.register(self.history)
        self._meters.extend([self.meters, self.cores])

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.pack_start(frame("CPU Usage", meter_box), False, False, 0)
        row.pack_start(frame("CPU Usage History", self.view), True, True, 0)
        self.body.pack_start(row, False, False, 0)

        self.system = InfoFrame("System", [
            "Handles", "Threads", "Processes", "Up time", "Commit charge"])
        self.processor = InfoFrame("Processor", [
            "Model", "Cores", "Frequency", "Load average", "Temperature"])
        self.body.pack_start(self.readouts(self.processor, self.system),
                             False, False, 0)
        self._kernel_times = None

    def set_one_graph_per_cpu(self, per_cpu):
        self.view.set_visible_child_name("cores" if per_cpu else "single")

    def set_kernel_times(self, enabled):
        if enabled == self._kernel_times:
            return
        self._kernel_times = enabled
        colours = CPU_SPLIT_COLOURS if enabled else CPU_PLAIN_COLOURS
        count = 2 if enabled else 1
        self.history.set_series(count, colours, stacked=enabled)
        self.cores.set_series(count, colours, stacked=enabled)

    def update(self, s):
        kernels = s.cpu_kernel_cores if self._kernel_times else None
        self.meters.update(s.cpu_cores, kernels)
        self.total_label.set_text(f"Total {s.cpu_total:.0f} %")
        if self._kernel_times:
            kernel = s.cpu_kernel / 100.0
            self.history.push(kernel, max(0.0, s.cpu_total / 100.0 - kernel))
        else:
            self.history.push(s.cpu_total / 100.0)
        self.cores.push(s.cpu_cores, kernels)

        self.system.set("Handles", f"{s.handles:,}")
        self.system.set("Threads", f"{s.nthreads:,}")
        self.system.set("Processes", f"{s.nprocs:,}")
        self.system.set("Up time", duration(s.uptime))
        self.system.set("Commit charge",
                        f"{bytes_h(s.committed)} / {bytes_h(s.commit_limit)}"
                        if s.commit_limit else bytes_h(s.committed))

        self.processor.set("Model", s.cpu_model)
        self.processor.labels["Model"].set_tooltip_text(s.cpu_model)
        self.processor.set("Cores", f"{NPROC_PHYS} physical / {NPROC} logical")
        self.processor.set("Frequency",
                           f"{s.cpu_freq:.0f} MHz" if s.cpu_freq else "-")
        self.processor.set("Load average",
                           "  ".join(f"{v:.2f}" for v in s.load_avg))
        self.processor.set("Temperature", _temperature(s))


def _temperature(s):
    for key in ("coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz"):
        readings = s.temps.get(key)
        if readings:
            return f"{max(v for _l, v in readings):.0f} °C  ({key})"
    for key, readings in s.temps.items():
        if readings:
            return f"{max(v for _l, v in readings):.0f} °C  ({key})"
    return "-"


# --------------------------------------------------------------------------
class MemoryPane(Pane):
    def __init__(self, cfg):
        super().__init__()
        classic = bool(cfg["classic_graphs"])
        self._swap_scale = AutoScale(64 << 20)
        self._zram_scale = AutoScale(64 << 20)

        self.mem_meter = Meter(classic=classic, width=54, height=88)
        self.mem_history = HistoryGraph(capacity=150, height=88, classic=classic,
                                        classic_series=MEM_COLOURS)
        self.register(self.mem_meter, self.mem_history)
        mem_row, self.mem_legend = self._row("Memory",
                                             "Physical Memory Usage History",
                                             self.mem_meter, self.mem_history)
        self.body.pack_start(mem_row, False, False, 0)

        self.swap_meter = Meter(classic=classic, width=54, height=76)
        self.swap_history = HistoryGraph(capacity=150, height=76, series=2,
                                         classic=classic, stacked=True,
                                         classic_series=SWAP_COLOURS)
        self.register(self.swap_meter, self.swap_history)
        self.swap_row, self.swap_legend = self._row(
            "Swap Usage", "Swap Usage History",
            self.swap_meter, self.swap_history)
        self.swap_row.set_no_show_all(True)
        self.body.pack_start(self.swap_row, False, False, 0)

        self.zram_meter = Meter(classic=classic, width=54, height=76)
        self.zram_history = HistoryGraph(capacity=150, height=76, series=2,
                                         classic=classic,
                                         classic_series=ZRAM_COLOURS)
        self.register(self.zram_meter, self.zram_history)
        self.zram_row, self.zram_legend = self._row(
            "zram Usage", "zram Usage History",
            self.zram_meter, self.zram_history)
        self.zram_row.set_no_show_all(True)
        self.body.pack_start(self.zram_row, False, False, 0)

        self.physical = InfoFrame("Physical Memory", [
            "Total", "In use", "Cached", "Available", "Free"])
        self.kernel = InfoFrame("Kernel Memory", [
            "Slab (reclaimable)", "Slab (unreclaimable)", "Page tables",
            "Swap used", "Swap total"])
        self.compressed = InfoFrame("Compressed Memory", [
            "zram stored", "zram in RAM", "zram saving",
            "zswap holds", "zswap pool"])
        flow = self.readouts(self.physical, self.kernel, self.compressed)
        self.body.pack_start(flow, False, False, 0)
        self.compressed_cell = self.compressed.get_parent()
        self.compressed_cell.set_no_show_all(True)

    def _row(self, meter_title, hist_title, meter, graph):
        legend = self.legend()
        meter_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        meter_box.pack_start(meter, True, True, 0)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.pack_start(frame(meter_title, meter_box), False, False, 0)
        row.pack_start(frame(hist_title, self.stack(graph, legend)), True, True, 0)
        return row, legend

    def update(self, s):
        mem = s.mem
        used = mem.total - mem.available
        frac = used / mem.total if mem.total else 0.0
        self.mem_meter.set_value(frac, 0.0, bytes_h(used, 0))
        self.mem_history.push(frac)
        self.mem_legend.set_text(
            f"In use {bytes_h(used)} of {bytes_h(mem.total)}   ·   "
            f"cached {bytes_h(getattr(mem, 'cached', 0))}   ·   "
            f"available {bytes_h(mem.available)}")

        self._update_swap(s)
        self._update_zram(s)

        self.physical.set("Total", bytes_h(mem.total))
        self.physical.set("In use", bytes_h(used))
        self.physical.set("Cached", bytes_h(getattr(mem, "cached", 0)))
        self.physical.set("Available", bytes_h(mem.available))
        self.physical.set("Free", bytes_h(mem.free))

        self.kernel.set("Slab (reclaimable)", bytes_h(s.kernel_slab_recl))
        self.kernel.set("Slab (unreclaimable)", bytes_h(s.kernel_slab_unrecl))
        self.kernel.set("Page tables", bytes_h(s.page_tables))
        self.kernel.set("Swap used", bytes_h(s.swap.used))
        self.kernel.set("Swap total", bytes_h(s.swap.total))

        set_row_visible(self.compressed_cell, bool(s.zrams) or s.zswap_on)
        if s.zrams or s.zswap_on:
            stored = sum(z.orig for z in s.zrams)
            in_ram = sum(z.mem_used for z in s.zrams)
            self.compressed.set("zram stored", bytes_h(stored) if s.zrams else "-")
            self.compressed.set("zram in RAM", bytes_h(in_ram) if s.zrams else "-")
            self.compressed.set(
                "zram saving",
                f"{stored / in_ram:.1f}×" if in_ram and stored > (1 << 20) else "-")
            self.compressed.set("zswap holds",
                                bytes_h(s.zswapped) if s.zswap_on else "off")
            self.compressed.set(
                "zswap pool",
                (bytes_h(s.zswap_pool)
                 + (f" ({s.zswap_compressor})" if s.zswap_compressor else ""))
                if s.zswap_on else "-")

    def _update_swap(self, s):
        swaps = s.swaps
        set_row_visible(self.swap_row, bool(swaps))
        if not swaps:
            return
        drive = [d for d in swaps if not d.is_zram]
        zram = [d for d in swaps if d.is_zram]
        drive_used, drive_size = sum(d.used for d in drive), sum(d.size for d in drive)
        zram_used, zram_size = sum(d.used for d in zram), sum(d.size for d in zram)
        total, used = drive_size + zram_size, drive_used + zram_used

        self.swap_meter.set_value(used / total if total else 0.0, 0.0,
                                  bytes_h(used, 0))
        scale = self._swap_scale(used, total)
        self.swap_history.push(drive_used / scale, zram_used / scale)

        drive_text = (f"On drive {bytes_h(drive_used)} of {bytes_h(drive_size)}"
                      if drive else "On drive: none")
        zram_text = (f"In zram {bytes_h(zram_used)} of {bytes_h(zram_size)}"
                     if zram else "In zram: none")
        devices = ", ".join(
            f"{d.path} ({'zram' if d.is_zram else d.kind}, priority {d.priority})"
            for d in swaps)
        self.swap_legend.set_markup(
            key_swatch(self.swap_history.series_hex(0), drive_text)
            + "   " + key_swatch(self.swap_history.series_hex(1), zram_text)
            + GLib.markup_escape_text(
                f"   ·   graph scale {bytes_h(scale)}   ·   {devices}"))
        self.swap_legend.set_tooltip_text(
            f"{drive_text}. {zram_text}. Devices: {devices}.")

    def _update_zram(self, s):
        zrams = s.zrams
        set_row_visible(self.zram_row, bool(zrams))
        if not zrams:
            return
        capacity = sum(z.disksize for z in zrams)
        stored = sum(z.orig for z in zrams)
        in_ram = sum(z.mem_used for z in zrams)
        self.zram_meter.set_value(stored / capacity if capacity else 0.0, 0.0,
                                  bytes_h(stored, 0))
        scale = self._zram_scale(max(stored, in_ram), capacity)
        self.zram_history.push(stored / scale, in_ram / scale)

        algorithms = ", ".join(sorted({z.algorithm for z in zrams if z.algorithm}))
        names = ", ".join(f"/dev/{z.name} ({z.used_as})" for z in zrams)
        stored_text = f"{bytes_h(stored)} stored" if stored else "empty"
        ram_text = f"{bytes_h(in_ram)} of RAM used"
        tail = f"   ·   of {bytes_h(capacity)}"
        if algorithms:
            tail += f"   ·   {algorithms}"
        tail += f"   ·   {names}"
        if s.zswap_on:
            tail += f"   ·   zswap holds {bytes_h(s.zswapped)} in front"
        self.zram_legend.set_markup(
            key_swatch(self.zram_history.series_hex(0), stored_text)
            + "   " + key_swatch(self.zram_history.series_hex(1), ram_text)
            + GLib.markup_escape_text(tail))
        tip = (f"{stored_text}, {ram_text}, of {bytes_h(capacity)} capacity "
               f"({algorithms or 'unknown algorithm'}) on {names}.")
        if s.zswap_on:
            # Without this the two rows look like they contradict each other.
            tip += (f"\n\nzswap sits in front of this device: it is holding "
                    f"{bytes_h(s.zswapped)} of swapped pages in a "
                    f"{bytes_h(s.zswap_pool)} pool in RAM, so those pages count "
                    f"as used swap without ever being written to zram.")
        self.zram_legend.set_tooltip_text(tip)


# --------------------------------------------------------------------------
class NetPane(Pane):
    def __init__(self, cfg, name):
        super().__init__()
        self.name = name
        classic = bool(cfg["classic_graphs"])
        self._scale = AutoScale(64 * 1024)

        self.history = HistoryGraph(capacity=150, height=170, series=2,
                                    classic=classic, classic_series=NET_COLOURS)
        self.register(self.history)
        self.legend_label = self.legend()
        self.body.pack_start(
            frame(f"{name} Throughput", self.stack(self.history, self.legend_label)),
            False, False, 0)

        self.link = InfoFrame("Adapter", [
            "State", "Link speed", "Duplex", "IPv4 address", "Interface"])
        self.traffic = InfoFrame("Traffic", [
            "Receive", "Send", "Total received", "Total sent", "Graph scale"])
        self.body.pack_start(self.readouts(self.link, self.traffic), False, False, 0)

    def update(self, s):
        nic = next((n for n in s.nics if n.name == self.name), None)
        if nic is None:
            return
        total = nic.recv_bps + nic.sent_bps
        cap = nic.speed * 125000.0 if nic.speed else 0
        scale = self._scale(total, cap)
        self.history.push(nic.recv_bps / scale, nic.sent_bps / scale)
        self.legend_label.set_markup(
            key_swatch(self.history.series_hex(0),
                       f"Receive {bits_rate(nic.recv_bps)}")
            + "   " + key_swatch(self.history.series_hex(1),
                                 f"Send {bits_rate(nic.sent_bps)}")
            + GLib.markup_escape_text(f"   ·   scale {bits_rate(scale)}"))

        self.link.set("State", "Connected" if nic.is_up else "Down")
        self.link.set("Link speed",
                      f"{nic.speed} Mbit/s" if nic.speed else "not reported")
        self.link.set("Duplex", nic.duplex or "-")
        self.link.set("IPv4 address", nic.addr or "-")
        self.link.set("Interface", nic.name)

        self.traffic.set("Receive", bits_rate(nic.recv_bps))
        self.traffic.set("Send", bits_rate(nic.sent_bps))
        self.traffic.set("Total received", bytes_h(nic.recv))
        self.traffic.set("Total sent", bytes_h(nic.sent))
        self.traffic.set("Graph scale", bits_rate(scale))


# --------------------------------------------------------------------------
class DiskPane(Pane):
    def __init__(self, cfg, name):
        super().__init__()
        self.name = name
        classic = bool(cfg["classic_graphs"])
        self._scale = AutoScale(1 << 20)
        self._mounts = None

        self.active = HistoryGraph(capacity=150, height=110, classic=classic,
                                   classic_series=DISK_COLOURS)
        self.transfer = HistoryGraph(capacity=150, height=110, series=2,
                                     classic=classic, classic_series=XFER_COLOURS)
        self.register(self.active, self.transfer)
        self.active_legend = self.legend()
        self.transfer_legend = self.legend()
        self.body.pack_start(
            frame("Active Time (100%)", self.stack(self.active, self.active_legend)),
            False, False, 0)
        self.body.pack_start(
            frame("Disk Transfer Rate",
                  self.stack(self.transfer, self.transfer_legend)),
            False, False, 0)

        self.hardware = InfoFrame("Drive", [
            "Model", "Type", "Capacity", "Device", "Average response time"])
        self.activity = InfoFrame("Activity", [
            "Active time", "Read speed", "Write speed", "Read IOPS", "Write IOPS"])
        self.body.pack_start(self.readouts(self.hardware, self.activity),
                             False, False, 0)

        self.volumes = Gtk.Grid(row_spacing=4, column_spacing=14)
        self.volumes.set_border_width(8)
        self.volumes_frame = Gtk.Frame(label=" Volumes ")
        self.volumes_frame.add(self.volumes)
        self.volumes_frame.set_no_show_all(True)
        self.body.pack_start(self.volumes_frame, False, False, 0)

    def update(self, s):
        drive = next((d for d in s.drives if d.name == self.name), None)
        if drive is None:
            return
        self.active.push(drive.active / 100.0)
        scale = self._scale(drive.total_bps)
        self.transfer.push(drive.read_bps / scale, drive.write_bps / scale)

        self.active_legend.set_text(f"{drive.active:.0f} % of the last second "
                                    f"had at least one request in flight")
        self.transfer_legend.set_markup(
            key_swatch(self.transfer.series_hex(0),
                       f"Read {rate(drive.read_bps) or '0 B/s'}")
            + "   " + key_swatch(self.transfer.series_hex(1),
                                 f"Write {rate(drive.write_bps) or '0 B/s'}")
            + GLib.markup_escape_text(f"   ·   scale {bytes_h(scale)}/s"))

        self.hardware.set("Model", drive.model)
        self.hardware.labels["Model"].set_tooltip_text(drive.model)
        self.hardware.set("Type", drive.kind)
        self.hardware.set("Capacity", bytes_h(drive.size))
        self.hardware.set("Device", f"/dev/{drive.name}")
        self.hardware.set("Average response time",
                          f"{drive.response_ms:.2f} ms" if drive.response_ms else "-")

        self.activity.set("Active time", f"{drive.active:.0f} %")
        self.activity.set("Read speed", rate(drive.read_bps) or "0 B/s")
        self.activity.set("Write speed", rate(drive.write_bps) or "0 B/s")
        self.activity.set("Read IOPS", f"{drive.read_iops:.0f}")
        self.activity.set("Write IOPS", f"{drive.write_iops:.0f}")

        if drive.mounts != self._mounts:
            self._mounts = list(drive.mounts)
            self._rebuild_volumes(drive.mounts)

    def _rebuild_volumes(self, mounts):
        for child in self.volumes.get_children():
            self.volumes.remove(child)
        if not mounts:
            self.volumes_frame.hide()
            return
        headers = ("Mounted on", "Type", "Size", "Used", "Free", "Full")
        for column, text in enumerate(headers):
            label = Gtk.Label(label=text, xalign=0.0 if column < 2 else 1.0)
            label.get_style_context().add_class("dim-label")
            self.volumes.attach(label, column, 0, 1, 1)
        for row, (mount, fstype, total, used, pct) in enumerate(mounts, start=1):
            cells = (mount, fstype, bytes_h(total), bytes_h(used),
                     bytes_h(max(0, total - used)), f"{pct:.1f} %")
            for column, text in enumerate(cells):
                label = Gtk.Label(label=text, xalign=0.0 if column < 2 else 1.0)
                label.set_selectable(True)
                label.set_ellipsize(Pango.EllipsizeMode.END)
                self.volumes.attach(label, column, row, 1, 1)
        self.volumes_frame.set_no_show_all(False)
        self.volumes_frame.show_all()


# --------------------------------------------------------------------------
class GpuPane(Pane):
    def __init__(self, cfg, key):
        super().__init__()
        self.key = key
        self.cfg = cfg
        classic = bool(cfg["classic_graphs"])
        self._engine_names = None
        self._engine_graphs = {}

        self.history = HistoryGraph(capacity=150, height=150, classic=classic,
                                    classic_series=GPU_COLOURS)
        self.register(self.history)
        self.legend_label = self.legend()
        self.body.pack_start(
            frame("GPU Utilisation", self.stack(self.history, self.legend_label)),
            False, False, 0)

        self.engine_grid = Gtk.Grid(column_spacing=6, row_spacing=6,
                                    column_homogeneous=True)
        self.engine_frame = Gtk.Frame(label=" Engines ")
        holder = Gtk.Box()
        holder.set_border_width(8)
        holder.pack_start(self.engine_grid, True, True, 0)
        self.engine_frame.add(holder)
        self.engine_frame.set_no_show_all(True)
        self.body.pack_start(self.engine_frame, False, False, 0)

        self.hardware = InfoFrame("Adapter", [
            "Name", "Driver", "PCI address", "Clock", "Temperature"])
        self.usage = InfoFrame("Utilisation", [
            "Busy", "Memory in use", "Memory total", "Open clients", "Measured by"])
        self.body.pack_start(self.readouts(self.hardware, self.usage),
                             False, False, 0)

        self.note = Gtk.Label(label="", xalign=0.0, wrap=True)
        self.note.get_style_context().add_class("dim-label")
        self.body.pack_start(self.note, False, False, 0)

    def update(self, s):
        gpu = next((g for g in s.gpus if g.key == self.key), None)
        if gpu is None:
            return
        self.history.push(gpu.busy / 100.0)
        self.legend_label.set_markup(
            key_swatch(self.history.series_hex(0), f"Busy {gpu.busy:.0f} %"))

        names = tuple(sorted(gpu.engines))
        if names != self._engine_names:
            self._engine_names = names
            self._rebuild_engines(names)
        for name, graph in self._engine_graphs.items():
            graph.push(gpu.engines.get(name, 0.0) / 100.0)

        self.hardware.set("Name", gpu.name)
        self.hardware.labels["Name"].set_tooltip_text(gpu.name)
        self.hardware.set("Driver", gpu.driver)
        self.hardware.set("PCI address", gpu.pdev or "-")
        clock = f"{gpu.freq:.0f} MHz" if gpu.freq else "-"
        if gpu.freq and gpu.freq_max:
            clock += f"  (max {gpu.freq_max:.0f})"
        self.hardware.set("Clock", clock)
        self.hardware.set("Temperature",
                          f"{gpu.temp:.0f} °C" if gpu.temp else "-")

        self.usage.set("Busy", f"{gpu.busy:.0f} %")
        self.usage.set("Memory in use", bytes_h(gpu.mem_used) if gpu.mem_used else "-")
        self.usage.set("Memory total",
                       bytes_h(gpu.mem_total) if gpu.mem_total else "shared with RAM")
        self.usage.set("Open clients", str(gpu.clients) if gpu.clients else "-")
        self.usage.set("Measured by", gpu.source or "-")
        self.note.set_text(gpu.note)
        self.note.set_visible(bool(gpu.note))

    def _rebuild_engines(self, names):
        for child in self.engine_grid.get_children():
            self.engine_grid.remove(child)
        self._engine_graphs.clear()
        if not names:
            self.engine_frame.hide()
            return
        classic = bool(self.cfg["classic_graphs"])
        columns = min(4, max(1, len(names)))
        for index, name in enumerate(names):
            graph = HistoryGraph(capacity=90, height=56, classic=classic,
                                 grid=(6, 4), classic_series=GPU_COLOURS)
            label = Gtk.Label(label=name, xalign=0.0)
            label.get_style_context().add_class("dim-label")
            label.set_name("core-label")
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            box.pack_start(graph, True, True, 0)
            box.pack_start(label, False, False, 0)
            self.engine_grid.attach(box, index % columns, index // columns, 1, 1)
            self._engine_graphs[name] = graph
            self._graphs.append(graph)
        self.engine_frame.set_no_show_all(False)
        self.engine_frame.show_all()
