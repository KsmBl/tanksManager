"""The Processes tab: the heart of the application.

Layout follows the Windows 7 original - a table filling the window, a
'Show processes from all users' toggle bottom-left and 'End Process'
bottom-right - with the things that tool always lacked bolted on: a live
filter, a process tree, per-process I/O, signals and affinity.
"""

from __future__ import annotations

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GObject, GLib, Pango  # noqa: E402

from ..backend import actions, icons
from ..backend.units import (bytes_h, kib, rate, cpu_time, wallclock,
                             nice_label, percent)

# --- model layout ---------------------------------------------------------
(C_PID, C_ICON, C_NAME, C_USER, C_STATUS, C_CPU, C_RSS, C_VMS, C_SHARED,
 C_THREADS, C_NICE, C_CPUTIME, C_START, C_READ, C_WRITE, C_CMD, C_TTY,
 C_PPID, C_VISIBLE, C_OWN) = range(20)

MODEL_TYPES = (
    int, str, str, str, str, float,
    GObject.TYPE_UINT64, GObject.TYPE_UINT64, GObject.TYPE_UINT64,
    int, int, float, float, float, float, str, str,
    int, bool, bool,
)

STATUS_LABELS = {
    "running": "Running", "sleeping": "Sleeping", "disk-sleep": "Uninterruptible",
    "stopped": "Suspended", "tracing-stop": "Traced", "zombie": "Zombie",
    "dead": "Dead", "idle": "Idle", "waking": "Waking", "parked": "Parked",
}


def _f_cpu(_c, cell, model, it, _d):
    value = model.get_value(it, C_CPU)
    cell.set_property("text", "" if value < 0.05 else f"{value:.1f}")


def _f_kib(col_id):
    def fn(_c, cell, model, it, _d):
        cell.set_property("text", kib(model.get_value(it, col_id)))
    return fn


def _f_bytes(col_id):
    def fn(_c, cell, model, it, _d):
        cell.set_property("text", bytes_h(model.get_value(it, col_id)))
    return fn


def _f_rate(col_id):
    def fn(_c, cell, model, it, _d):
        cell.set_property("text", rate(model.get_value(it, col_id)))
    return fn


def _f_status(_c, cell, model, it, _d):
    raw = model.get_value(it, C_STATUS)
    cell.set_property("text", STATUS_LABELS.get(raw, raw.title()))


def _f_nice(_c, cell, model, it, _d):
    cell.set_property("text", nice_label(model.get_value(it, C_NICE)))


def _f_cputime(_c, cell, model, it, _d):
    cell.set_property("text", cpu_time(model.get_value(it, C_CPUTIME)))


def _f_start(_c, cell, model, it, _d):
    cell.set_property("text", wallclock(model.get_value(it, C_START)))


def _f_int(col_id):
    def fn(_c, cell, model, it, _d):
        cell.set_property("text", str(model.get_value(it, col_id)))
    return fn


# id, title, sort column, formatter, right-aligned, visible by default, width
COLUMNS = [
    ("name",    "Image Name",   C_NAME,    None,             False, True,  240),
    ("pid",     "PID",          C_PID,     _f_int(C_PID),    True,  True,   64),
    ("user",    "User Name",    C_USER,    None,             False, True,  110),
    ("cpu",     "CPU",          C_CPU,     _f_cpu,           True,  True,   56),
    ("mem",     "Memory",       C_RSS,     _f_kib(C_RSS),    True,  True,   96),
    ("status",  "Status",       C_STATUS,  _f_status,        False, True,   96),
    ("cputime", "CPU Time",     C_CPUTIME, _f_cputime,       True,  False,  84),
    ("vms",     "Virtual Size", C_VMS,     _f_bytes(C_VMS),  True,  False,  96),
    ("shared",  "Shared",       C_SHARED,  _f_bytes(C_SHARED), True, False, 90),
    ("threads", "Threads",      C_THREADS, _f_int(C_THREADS), True, False,  70),
    ("nice",    "Priority",     C_NICE,    _f_nice,          False, False, 110),
    ("read",    "Disk Read",    C_READ,    _f_rate(C_READ),  True,  False,  92),
    ("write",   "Disk Write",   C_WRITE,   _f_rate(C_WRITE), True,  False,  92),
    ("start",   "Started",      C_START,   _f_start,         False, False, 110),
    ("tty",     "Terminal",     C_TTY,     None,             False, False,  90),
    ("ppid",    "Parent PID",   C_PPID,    _f_int(C_PPID),   True,  False,  80),
    ("cmd",     "Command Line", C_CMD,     None,             False, False, 400),
]
COLUMN_TITLES = {c[0]: c[1] for c in COLUMNS}
DEFAULT_VISIBLE = [c[0] for c in COLUMNS if c[5]]


