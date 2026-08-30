"""Secondary windows: process properties, affinity, Run..., column chooser."""

from __future__ import annotations

import os

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Pango  # noqa: E402

import psutil

from .. import APP_NAME, __version__
from ..backend import actions
from ..backend.sampler import NPROC
from ..backend.units import bytes_h, cpu_time, wallclock, nice_label


def _mono_page(text):
    view = Gtk.TextView()
    view.set_editable(False)
    view.set_monospace(True)
    view.set_left_margin(8)
    view.set_right_margin(8)
    view.set_top_margin(6)
    view.get_buffer().set_text(text)
    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    scroller.add(view)
    return scroller


def _grid_page(rows):
    grid = Gtk.Grid(row_spacing=6, column_spacing=14)
    grid.set_border_width(12)
    for i, (label, value) in enumerate(rows):
        key = Gtk.Label(label=label, xalign=1.0)
        key.get_style_context().add_class("dim-label")
        val = Gtk.Label(label=str(value), xalign=0.0)
        val.set_selectable(True)
        val.set_line_wrap(True)
        val.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        val.set_max_width_chars(64)
        grid.attach(key, 0, i, 1, 1)
        grid.attach(val, 1, i, 1, 1)
    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroller.add(grid)
    return scroller


class PropertiesDialog(Gtk.Window):
    """Everything /proc knows about one process, in one place."""

    def __init__(self, parent, pid):
        super().__init__(title=f"Properties - PID {pid}")
        self.set_transient_for(parent)
        self.set_default_size(620, 520)
        self.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        self.pid = pid

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.notebook = Gtk.Notebook()
        self.notebook.set_border_width(6)
        box.pack_start(self.notebook, True, True, 0)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        buttons.set_border_width(8)
        refresh = Gtk.Button(label="_Refresh", use_underline=True)
        refresh.connect("clicked", lambda *_: self.reload())
        close = Gtk.Button(label="_Close", use_underline=True)
        close.connect("clicked", lambda *_: self.destroy())
        buttons.pack_start(refresh, False, False, 0)
        buttons.pack_end(close, False, False, 0)
        box.pack_start(buttons, False, False, 0)
        self.add(box)
        self.reload()

    def reload(self):
        page = self.notebook.get_current_page()
        for child in self.notebook.get_children():
            self.notebook.remove(child)
        try:
            self._populate()
        except psutil.NoSuchProcess:
            self.notebook.append_page(
                _grid_page([("", f"Process {self.pid} has exited.")]),
                Gtk.Label(label="General"))
        self.notebook.show_all()
        if 0 <= page < self.notebook.get_n_pages():
            self.notebook.set_current_page(page)

    def _populate(self):
        p = psutil.Process(self.pid)
        with p.oneshot():
            def safe(fn, default="(not permitted)"):
                try:
                    return fn()
                except (psutil.AccessDenied, psutil.Error, OSError):
                    return default

            mem = safe(p.memory_info, None)
            general = [
                ("Image name", p.name()),
                ("PID", p.pid),
                ("Parent PID", p.ppid()),
                ("User", safe(p.username, "?")),
                ("Status", p.status().title()),
                ("Started", wallclock(p.create_time())),
                ("CPU time", cpu_time(sum(p.cpu_times()[:2]))),
                ("Threads", p.num_threads()),
                ("Priority", nice_label(p.nice())),
                ("Executable", safe(p.exe, "")),
                ("Working directory", safe(p.cwd, "")),
                ("Command line", " ".join(safe(p.cmdline, []) or [])),
                ("Terminal", safe(p.terminal, "") or "-"),
                ("Open files", len(safe(p.open_files, []) or [])),
                ("File descriptors", safe(p.num_fds, "-")),
                ("CPU affinity", ", ".join(str(c) for c in (actions.get_affinity(self.pid) or []))),
            ]
            self.notebook.append_page(_grid_page(general), Gtk.Label(label="General"))

            if mem is not None:
                full = safe(p.memory_full_info, None)
                rows = [
                    ("Resident (RSS)", bytes_h(mem.rss)),
                    ("Virtual (VMS)", bytes_h(mem.vms)),
                    ("Shared", bytes_h(getattr(mem, "shared", 0))),
                    ("Text", bytes_h(getattr(mem, "text", 0))),
                    ("Data", bytes_h(getattr(mem, "data", 0))),
                    ("Percent of RAM", f"{p.memory_percent():.2f} %"),
                ]
                if full is not None and hasattr(full, "uss"):
                    rows += [("Unique (USS)", bytes_h(full.uss)),
                             ("Proportional (PSS)", bytes_h(full.pss)),
                             ("Swapped", bytes_h(full.swap))]
                self.notebook.append_page(_grid_page(rows), Gtk.Label(label="Memory"))

            files = safe(p.open_files, [])
            text = "\n".join(f.path for f in files) if files else "(none)"
            self.notebook.append_page(_mono_page(text), Gtk.Label(label="Files"))

            conns = safe(p.net_connections, [])
            if isinstance(conns, list) and conns:
                lines = []
                for c in conns:
                    laddr = f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else "-"
                    raddr = f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else "-"
                    lines.append(f"{c.type.name:<11} {laddr:<24} {raddr:<24} {c.status}")
                conn_text = "\n".join(lines)
            else:
                conn_text = conns if isinstance(conns, str) else "(none)"
            self.notebook.append_page(_mono_page(conn_text), Gtk.Label(label="Connections"))

            threads = safe(p.threads, [])
            if isinstance(threads, list):
                lines = [f"{'TID':>8}  {'User':>10}  {'System':>10}"]
                lines += [f"{t.id:>8}  {t.user_time:>10.2f}  {t.system_time:>10.2f}"
                          for t in threads]
                thread_text = "\n".join(lines)
            else:
                thread_text = str(threads)
            self.notebook.append_page(_mono_page(thread_text), Gtk.Label(label="Threads"))

            env = safe(p.environ, {})
            if isinstance(env, dict):
                env_text = "\n".join(f"{k}={v}" for k, v in sorted(env.items())) or "(none)"
            else:
                env_text = str(env)
            self.notebook.append_page(_mono_page(env_text), Gtk.Label(label="Environment"))


