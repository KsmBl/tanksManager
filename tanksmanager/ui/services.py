"""The Services tab, backed by systemd. Covers both the system manager and
the user session, which the Windows original never had to think about."""

from __future__ import annotations

import threading

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, GObject  # noqa: E402

from .table import Column, KeyedTable
from ..backend import services as backend

REFRESH_EVERY = 5  # seconds; systemctl is a subprocess, not a /proc read


class ServicesTab(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.window = window
        self._units = {}
        self._pids = {}
        self._busy = False
        self._tick = 0
        self._needle = ""

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bar.set_border_width(6)
        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("Filter services")
        self.search.connect("search-changed", self._on_search)
        bar.pack_start(self.search, True, True, 0)

        self.scope = Gtk.ComboBoxText()
        self.scope.append("system", "System services")
        self.scope.append("user", "User services")
        self.scope.set_active_id("system")
        self.scope.connect("changed", lambda *_: self.reload(force=True))
        bar.pack_start(self.scope, False, False, 0)

        self.running_only = Gtk.CheckButton(label="Running only")
        self.running_only.connect("toggled", lambda *_: self._apply())
        bar.pack_start(self.running_only, False, False, 0)
        self.pack_start(bar, False, False, 0)

        self.table = KeyedTable([
            Column("Name", width=230),
            Column("Description", width=320, expand=True),
            Column("Status", width=100),
            Column("Sub-state", width=110),
            Column("Load", width=100),
            Column("PID", int, lambda v: str(v) if v else "", right=True, width=70),
        ], sort=(0, False), multiple=True)
        box = Gtk.Box()
        box.set_border_width(6)
        box.set_margin_top(0)
        box.pack_start(self.table, True, True, 0)
        self.table.connect_context_menu(self._menu)
        self.pack_start(box, True, True, 0)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        footer.set_border_width(6)
        footer.set_margin_top(0)
        self.count = Gtk.Label(label="", xalign=0.0)
        self.count.get_style_context().add_class("dim-label")
        footer.pack_start(self.count, True, True, 0)
        if not backend.HAVE_SYSTEMCTL:
            self.count.set_text("systemctl was not found - this tab needs systemd.")
        for label, action in (("_Start", "start"), ("Sto_p", "stop"),
                              ("_Restart", "restart")):
            button = Gtk.Button(label=label, use_underline=True)
            button.connect("clicked", lambda _b, a=action: self.control(a))
            button.set_sensitive(backend.HAVE_SYSTEMCTL)
            footer.pack_end(button, False, False, 0)
        goto = Gtk.Button(label="_Go To Process", use_underline=True)
        goto.connect("clicked", lambda *_: self.go_to_process())
        footer.pack_end(goto, False, False, 0)
        self.pack_start(footer, False, False, 0)

    # -- data ---------------------------------------------------------------
    def _on_search(self, entry):
        self._needle = entry.get_text().strip().lower()
        self._apply()

    def update(self, _snapshot=None):
        """Called once a second by the window; only actually shells out every
        few seconds."""
        if self._tick % REFRESH_EVERY == 0:
            self.reload()
        self._tick += 1

    def reload(self, force=False):
        if not backend.HAVE_SYSTEMCTL or (self._busy and not force):
            return
        self._busy = True
        scope = self.scope.get_active_id() or "system"
        threading.Thread(target=self._load, args=(scope,), daemon=True,
                         name="systemctl").start()

    def _load(self, scope):
        units = backend.list_units(scope)
        running = [u.unit for u in units if u.sub == "running"]
        pids = backend.main_pids(running, scope)
        GLib.idle_add(self._loaded, scope, units, pids)

    def _loaded(self, scope, units, pids):
        self._busy = False
        if (self.scope.get_active_id() or "system") != scope:
            return False
        self._units = {u.unit: u for u in units}
        self._pids = pids
        self._apply()
        return False

    def _apply(self):
        rows = []
        running_only = self.running_only.get_active()
        active = 0
        for unit, u in self._units.items():
            if u.active == "active":
                active += 1
            if running_only and u.sub != "running":
                continue
            if self._needle and self._needle not in unit.lower() \
                    and self._needle not in u.description.lower():
                continue
            rows.append((unit, (u.name, u.description, u.active.title(),
                                u.sub, u.load, self._pids.get(unit, 0))))
        self.table.sync(rows)
        self.count.set_text(f"{len(self._units)} units, {active} active"
                            f"   ·   showing {len(rows)}")

    # -- actions ------------------------------------------------------------
    def _menu(self):
        units = self.table.selected_keys()
        if not units:
            return None
        menu = Gtk.Menu()
        for label, action in (("Start", "start"), ("Stop", "stop"),
                              ("Restart", "restart"), ("Reload", "reload")):
            item = Gtk.MenuItem(label=label)
            item.connect("activate", lambda _i, a=action: self.control(a))
            menu.append(item)
        menu.append(Gtk.SeparatorMenuItem())
        for label, action in (("Enable at boot", "enable"),
                              ("Disable at boot", "disable")):
            item = Gtk.MenuItem(label=label)
            item.connect("activate", lambda _i, a=action: self.control(a))
            menu.append(item)
        menu.append(Gtk.SeparatorMenuItem())
        status = Gtk.MenuItem(label="View Status Output...")
        status.connect("activate", lambda *_: self.show_status())
        menu.append(status)
        goto = Gtk.MenuItem(label="Go To Process")
        goto.connect("activate", lambda *_: self.go_to_process())
        menu.append(goto)
        return menu

    def control(self, action):
        units = self.table.selected_keys()
        if not units:
            return
        scope = self.scope.get_active_id() or "system"
        errors = []
        for unit in units:
            errors += backend.control(action, unit, scope)
        if errors:
            self.window.set_status(errors[0].splitlines()[0])
        else:
            self.window.set_status(f"{action.title()}ed {len(units)} unit(s).")
        GLib.timeout_add(400, lambda: (self.reload(force=True), False)[1])

    def show_status(self):
        units = self.table.selected_keys()
        if not units:
            return
        scope = self.scope.get_active_id() or "system"
        text = backend.unit_status(units[0], scope)
        dialog = Gtk.Window(title=f"systemctl status {units[0]}")
        dialog.set_transient_for(self.window)
        dialog.set_default_size(760, 460)
        view = Gtk.TextView()
        view.set_editable(False)
        view.set_monospace(True)
        view.set_left_margin(8)
        view.set_top_margin(6)
        view.get_buffer().set_text(text)
        scroller = Gtk.ScrolledWindow()
        scroller.add(view)
        dialog.add(scroller)
        dialog.show_all()

    def go_to_process(self):
        units = self.table.selected_keys()
        if not units:
            return
        pid = self._pids.get(units[0], 0)
        if pid:
            self.window.go_to_process(pid)
        else:
            self.window.set_status(f"{units[0]} has no main process running.")
