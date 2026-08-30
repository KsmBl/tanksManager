"""Main window: menu bar, tabs, status bar - the Windows XP/7 skeleton."""

from __future__ import annotations

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib  # noqa: E402

from .. import APP_NAME
from ..backend import actions
from ..backend.config import UPDATE_SPEEDS
from ..backend.sampler import Sampler
from ..backend.units import bytes_h
from . import dialogs
from .applications import ApplicationsTab
from .performance import PerformanceTab
from .processes import ProcessTab, COLUMNS
from .services import ServicesTab
from .users import UsersTab

CSS = b"""
.tm-big { font-size: 160%; font-weight: bold; }
.tm-card-title { font-weight: bold; }
#core-label { font-size: 80%; }
statusbar label { padding: 0 2px; }
"""


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, application, cfg):
        super().__init__(application=application, title=APP_NAME)
        self.cfg = cfg
        self._last = None
        self._status_reset = 0
        self._wayland = Gdk.Display.get_default().__class__.__name__.startswith("Wayland")

        self.set_default_size(*(cfg["window"] or [760, 560]))
        if cfg["maximised"]:
            self.maximize()
        self.set_icon_name("utilities-system-monitor")

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(root)

        self.notebook = Gtk.Notebook()
        self.notebook.set_border_width(0)
        self.notebook.set_scrollable(True)

        self.tabs = {}
        self.apps_tab = ApplicationsTab(self)
        self.proc_tab = ProcessTab(self)
        self.perf_tab = PerformanceTab(self)
        self.users_tab = UsersTab(self)
        self.services_tab = ServicesTab(self)
        pages = [
            ("Applications", self.apps_tab),
            ("Processes", self.proc_tab),
            ("Performance", self.perf_tab),
            ("Users", self.users_tab),
            ("Services", self.services_tab),
        ]
        for title, widget in pages:
            self.notebook.append_page(widget, Gtk.Label(label=title))
        self.notebook.connect("switch-page", self._on_switch_page)

        root.pack_start(self._build_menu(), False, False, 0)
        root.pack_start(self.notebook, True, True, 0)
        root.pack_start(self._build_statusbar(), False, False, 0)

        self.proc_tab.connect("status-message", lambda _t, msg: self.set_status(msg))

        self.sampler = Sampler(self._deliver, UPDATE_SPEEDS[cfg["update_speed"]])
        self.sampler.start()

        self.connect("delete-event", self._on_delete)
        self.connect("key-press-event", self._on_key_press)
        self.connect("window-state-event", self._on_state)

        index = min(max(0, int(cfg["tab"])), len(pages) - 1)
        self.show_all()
        self.notebook.set_current_page(index)
        self.perf_tab.apply_options()
        self._apply_always_on_top()

    # -- chrome -------------------------------------------------------------
    def _build_menu(self):
        bar = Gtk.MenuBar()

        def menu(label):
            item = Gtk.MenuItem(label=label, use_underline=True)
            sub = Gtk.Menu()
            item.set_submenu(sub)
            bar.append(item)
            return sub

        def add(parent, label, callback, accel=None):
            item = Gtk.MenuItem(label=label, use_underline=True)
            item.connect("activate", lambda *_: callback())
            parent.append(item)
            return item

        def add_check(parent, label, key, callback=None):
            item = Gtk.CheckMenuItem(label=label, use_underline=True)
            item.set_active(bool(self.cfg[key]))

            def toggled(widget):
                self.cfg[key] = widget.get_active()
                if callback:
                    callback(widget.get_active())
            item.connect("toggled", toggled)
            parent.append(item)
            return item

        file_menu = menu("_File")
        add(file_menu, "_New Task (Run...)", self.new_task)
        file_menu.append(Gtk.SeparatorMenuItem())
        add(file_menu, "E_xit", lambda: self.get_application().quit())

        options = menu("_Options")
        self.on_top_item = add_check(options, "_Always On Top", "always_on_top",
                                     lambda _v: self._apply_always_on_top())
        if self._wayland:
            self.on_top_item.set_sensitive(False)
            self.on_top_item.set_tooltip_text(
                "Wayland compositors decide stacking themselves. Use your "
                "window manager's own always-on-top binding.")
        add_check(options, "_Minimise On Use", "minimise_on_use")
        add_check(options, "_Confirm Before Ending A Process", "confirm_kill")
        options.append(Gtk.SeparatorMenuItem())
        add_check(options, "_Classic Graph Colours (green on black)",
                  "classic_graphs", self._apply_classic)

        view = menu("_View")
        add(view, "_Refresh Now", self.refresh_now)

        speed_item = Gtk.MenuItem(label="_Update Speed", use_underline=True)
        speed_menu = Gtk.Menu()
        group = []
        for key, label in (("high", "_High"), ("normal", "_Normal"),
                           ("low", "_Low"), ("paused", "_Paused")):
            item = Gtk.RadioMenuItem(label=label, use_underline=True,
                                     group=group[0] if group else None)
            group.append(item)
            if self.cfg["update_speed"] == key:
                item.set_active(True)
            item.connect("toggled", self._on_speed, key)
            speed_menu.append(item)
        speed_item.set_submenu(speed_menu)
        view.append(speed_item)

        view.append(Gtk.SeparatorMenuItem())
        add(view, "Select _Columns...", self.select_columns)
        add_check(view, "Show Processes From _All Users", "all_users",
                  lambda v: self.proc_tab.set_all_users(v))
        add_check(view, "_Tree View", "tree_view",
                  lambda v: self.proc_tab.tree_toggle.set_active(v))
        add_check(view, "Show _Kernel Threads", "kernel_threads",
                  lambda _v: self.proc_tab.refilter())
        view.append(Gtk.SeparatorMenuItem())

        history_item = Gtk.MenuItem(label="CPU _History", use_underline=True)
        history_menu = Gtk.Menu()
        history_group = []
        for per_cpu, label in ((False, "One Graph, _All CPUs"),
                               (True, "One Graph _Per CPU")):
            item = Gtk.RadioMenuItem(label=label, use_underline=True,
                                     group=history_group[0] if history_group else None)
            history_group.append(item)
            if bool(self.cfg["one_graph_per_cpu"]) == per_cpu:
                item.set_active(True)
            item.connect("toggled", self._on_cpu_history, per_cpu)
            history_menu.append(item)
        history_item.set_submenu(history_menu)
        view.append(history_item)

        add_check(view, "Show _Kernel Times", "show_kernel_times",
                  lambda v: self.perf_tab.set_kernel_times(v))
        add_check(view, "CPU Column Counts _Each Core Separately",
                  "cpu_per_core_scale", lambda _v: self.proc_tab.refilter())

        help_menu = menu("_Help")
        add(help_menu, "_About Tanks Manager", lambda: dialogs.show_about(self))
        return bar

    def _build_statusbar(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        bar.get_style_context().add_class("statusbar")
        bar.set_border_width(4)
        self.status_left = Gtk.Label(label="Starting...", xalign=0.0)
        self.status_procs = Gtk.Label(label="", xalign=0.0)
        self.status_cpu = Gtk.Label(label="", xalign=0.0)
        self.status_mem = Gtk.Label(label="", xalign=0.0)
        bar.pack_start(self.status_left, True, True, 4)
        for widget in (self.status_procs, self.status_cpu, self.status_mem):
            bar.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL),
                           False, False, 6)
            bar.pack_start(widget, False, False, 4)
        return bar

    # -- sampling -----------------------------------------------------------
    def _deliver(self, snapshot):
        GLib.idle_add(self._on_snapshot, snapshot, priority=GLib.PRIORITY_DEFAULT_IDLE)

    def _on_snapshot(self, snapshot):
        self._last = snapshot
        s = snapshot.system
        self.status_procs.set_text(f"Processes: {s.nprocs}")
        self.status_cpu.set_text(f"CPU Usage: {s.cpu_total:.0f}%")
        used = (s.mem.total - s.mem.available) / s.mem.total * 100 if s.mem.total else 0
        self.status_mem.set_text(f"Physical Memory: {used:.0f}%")
        if self._status_reset and GLib.get_monotonic_time() > self._status_reset:
            self._status_reset = 0
            self.status_left.set_text(self._idle_status())
        elif not self._status_reset:
            self.status_left.set_text(self._idle_status())
        self._refresh_current_tab()
        return False

    def _idle_status(self):
        if self.cfg["update_speed"] == "paused":
            return "Paused - press F5 to refresh once."
        s = self._last.system if self._last else None
        if s is None:
            return ""
        return (f"Up {int(s.uptime // 86400)}d "
                f"{int(s.uptime % 86400 // 3600)}h   ·   "
                f"{bytes_h(s.mem.total - s.mem.available)} of "
                f"{bytes_h(s.mem.total)} in use   ·   "
                f"load {s.load_avg[0]:.2f}")

    def _refresh_current_tab(self):
        if self._last is None:
            return
        page = self.notebook.get_nth_page(self.notebook.get_current_page())
        if page is self.proc_tab:
            self.proc_tab.update(self._last.procs)
        elif page is self.perf_tab:
            self.perf_tab.update(self._last.system)
        elif page is self.users_tab:
            self.users_tab.update(self._last)
        elif page is self.services_tab:
            self.services_tab.update(self._last)
        elif page is self.apps_tab:
            self.apps_tab.update(self._last)
        # The performance graphs must not lose history while another tab is
        # in front, so they are always fed.
        if page is not self.perf_tab:
            self.perf_tab.update(self._last.system)

    def _on_switch_page(self, _notebook, _page, index):
        self.cfg["tab"] = index
        GLib.idle_add(self._refresh_current_tab)

    def _on_speed(self, item, key):
        if not item.get_active():
            return
        self.cfg["update_speed"] = key
        self.sampler.set_interval(UPDATE_SPEEDS[key])
        self.sampler.set_paused(key == "paused")
        self.status_left.set_text(self._idle_status())

    def _on_cpu_history(self, item, per_cpu):
        if not item.get_active():
            return
        self.cfg["one_graph_per_cpu"] = per_cpu
        self.perf_tab.set_one_graph_per_cpu(per_cpu)

    def refresh_now(self):
        self.sampler.refresh_now()

    # -- options ------------------------------------------------------------
    def _apply_classic(self, classic):
        self.perf_tab.set_classic(classic)

    def _apply_always_on_top(self):
        if not self._wayland:
            self.set_keep_above(bool(self.cfg["always_on_top"]))

    def select_columns(self):
        dialog = dialogs.ColumnsDialog(
            self, [(c[0], c[1]) for c in COLUMNS], self.proc_tab.visible_columns())
        chosen = dialog.run_and_get()
        if chosen is not None:
            self.proc_tab.apply_visible_columns(chosen)
            self.cfg["columns"] = chosen

    # -- cross-tab actions --------------------------------------------------
    def new_task(self):
        dialog = dialogs.RunDialog(self)
        result = dialog.run_and_get()
        if result is None:
            return
        command, as_shell = result
        errors = actions.run_new_task(command, as_shell)
        self.set_status(errors[0] if errors else f"Started {command}.")

    def go_to_process(self, pid):
        self.notebook.set_current_page(1)
        self.proc_tab.set_all_users(True)
        self.proc_tab.search.set_text("")
        self.proc_tab.refilter()
        self.proc_tab.focus_pid(pid)
        self.set_status(f"Selected PID {pid}.")

    def filter_processes(self, text):
        self.notebook.set_current_page(1)
        self.proc_tab.set_all_users(True)
        self.proc_tab.search.set_text(text)

    def set_status(self, message):
        self.status_left.set_text(message)
        self._status_reset = GLib.get_monotonic_time() + 6_000_000

    # -- lifecycle ----------------------------------------------------------
    def _on_key_press(self, _widget, event):
        ctrl = event.state & Gdk.ModifierType.CONTROL_MASK
        if event.keyval == Gdk.KEY_F5:
            self.refresh_now()
            return True
        if ctrl and event.keyval in (Gdk.KEY_f, Gdk.KEY_F):
            self.notebook.set_current_page(1)
            self.proc_tab.focus_search()
            return True
        if ctrl and event.keyval in (Gdk.KEY_n, Gdk.KEY_N):
            self.new_task()
            return True
        if ctrl and event.keyval in (Gdk.KEY_q, Gdk.KEY_Q, Gdk.KEY_w, Gdk.KEY_W):
            self.get_application().quit()
            return True
        if event.keyval == Gdk.KEY_Escape:
            page = self.notebook.get_nth_page(self.notebook.get_current_page())
            if page is self.proc_tab and self.proc_tab.search.get_text():
                self.proc_tab.search.set_text("")
                return True
        return False

    def _on_state(self, _widget, event):
        self.cfg["maximised"] = bool(
            event.new_window_state & Gdk.WindowState.MAXIMIZED)
        return False

    def _on_delete(self, *_args):
        self.save_state()
        return False

    def save_state(self):
        if not self.cfg["maximised"]:
            self.cfg["window"] = list(self.get_size())
        self.proc_tab.save_state()
        self.perf_tab.save_state()
        self.cfg.save()
