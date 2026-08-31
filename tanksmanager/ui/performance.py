"""The Performance tab.

Navigation follows Windows 10: a strip of live cards down the left - CPU,
Memory, one per drive, one per network adapter, one per GPU - and a detail
pane on the right that changes with the selection. The graphs inside keep the
Windows XP palette.
"""

from __future__ import annotations

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from .graph import HistoryGraph, XP_GREEN, XP_YELLOW
from .perfcards import ResourceCard
from .perfpanes import (AutoScale, CpuPane, DiskPane, GpuPane, MemoryPane,
                        NetPane, NET_COLOURS, SWAP_COLOURS, XFER_COLOURS)
from ..backend.units import bytes_h, bytes_pair, bits_rate, rate


def _history_graphs(widget):
    """Every HistoryGraph inside a pane, however deeply it is nested."""
    if isinstance(widget, HistoryGraph):
        yield widget
        return
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            yield from _history_graphs(child)

SKIP_INTERFACES = ("lo",)


class PerformanceTab(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.window = window
        self.cfg = window.cfg
        self.cards = {}
        self.panes = {}
        self._order = []
        self._scales = {}
        self._pending_select = str(self.cfg["perf_card"] or "cpu")
        # True while the code is driving the selection, so a genuine click can
        # be told apart from a programmatic one.
        self._selecting = False

        self.list = Gtk.ListBox()
        self.list.set_selection_mode(Gtk.SelectionMode.BROWSE)
        self.list.connect("row-selected", self._on_row_selected)
        sidebar = Gtk.ScrolledWindow()
        sidebar.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sidebar.set_shadow_type(Gtk.ShadowType.IN)
        sidebar.add(self.list)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(120)

        self.paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.paned.set_border_width(6)
        self.paned.pack1(sidebar, False, False)
        self.paned.pack2(self.stack, True, False)
        self.paned.set_position(int(self.cfg["perf_sidebar"] or 250))
        sidebar.set_size_request(190, -1)
        self.pack_start(self.paned, True, True, 0)

        # CPU and Memory always exist; everything else follows the hardware.
        self._ensure("cpu", "CPU")
        self._ensure("memory", "Memory")

    # -- options ------------------------------------------------------------
    def apply_options(self):
        """GtkStack refuses to make a child that has not been shown yet the
        visible one, so this runs after the window is on screen."""
        cpu = self.panes.get("cpu")
        if cpu is not None:
            cpu.set_one_graph_per_cpu(bool(self.cfg["one_graph_per_cpu"]))
            cpu.set_kernel_times(bool(self.cfg["show_kernel_times"]))
        self.set_tank_mode(bool(self.cfg["tank_mode"]))
        self._select(self._pending_select)

    def set_tank_mode(self, enabled):
        """Arm, or stand down, every graph in the tab.

        The panes are walked rather than asked, because a pane may hold one
        graph or one per core, and a drive or GPU appearing brings a whole
        new pane with it.
        """
        for pane in self.panes.values():
            for graph in _history_graphs(pane):
                graph.set_tank_mode(enabled)

    def set_classic(self, classic):
        for pane in self.panes.values():
            pane.set_classic(classic)
        for card in self.cards.values():
            card.set_classic(classic)

    def set_one_graph_per_cpu(self, per_cpu):
        cpu = self.panes.get("cpu")
        if cpu is not None:
            cpu.set_one_graph_per_cpu(per_cpu)

    def set_kernel_times(self, enabled):
        cpu = self.panes.get("cpu")
        if cpu is not None:
            cpu.set_kernel_times(enabled)

    def save_state(self):
        self.cfg["perf_sidebar"] = self.paned.get_position()
        row = self.list.get_selected_row()
        if row is not None:
            self.cfg["perf_card"] = row.key

    # -- card plumbing ------------------------------------------------------
    def _make_pane(self, key):
        kind, _, name = key.partition(":")
        if kind == "cpu":
            return CpuPane(self.cfg)
        if kind == "memory":
            return MemoryPane(self.cfg)
        if kind == "net":
            return NetPane(self.cfg, name)
        if kind == "disk":
            return DiskPane(self.cfg, name)
        return GpuPane(self.cfg, name)

    def _ensure(self, key, title):
        if key in self.cards:
            self.cards[key].set_title(title)
            return self.cards[key]
        kind = key.partition(":")[0]
        series, colours, stacked = 1, [XP_GREEN], False
        if kind == "net":
            series, colours = 2, NET_COLOURS
        elif kind == "disk":
            series, colours = 2, XFER_COLOURS
        elif kind == "memory":
            series, colours = 2, SWAP_COLOURS
            stacked = True
        card = ResourceCard(key, title, classic=bool(self.cfg["classic_graphs"]),
                            series=series, classic_series=colours, stacked=stacked)
        pane = self._make_pane(key)
        self.cards[key] = card
        self.panes[key] = pane
        self.stack.add_named(pane, key)
        pane.show_all()
        # A drive or GPU plugged in while Tank Mode is on arrives armed.
        if self.cfg["tank_mode"]:
            for graph in _history_graphs(pane):
                graph.set_tank_mode(True)
        return card

    def _drop(self, key):
        card = self.cards.pop(key, None)
        pane = self.panes.pop(key, None)
        self._scales.pop(key, None)
        if card is not None and card.get_parent() is not None:
            self.list.remove(card)
        if card is not None:
            card.destroy()
        if pane is not None:
            self.stack.remove(pane)
            pane.destroy()

    def _sync(self, s):
        """Keep the card strip matching the hardware that is actually there."""
        specs = [("cpu", "CPU"), ("memory", "Memory")]
        for drive in s.drives:
            specs.append((f"disk:{drive.name}", f"{drive.kind} ({drive.name})"))
        for nic in s.nics:
            if nic.name in SKIP_INTERFACES:
                continue
            specs.append((f"net:{nic.name}", nic.name))
        for index, gpu in enumerate(s.gpus):
            label = "GPU" if len(s.gpus) == 1 else f"GPU {index}"
            specs.append((f"gpu:{gpu.key}", label))

        keys = [key for key, _title in specs]
        if keys == self._order:
            for key, title in specs:
                self.cards[key].set_title(title)
            return

        selected = self.list.get_selected_row()
        # A card restored from the config may name hardware that has not been
        # discovered yet, so the request outlives the first few syncs.
        selected_key = (self._pending_select
                        or (selected.key if selected is not None else "cpu"))
        for key in list(self.cards):
            if key not in keys:
                self._drop(key)
        for key, title in specs:
            self._ensure(key, title)

        self._selecting = True
        try:
            for row in list(self.list.get_children()):
                self.list.remove(row)
            for key, _title in specs:
                self.list.insert(self.cards[key], -1)
        finally:
            self._selecting = False
        self.list.show_all()
        self._order = keys
        self._select(selected_key)

    def _select(self, key):
        card = self.cards.get(key)
        self._selecting = True
        try:
            if card is None:
                # The hardware it names may not have been discovered yet, so
                # keep asking until it turns up - or until the user picks
                # something else themselves.
                self._pending_select = key
                if self.list.get_selected_row() is None:
                    fallback = self.cards.get("cpu")
                    if fallback is not None:
                        self.list.select_row(fallback)
                return
            self._pending_select = None
            self.list.select_row(card)
        finally:
            self._selecting = False

    def _on_row_selected(self, _list, row):
        if row is None:
            return
        if not self._selecting:
            # An explicit choice retires any outstanding restore request,
            # otherwise unplugging and replugging a drive would drag the user
            # back to it from wherever they had moved on to.
            self._pending_select = None
        self.cfg["perf_card"] = row.key
        self.stack.set_visible_child_name(row.key)

    def _scale(self, key, floor):
        scale = self._scales.get(key)
        if scale is None:
            scale = self._scales[key] = AutoScale(floor)
        return scale

    # -- data ---------------------------------------------------------------
    def update(self, s):
        self._sync(s)
        self._update_cards(s)
        # Every pane is fed, not just the visible one, so switching cards does
        # not reveal a graph that has been sitting empty.
        for pane in self.panes.values():
            pane.update(s)

    def _update_cards(self, s):
        cpu = self.cards.get("cpu")
        if cpu is not None:
            cpu.push(s.cpu_total / 100.0)
            speed = f"  ·  {s.cpu_freq / 1000:.2f} GHz" if s.cpu_freq else ""
            cpu.set_subtitle(f"{s.cpu_total:.0f} %{speed}")

        memory = self.cards.get("memory")
        if memory is not None:
            used = s.mem.total - s.mem.available
            frac = used / s.mem.total if s.mem.total else 0.0
            swap_total = sum(d.size for d in s.swaps)
            swap_used = sum(d.used for d in s.swaps)
            memory.push(frac, (swap_used / swap_total) if swap_total else 0.0)
            detail = f"{bytes_pair(used, s.mem.total)} ({frac * 100:.0f} %)"
            if swap_total:
                detail += f"  ·  swap {bytes_h(swap_used, 0)}"
            memory.set_subtitle(detail)
            memory.set_tooltip_text(
                f"{bytes_h(used)} of {bytes_h(s.mem.total)} in use"
                + (f", swap {bytes_h(swap_used)} of {bytes_h(swap_total)}"
                   if swap_total else ""))

        for drive in s.drives:
            card = self.cards.get(f"disk:{drive.name}")
            if card is None:
                continue
            scale = self._scale(f"disk:{drive.name}", 1 << 20)(drive.total_bps)
            card.push(drive.read_bps / scale, drive.write_bps / scale)
            card.set_subtitle(f"{drive.active:.0f} %  ·  {rate(drive.total_bps) or '0 B/s'}")
            card.set_tooltip_text(f"{drive.model} — {bytes_h(drive.size)}")

        for nic in s.nics:
            card = self.cards.get(f"net:{nic.name}")
            if card is None:
                continue
            total = nic.recv_bps + nic.sent_bps
            cap = nic.speed * 125000.0 if nic.speed else 0
            scale = self._scale(f"net:{nic.name}", 64 * 1024)(total, cap)
            card.push(nic.recv_bps / scale, nic.sent_bps / scale)
            card.set_subtitle(f"R {bits_rate(nic.recv_bps)}  ·  S {bits_rate(nic.sent_bps)}")
            card.set_tooltip_text(
                f"{nic.name} — {'connected' if nic.is_up else 'down'}"
                + (f", {nic.addr}" if nic.addr else ""))

        for gpu in s.gpus:
            card = self.cards.get(f"gpu:{gpu.key}")
            if card is None:
                continue
            card.push(gpu.busy / 100.0)
            card.set_subtitle(f"{gpu.busy:.0f} %"
                              + (f"  ·  {gpu.freq:.0f} MHz" if gpu.freq else ""))
            card.set_tooltip_text(f"{gpu.name} ({gpu.driver})")