class ProcessTab(Gtk.Box):
    __gsignals__ = {
        "status-message": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.window = window
        self.cfg = window.cfg
        self.icons = icons.index()
        self._rows = {}          # pid -> Gtk.TreeIter
        self._cache = {}         # pid -> tuple of last written values
        self._ppid = {}          # pid -> ppid as currently modelled
        self._collapsed = set()
        self._procs = {}         # pid -> ProcInfo of the last snapshot
        self._pending_focus = None

        self.store = Gtk.TreeStore(*MODEL_TYPES)
        self.filter = self.store.filter_new()
        self.filter.set_visible_column(C_VISIBLE)
        self.sorted = Gtk.TreeModelSort(model=self.filter)

        self._build_toolbar()
        self._build_view()
        self._build_footer()
        self._restore_sort()

    # -- construction -------------------------------------------------------
    def _build_toolbar(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bar.set_border_width(6)

        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("Filter by name, user, PID or command line")
        self.search.set_width_chars(28)
        self.search.connect("search-changed", lambda *_: self.refilter())
        self.search.connect("stop-search", lambda *_: self.search.set_text(""))
        bar.pack_start(self.search, True, True, 0)

        self.tree_toggle = Gtk.ToggleButton()
        self.tree_toggle.set_image(Gtk.Image.new_from_icon_name(
            "view-list-tree-symbolic", Gtk.IconSize.BUTTON))
        self.tree_toggle.set_tooltip_text("Group processes under their parent")
        self.tree_toggle.set_active(bool(self.cfg["tree_view"]))
        self.tree_toggle.connect("toggled", self._on_tree_toggled)
        bar.pack_start(self.tree_toggle, False, False, 0)

        self.pack_start(bar, False, False, 0)

    def _build_view(self):
        self.view = Gtk.TreeView(model=self.sorted)
        self.view.set_enable_search(False)
        self.view.set_rubber_banding(True)
        self.view.set_fixed_height_mode(True)
        self.view.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        self.view.connect("button-press-event", self._on_button_press)
        self.view.connect("popup-menu", lambda *_: self._popup(None))
        self.view.connect("row-activated", lambda *_: self.show_properties())
        self.view.connect("key-press-event", self._on_key_press)
        self.view.connect("row-expanded", self._on_expanded)
        self.view.connect("row-collapsed", self._on_collapsed)
        self.view.set_show_expanders(bool(self.cfg["tree_view"]))

        self._columns = {}
        for cid, title, sort_col, formatter, right, _vis, width in COLUMNS:
            column = Gtk.TreeViewColumn(title)
            column.set_resizable(True)
            column.set_reorderable(True)
            column.set_sizing(Gtk.TreeViewColumnSizing.FIXED)
            column.set_fixed_width(width)
            column.set_min_width(40)
            if cid == "name":
                pix = Gtk.CellRendererPixbuf()
                pix.set_property("stock-size", Gtk.IconSize.MENU)
                column.pack_start(pix, False)
                column.add_attribute(pix, "icon-name", C_ICON)
            cell = Gtk.CellRendererText()
            cell.set_property("ellipsize", Pango.EllipsizeMode.END)
            if right:
                cell.set_property("xalign", 1.0)
            column.pack_start(cell, True)
            if formatter is None:
                column.add_attribute(cell, "text", sort_col)
            else:
                column.set_cell_data_func(cell, formatter, None)
            column.set_sort_column_id(sort_col)
            self.view.append_column(column)
            self._columns[cid] = column

        self.apply_visible_columns(self.cfg["columns"] or DEFAULT_VISIBLE)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_shadow_type(Gtk.ShadowType.IN)
        scroller.add(self.view)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_border_width(6)
        box.set_margin_top(0)
        box.pack_start(scroller, True, True, 0)
        self.pack_start(box, True, True, 0)

    def _build_footer(self):
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        footer.set_border_width(6)
        footer.set_margin_top(0)

        self.all_users = Gtk.CheckButton(label="Show processes from all users")
        self.all_users.set_active(bool(self.cfg["all_users"]))
        self.all_users.connect("toggled", self._on_all_users)
        footer.pack_start(self.all_users, False, False, 0)

        end_button = Gtk.Button(label="_End Process", use_underline=True)
        end_button.connect("clicked", lambda *_: self.end_task())
        footer.pack_end(end_button, False, False, 0)
        self.end_button = end_button

        details = Gtk.Button(label="_Properties", use_underline=True)
        details.connect("clicked", lambda *_: self.show_properties())
        footer.pack_end(details, False, False, 0)

        self.pack_start(footer, False, False, 0)

    def _restore_sort(self):
        cid, direction = (self.cfg["sort"] or ["cpu", "desc"])
        column = next((c for c in COLUMNS if c[0] == cid), None)
        if column is None:
            column = COLUMNS[3]
        order = Gtk.SortType.DESCENDING if direction == "desc" else Gtk.SortType.ASCENDING
        self.sorted.set_sort_column_id(column[2], order)

    def save_state(self):
        self.cfg["columns"] = [cid for cid, col in self._columns.items()
                               if col.get_visible()]
        col_id, order = self.sorted.get_sort_column_id()
        for cid, _t, sort_col, *_rest in COLUMNS:
            if sort_col == col_id:
                self.cfg["sort"] = [cid, "desc" if order == Gtk.SortType.DESCENDING else "asc"]
                break
        self.cfg["tree_view"] = self.tree_toggle.get_active()
        self.cfg["all_users"] = self.all_users.get_active()

    # -- options ------------------------------------------------------------
    def apply_visible_columns(self, visible):
        visible = set(visible) | {"name"}
        for cid, column in self._columns.items():
            column.set_visible(cid in visible)

    def visible_columns(self):
        return [cid for cid, col in self._columns.items() if col.get_visible()]

    def _on_tree_toggled(self, button):
        self.cfg["tree_view"] = button.get_active()
        self.view.set_show_expanders(button.get_active())
        self.rebuild()

    def _on_all_users(self, _button):
        self.cfg["all_users"] = self.all_users.get_active()
        self.refilter()

    def set_all_users(self, value):
        if self.all_users.get_active() != value:
            self.all_users.set_active(value)

    def focus_search(self):
        self.search.grab_focus()

    # -- data ---------------------------------------------------------------
    def rebuild(self):
        selected = set(self.selected_pids())
        self.store.clear()
        self._rows.clear()
        self._cache.clear()
        self._ppid.clear()
        if self._procs:
            self.update(list(self._procs.values()))
            self._select_pids(selected)

    def _row_values(self, p):
        return (
            p.pid,
            self.icons.icon_for(p.name, p.exe),
            p.name,
            p.username,
            p.status,
            p.cpu_raw if self.cfg["cpu_per_core_scale"] else p.cpu,
            p.rss, p.vms, p.shared,
            p.threads, p.nice, p.cpu_time, p.create_time,
            p.read_bps, p.write_bps,
            p.cmdline or p.name,
            p.terminal,
            p.ppid,
            True,
            p.is_own,
        )

    def update(self, procs):
        self._procs = {p.pid: p for p in procs}
        tree_mode = self.tree_toggle.get_active()
        store = self.store

        # --- work out what should be on screen -----------------------------
        needle = self.search.get_text().strip().lower()
        all_users = self.all_users.get_active()
        hidden = set() if self.cfg["kernel_threads"] else self._kernel_threads()
        own = set()
        matched = set()
        for p in procs:
            if not all_users and not p.is_own:
                continue
            if p.pid in hidden:
                continue
            own.add(p.pid)
            if not needle or (needle in p.name.lower()
                              or needle in p.username.lower()
                              or needle in p.cmdline.lower()
                              or needle == str(p.pid)):
                matched.add(p.pid)

        visible = set(matched)
        if tree_mode:
            # Keep the ancestors of every match, otherwise the filter would
            # hide the matches along with their parents.
            for pid in matched:
                cur = self._procs.get(pid)
                depth = 0
                while cur is not None and depth < 64:
                    parent = self._procs.get(cur.ppid)
                    if parent is None or parent.pid in visible:
                        break
                    visible.add(parent.pid)
                    cur = parent
                    depth += 1

        wanted = own if not needle else visible
        if not tree_mode:
            wanted = wanted & own

        # --- drop rows that went away --------------------------------------
        for pid in list(self._rows):
            if pid not in wanted or pid not in self._procs:
                self._remove(pid)

        # --- reparenting ---------------------------------------------------
        readd = []
        if tree_mode:
            for pid, it in list(self._rows.items()):
                p = self._procs.get(pid)
                if p is not None and self._ppid.get(pid) != p.ppid:
                    readd.extend(self._collect_subtree(pid))
                    self._remove(pid)
        readd = [pid for pid in readd if pid in wanted and pid in self._procs]

        # --- insert new rows, parents first --------------------------------
        new = [pid for pid in wanted if pid not in self._rows]
        new.extend(pid for pid in readd if pid not in self._rows)
        if tree_mode and new:
            new.sort(key=self._depth)
        for pid in new:
            p = self._procs.get(pid)
            if p is None:
                continue
            parent_iter = None
            if tree_mode:
                parent_iter = self._rows.get(p.ppid)
            values = self._row_values(p)
            it = store.append(parent_iter, list(values))
            self._rows[pid] = it
            self._cache[pid] = values
            self._ppid[pid] = p.ppid
            if tree_mode and parent_iter is not None and p.ppid not in self._collapsed:
                view_path = self._to_view_path(store.get_path(it))
                if view_path is not None:
                    self.view.expand_to_path(view_path)

        # --- update the rows that stayed ------------------------------------
        changed_cols = []
        changed_vals = []
        for pid, it in self._rows.items():
            p = self._procs.get(pid)
            if p is None:
                continue
            values = self._row_values(p)
            old = self._cache.get(pid)
            if old == values:
                continue
            changed_cols.clear()
            changed_vals.clear()
            for i, value in enumerate(values):
                if old is None or old[i] != value:
                    changed_cols.append(i)
                    changed_vals.append(value)
            if changed_cols:
                store.set(it, list(changed_cols), list(changed_vals))
            self._cache[pid] = values

        if self._pending_focus is not None:
            self._select_pids({self._pending_focus})
            self._pending_focus = None

    def _kernel_threads(self):
        """Anything descended from kthreadd (PID 2). They have no user-space
        address space, so they only ever added noise to the list."""
        out = set()
        for pid, p in self._procs.items():
            chain, cur, guard = [], p, 0
            while cur is not None and guard < 64:
                if cur.pid in out or cur.ppid == 2 or cur.pid == 2:
                    out.update(chain)
                    out.add(cur.pid)
                    break
                chain.append(cur.pid)
                cur = self._procs.get(cur.ppid)
                guard += 1
        return out

    def _depth(self, pid):
        depth, cur, guard = 0, self._procs.get(pid), 0
        while cur is not None and guard < 64:
            parent = self._procs.get(cur.ppid)
            if parent is None or parent.pid == cur.pid:
                break
            depth += 1
            cur = parent
            guard += 1
        return depth

    def _collect_subtree(self, pid):
        """The pid plus every pid modelled underneath it, parents first."""
        out = [pid]
        it = self._rows.get(pid)
        if it is None:
            return out
        stack = [it]
        while stack:
            parent = stack.pop()
            child = self.store.iter_children(parent)
            while child is not None:
                out.append(self.store.get_value(child, C_PID))
                stack.append(child)
                child = self.store.iter_next(child)
        return out

    def _remove(self, pid):
        it = self._rows.get(pid)
        if it is None:
            self._cache.pop(pid, None)
            self._ppid.pop(pid, None)
            return
        # GtkTreeStore drops the whole subtree with the parent, so the
        # bookkeeping for the children has to go at the same moment - their
        # iterators would otherwise dangle until the next full rebuild.
        for dead in self._collect_subtree(pid):
            self._rows.pop(dead, None)
            self._cache.pop(dead, None)
            self._ppid.pop(dead, None)
        if self.store.iter_is_valid(it):
            self.store.remove(it)

    def refilter(self):
        if self._procs:
            self.update(list(self._procs.values()))

    def _on_expanded(self, view, it, _path):
        self._collapsed.discard(view.get_model().get_value(it, C_PID))

    def _on_collapsed(self, view, it, _path):
        model = view.get_model()
        self._collapsed.add(model.get_value(it, C_PID))

    # -- selection ----------------------------------------------------------
    def _to_view_path(self, store_path):
        it = self.store.get_iter(store_path)
        fit = self.filter.convert_child_iter_to_iter(it)
        if not fit[0]:
            return None
        sit = self.sorted.convert_child_iter_to_iter(fit[1])
        if not sit[0]:
            return None
        return self.sorted.get_path(sit[1])

    def selected_pids(self):
        model, paths = self.view.get_selection().get_selected_rows()
        return [model.get_value(model.get_iter(p), C_PID) for p in paths]

    def selected_procs(self):
        return [self._procs[pid] for pid in self.selected_pids() if pid in self._procs]

    def _select_pids(self, pids):
        if not pids:
            return
        selection = self.view.get_selection()
        selection.unselect_all()
        first = True
        for pid in pids:
            store_it = self._rows.get(pid)
            if store_it is None:
                continue
            path = self._to_view_path(self.store.get_path(store_it))
            if path is None:
                continue
            selection.select_path(path)
            if first:
                self.view.scroll_to_cell(path, None, False, 0, 0)
                first = False

    def focus_pid(self, pid):
        self._pending_focus = pid
        self._select_pids({pid})

    # -- interaction --------------------------------------------------------
    def _on_key_press(self, _view, event):
        if event.keyval in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete):
            self.end_task()
            return True
        return False

    def _on_button_press(self, view, event):
        if event.button != Gdk.BUTTON_SECONDARY:
            return False
        hit = view.get_path_at_pos(int(event.x), int(event.y))
        selection = view.get_selection()
        if hit and not selection.path_is_selected(hit[0]):
            selection.unselect_all()
            selection.select_path(hit[0])
        self._popup(event)
        return True

    def _popup(self, event):
        procs = self.selected_procs()
        menu = Gtk.Menu()
        many = len(procs) > 1

        def add(label, callback, sensitive=True):
            item = Gtk.MenuItem(label=label, use_underline=True)
            item.set_sensitive(sensitive and bool(procs))
            if callback:
                item.connect("activate", lambda *_: callback())
            menu.append(item)
            return item

        add("_End Process" + ("es" if many else ""), self.end_task)
        add("End Process _Tree", self.end_tree)
        menu.append(Gtk.SeparatorMenuItem())

        prio = Gtk.MenuItem(label="Set _Priority", use_underline=True)
        prio_menu = Gtk.Menu()
        current = procs[0].nice if procs else 0
        group = []
        for label, value in actions.PRIORITIES:
            item = Gtk.RadioMenuItem(label=label, group=group[0] if group else None)
            group.append(item)
            item.set_active(not many and value == current)
            item.connect("activate", self._on_priority, value)
            prio_menu.append(item)
        prio.set_submenu(prio_menu)
        prio.set_sensitive(bool(procs))
        menu.append(prio)

        affinity = add("Set _Affinity...", self.set_affinity)
        affinity.set_sensitive(bool(procs) and actions.get_affinity(procs[0].pid) is not None)

        sig = Gtk.MenuItem(label="Send _Signal", use_underline=True)
        sig_menu = Gtk.Menu()
        for label, signum, tip in actions.SIGNALS:
            item = Gtk.MenuItem(label=f"{label}  ({signum.name})")
            item.set_tooltip_text(tip)
            item.connect("activate", self._on_signal, signum)
            sig_menu.append(item)
        sig.set_submenu(sig_menu)
        sig.set_sensitive(bool(procs))
        menu.append(sig)

        menu.append(Gtk.SeparatorMenuItem())
        add("Open _File Location", lambda: self._report(actions.open_location(procs[0].pid)),
            not many)
        add("_Copy Command Line", self._copy_cmdline)
        add("P_roperties", self.show_properties, not many)

        menu.show_all()
        if event is not None:
            menu.popup_at_pointer(event)
        else:
            menu.popup_at_widget(self.view, Gdk.Gravity.CENTER, Gdk.Gravity.CENTER, None)

    def _on_priority(self, item, value):
        if not item.get_active():
            return
        pids = self.selected_pids()
        if not pids:
            return
        errors = actions.set_nice(pids, value)
        if errors and value < 0:
            errors.append("Raising priority normally requires root; try "
                          "running Tanks Manager with pkexec.")
        self._report(errors, f"Priority set to {value:+d} for {len(pids)} process(es).")

    def _on_signal(self, _item, signum):
        pids = self.selected_pids()
        self._report(actions.send_signal(pids, signum),
                     f"Sent {signum.name} to {len(pids)} process(es).")

    def _copy_cmdline(self):
        procs = self.selected_procs()
        if not procs:
            return
        text = "\n".join(p.cmdline or p.name for p in procs)
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(text, -1)
        self.emit("status-message", "Command line copied to the clipboard.")

    # -- operations ---------------------------------------------------------
    def _confirm(self, procs, title, body):
        if not self.cfg["confirm_kill"]:
            return True
        dialog = Gtk.MessageDialog(
            transient_for=self.window, modal=True,
            message_type=Gtk.MessageType.WARNING, buttons=Gtk.ButtonsType.NONE,
            text=title)
        dialog.format_secondary_text(body)
        dialog.add_button("_Cancel", Gtk.ResponseType.CANCEL)
        end = dialog.add_button("_End Process", Gtk.ResponseType.OK)
        end.get_style_context().add_class("destructive-action")
        dialog.set_default_response(Gtk.ResponseType.CANCEL)
        check = Gtk.CheckButton(label="Do not ask me again")
        check.set_margin_top(8)
        dialog.get_content_area().pack_start(check, False, False, 0)
        check.show()
        response = dialog.run()
        if check.get_active():
            self.cfg["confirm_kill"] = False
        dialog.destroy()
        return response == Gtk.ResponseType.OK

    def end_task(self):
        procs = self.selected_procs()
        if not procs:
            return
        names = ", ".join(sorted({p.name for p in procs})[:4])
        if not self._confirm(
                procs, "End the selected process?" if len(procs) == 1 else
                f"End {len(procs)} processes?",
                f"{names} will be asked to quit (SIGTERM). Unsaved data in "
                f"{'it' if len(procs) == 1 else 'them'} will be lost."):
            return
        self._report(actions.end_task([p.pid for p in procs]),
                     f"Terminated {len(procs)} process(es).")

    def end_tree(self):
        procs = self.selected_procs()
        if not procs:
            return
        if not self._confirm(procs, "End the process tree?",
                             "The selected processes and every child they "
                             "started will be asked to quit."):
            return
        self._report(actions.end_tree([p.pid for p in procs]),
                     "Process tree terminated.")

    def set_affinity(self):
        from .dialogs import AffinityDialog
        procs = self.selected_procs()
        if not procs:
            return
        dialog = AffinityDialog(self.window, procs[0])
        cpus = dialog.run_and_get()
        if cpus is not None:
            self._report(actions.set_affinity([p.pid for p in procs], cpus),
                         f"Affinity set to {len(cpus)} CPU(s).")

    def show_properties(self):
        from .dialogs import PropertiesDialog
        procs = self.selected_procs()
        if not procs:
            return
        PropertiesDialog(self.window, procs[0].pid).show_all()

    def _report(self, errors, success=""):
        if errors:
            self.emit("status-message", errors[0] if len(errors) == 1
                      else f"{len(errors)} operations failed: {errors[0]}")
        elif success:
            self.emit("status-message", success)