class AffinityDialog(Gtk.Dialog):
    """Which logical CPUs a process is allowed to run on."""

    def __init__(self, parent, proc):
        super().__init__(title=f"Processor affinity - {proc.name}",
                         transient_for=parent, modal=True)
        self.add_button("_Cancel", Gtk.ResponseType.CANCEL)
        self.add_button("_OK", Gtk.ResponseType.OK)
        self.set_default_response(Gtk.ResponseType.OK)

        current = set(actions.get_affinity(proc.pid) or range(NPROC))
        area = self.get_content_area()
        area.set_border_width(12)
        area.set_spacing(8)
        area.pack_start(Gtk.Label(
            label=f"Allow <b>{GLib.markup_escape_text(proc.name)}</b> (PID {proc.pid}) "
                  "to run on:", use_markup=True, xalign=0.0), False, False, 0)

        grid = Gtk.Grid(row_spacing=2, column_spacing=16)
        self.checks = []
        per_col = (NPROC + 3) // 4 if NPROC > 8 else NPROC
        for i in range(NPROC):
            check = Gtk.CheckButton(label=f"CPU {i}")
            check.set_active(i in current)
            grid.attach(check, i // per_col, i % per_col, 1, 1)
            self.checks.append(check)
        area.pack_start(grid, True, True, 0)

        buttons = Gtk.Box(spacing=6)
        for label, value in (("All", True), ("None", False)):
            button = Gtk.Button(label=label)
            button.connect("clicked", lambda _b, v=value:
                           [c.set_active(v) for c in self.checks])
            buttons.pack_start(button, False, False, 0)
        area.pack_start(buttons, False, False, 0)

    def run_and_get(self):
        self.show_all()
        response = self.run()
        cpus = [i for i, c in enumerate(self.checks) if c.get_active()]
        self.destroy()
        if response != Gtk.ResponseType.OK or not cpus:
            return None
        return cpus


class RunDialog(Gtk.Dialog):
    """File > New Task (Run...) - the XP 'Create New Task' box."""

    def __init__(self, parent):
        super().__init__(title="Create New Task", transient_for=parent, modal=True)
        self.add_button("_Cancel", Gtk.ResponseType.CANCEL)
        self.add_button("_OK", Gtk.ResponseType.OK)
        self.set_default_response(Gtk.ResponseType.OK)

        area = self.get_content_area()
        area.set_border_width(12)
        area.set_spacing(8)
        area.pack_start(Gtk.Label(
            label="Type the name of a program, and Tanks Manager will start it "
                  "for you.", xalign=0.0, wrap=True), False, False, 0)

        self.entry = Gtk.Entry()
        self.entry.set_activates_default(True)
        self.entry.set_width_chars(40)
        completion = Gtk.EntryCompletion()
        store = Gtk.ListStore(str)
        for name in sorted(self._path_binaries())[:4000]:
            store.append([name])
        completion.set_model(store)
        completion.set_text_column(0)
        completion.set_inline_completion(True)
        self.entry.set_completion(completion)
        area.pack_start(self.entry, False, False, 0)

        self.shell = Gtk.CheckButton(label="Run through the shell (allows pipes and $VARS)")
        area.pack_start(self.shell, False, False, 0)

    @staticmethod
    def _path_binaries():
        names = set()
        for directory in (os.environ.get("PATH") or "").split(os.pathsep):
            try:
                with os.scandir(directory) as it:
                    for entry in it:
                        if entry.is_file() and os.access(entry.path, os.X_OK):
                            names.add(entry.name)
            except OSError:
                continue
        return names

    def run_and_get(self):
        self.show_all()
        response = self.run()
        command, as_shell = self.entry.get_text(), self.shell.get_active()
        self.destroy()
        if response != Gtk.ResponseType.OK:
            return None
        return command, as_shell


class ColumnsDialog(Gtk.Dialog):
    """View > Select Columns..., straight out of Windows."""

    def __init__(self, parent, all_columns, visible):
        super().__init__(title="Select Columns", transient_for=parent, modal=True)
        self.add_button("_Cancel", Gtk.ResponseType.CANCEL)
        self.add_button("_OK", Gtk.ResponseType.OK)
        self.set_default_response(Gtk.ResponseType.OK)
        self.set_default_size(320, 420)

        area = self.get_content_area()
        area.set_border_width(12)
        area.set_spacing(8)
        area.pack_start(Gtk.Label(
            label="Select the columns that will appear on the Processes tab.",
            xalign=0.0, wrap=True), False, False, 0)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.checks = []
        for cid, title in all_columns:
            check = Gtk.CheckButton(label=title)
            check.set_active(cid in visible)
            if cid == "name":
                check.set_active(True)
                check.set_sensitive(False)
            box.pack_start(check, False, False, 0)
            self.checks.append((cid, check))
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_shadow_type(Gtk.ShadowType.IN)
        scroller.add(box)
        area.pack_start(scroller, True, True, 0)

    def run_and_get(self):
        self.show_all()
        response = self.run()
        chosen = [cid for cid, check in self.checks if check.get_active()]
        self.destroy()
        return chosen if response == Gtk.ResponseType.OK else None


def show_about(parent):
    dialog = Gtk.AboutDialog(transient_for=parent, modal=True)
    dialog.set_program_name(APP_NAME)
    dialog.set_version(__version__)
    dialog.set_comments(
        "A system monitor in the shape of the Windows XP / 7 Task Manager,\n"
        "built with GTK 3 so it looks and themes like the rest of your desktop.")
    dialog.set_logo_icon_name("utilities-system-monitor")
    dialog.set_license_type(Gtk.License.MIT_X11)
    dialog.set_website_label("Built with PyGObject and psutil")
    dialog.run()
    dialog.destroy()


def show_error(parent, primary, secondary=""):
    dialog = Gtk.MessageDialog(transient_for=parent, modal=True,
                               message_type=Gtk.MessageType.ERROR,
                               buttons=Gtk.ButtonsType.CLOSE, text=primary)
    if secondary:
        dialog.format_secondary_text(secondary)
    dialog.run()
    dialog.destroy()
