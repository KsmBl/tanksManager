"""The Users tab: who is logged in, and what they are costing the machine."""

from __future__ import annotations

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GObject  # noqa: E402

from .table import Column, KeyedTable
from ..backend import users as users_backend
from ..backend.units import bytes_h, wallclock


class UsersTab(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.window = window
        self._rows = {}

        self.table = KeyedTable([
            Column("User", width=140),
            Column("Session", width=90),
            Column("Terminal", width=110),
            Column("From", width=140),
            Column("Type", width=90),
            Column("Logged on", GObject.TYPE_DOUBLE, wallclock, width=130),
            Column("Processes", int, str, right=True, width=90),
            Column("CPU", GObject.TYPE_DOUBLE, lambda v: f"{v:.1f}",
                   right=True, width=70),
            Column("Memory", GObject.TYPE_UINT64, bytes_h, right=True, width=100),
        ], sort=(8, True), multiple=False)
        self.table.set_border_width(6)
        self.pack_start(self.table, True, True, 0)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        footer.set_border_width(6)
        footer.set_margin_top(0)
        hint = Gtk.Label(
            label="Ending a session closes every program that user is running.",
            xalign=0.0)
        hint.get_style_context().add_class("dim-label")
        footer.pack_start(hint, True, True, 0)
        for label, callback in (("_Log Off", self.log_off),
                                ("_Show Processes", self.show_processes)):
            button = Gtk.Button(label=label, use_underline=True)
            button.connect("clicked", lambda _b, cb=callback: cb())
            footer.pack_end(button, False, False, 0)
        self.pack_start(footer, False, False, 0)

    def update(self, snapshot):
        entries = users_backend.list_users(snapshot.procs)
        self._rows = {}
        rows = []
        for u in entries:
            key = f"{u.name}@{u.terminal}"
            self._rows[key] = u
            rows.append((key, (u.name, u.session_id or "-", u.terminal or "-",
                               u.host or "local", u.kind, u.started,
                               u.procs, u.cpu, u.rss)))
        self.table.sync(rows)

    def _selected(self):
        keys = self.table.selected_keys()
        return self._rows.get(keys[0]) if keys else None

    def log_off(self):
        user = self._selected()
        if user is None:
            return
        dialog = Gtk.MessageDialog(
            transient_for=self.window, modal=True,
            message_type=Gtk.MessageType.WARNING, buttons=Gtk.ButtonsType.OK_CANCEL,
            text=f"Log {user.name} off?")
        dialog.format_secondary_text(
            "Every program in that session will be closed and unsaved work "
            "will be lost.")
        response = dialog.run()
        dialog.destroy()
        if response != Gtk.ResponseType.OK:
            return
        errors = users_backend.logoff(user.session_id)
        self.window.set_status(errors[0] if errors else f"Session {user.session_id} ended.")

    def show_processes(self):
        user = self._selected()
        if user is not None:
            self.window.filter_processes(user.name)
