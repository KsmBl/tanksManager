"""The Applications tab: open windows rather than processes, with the three
buttons Windows always had along the bottom."""

from __future__ import annotations

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from .table import Column, KeyedTable
from ..backend import icons, windows


class ApplicationsTab(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.window = window
        self.icons = icons.index()
        self.backend = windows.detect()
        self._by_handle = {}

        self.info = Gtk.InfoBar()
        self.info.set_message_type(Gtk.MessageType.INFO)
        self.info.set_show_close_button(True)
        self.info.connect("response", lambda bar, _r: bar.hide())
        label = Gtk.Label(label=self.backend.reason, wrap=True, xalign=0.0)
        self.info.get_content_area().add(label)
        self.info.set_no_show_all(self.backend.available)
        self.pack_start(self.info, False, False, 0)

        self.table = KeyedTable([
            Column("__icon", hidden=True, width=1),
            Column("Task", icon_col=0, width=340, expand=True),
            Column("Status", width=110),
            Column("Workspace", width=110),
            Column("Application", width=150),
            Column("PID", int, str, right=True, width=70),
        ], sort=(1, False), multiple=True)
        self.table.set_border_width(6)
        self.table.connect_activate(self.switch_to)
        self.pack_start(self.table, True, True, 0)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        footer.set_border_width(6)
        footer.set_margin_top(0)
        self.count = Gtk.Label(label="", xalign=0.0)
        self.count.get_style_context().add_class("dim-label")
        footer.pack_start(self.count, True, True, 0)
        for label, callback in (("_End Task", self.end_task),
                                ("_Switch To", self.switch_to),
                                ("_New Task...", self.new_task),
                                ("_Go To Process", self.go_to_process)):
            button = Gtk.Button(label=label, use_underline=True)
            button.connect("clicked", lambda _b, cb=callback: cb())
            button.set_sensitive(self.backend.available or label == "_New Task...")
            footer.pack_end(button, False, False, 0)
        self.pack_start(footer, False, False, 0)

    # -- data ---------------------------------------------------------------
    def update(self, _snapshot=None):
        if not self.backend.available:
            self.count.set_text("Window list unavailable in this session.")
            return
        wins = self.backend.list_windows()
        self._by_handle = {w.handle: w for w in wins}
        rows = []
        for w in wins:
            if w.urgent:
                status = "Needs attention"
            elif w.minimised:
                status = "Minimised"
            elif w.focused:
                status = "Active"
            else:
                status = "Running"
            rows.append((w.handle, (
                self.icons.icon_for(w.app_id or "", ""),
                w.title, status, w.workspace or "-", w.app_id or "-", w.pid,
            )))
        self.table.sync(rows)
        self.count.set_text(f"{len(wins)} window{'' if len(wins) == 1 else 's'} open"
                            f"   ({self.backend.name})")

    # -- actions ------------------------------------------------------------
    def _selected(self):
        return [self._by_handle[h] for h in self.table.selected_keys()
                if h in self._by_handle]

    def end_task(self):
        errors = []
        for w in self._selected():
            errors += self.backend.close(w.handle)
        self._report(errors, "Close request sent.")

    def switch_to(self):
        selected = self._selected()
        if not selected:
            return
        errors = self.backend.activate(selected[0].handle)
        self._report(errors)
        if not errors and self.window.cfg["minimise_on_use"]:
            self.window.iconify()

    def new_task(self):
        self.window.new_task()

    def go_to_process(self):
        selected = self._selected()
        if selected and selected[0].pid:
            self.window.go_to_process(selected[0].pid)

    def _report(self, errors, success=""):
        if errors:
            self.window.set_status(errors[0])
        elif success:
            self.window.set_status(success)
