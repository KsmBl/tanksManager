"""A small keyed list view used by the Networking, Users, Services,
Applications and Disks tabs.

Rows are matched on a stable key and updated in place, so selection and
scroll position survive the once-a-second refresh.
"""

from __future__ import annotations

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GObject, Pango  # noqa: E402


class Column:
    def __init__(self, title, gtype=str, format=None, right=False,
                 width=140, expand=False, icon_col=None, hidden=False):
        self.title = title
        self.gtype = gtype
        self.format = format
        self.right = right
        self.width = width
        self.expand = expand
        # icon_col: 0-based index of another Column holding an icon name,
        # rendered to the left of this column's text.
        self.icon_col = icon_col
        self.hidden = hidden


class KeyedTable(Gtk.ScrolledWindow):
    def __init__(self, columns, sort=None, multiple=False):
        super().__init__()
        self.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.set_shadow_type(Gtk.ShadowType.IN)
        self.columns = columns
        # column 0 is the hidden key
        types = [str] + [c.gtype for c in columns]
        self.store = Gtk.ListStore(*types)
        self.sorted = Gtk.TreeModelSort(model=self.store)
        self.view = Gtk.TreeView(model=self.sorted)
        self.view.set_enable_search(False)
        self.view.get_selection().set_mode(
            Gtk.SelectionMode.MULTIPLE if multiple else Gtk.SelectionMode.BROWSE)
        self._rows = {}
        self._cache = {}

        for index, spec in enumerate(columns, start=1):
            column = Gtk.TreeViewColumn(spec.title)
            column.set_resizable(True)
            column.set_sizing(Gtk.TreeViewColumnSizing.FIXED)
            column.set_fixed_width(spec.width)
            column.set_min_width(48)
            column.set_expand(spec.expand)
            column.set_visible(not spec.hidden)
            if spec.icon_col is not None:
                pix = Gtk.CellRendererPixbuf()
                pix.set_property("stock-size", Gtk.IconSize.MENU)
                column.pack_start(pix, False)
                column.add_attribute(pix, "icon-name", spec.icon_col + 1)
            cell = Gtk.CellRendererText()
            cell.set_property("ellipsize", Pango.EllipsizeMode.END)
            if spec.right:
                cell.set_property("xalign", 1.0)
            column.pack_start(cell, True)
            if spec.format is None:
                column.add_attribute(cell, "text", index)
            else:
                column.set_cell_data_func(cell, self._formatter(index, spec.format))
            column.set_sort_column_id(index)
            self.view.append_column(column)

        if sort is not None:
            col, descending = sort
            self.sorted.set_sort_column_id(
                col + 1, Gtk.SortType.DESCENDING if descending else Gtk.SortType.ASCENDING)
        self.add(self.view)

    @staticmethod
    def _formatter(index, fn):
        def cell_data(_column, cell, model, it, _data):
            cell.set_property("text", fn(model.get_value(it, index)))
        return cell_data

    # -- data ---------------------------------------------------------------
    def sync(self, rows):
        """rows: iterable of (key, (value, ...))"""
        seen = set()
        for key, values in rows:
            seen.add(key)
            it = self._rows.get(key)
            if it is None:
                self._rows[key] = self.store.append([key] + list(values))
                self._cache[key] = tuple(values)
                continue
            old = self._cache.get(key)
            if old == tuple(values):
                continue
            cols, vals = [], []
            for i, value in enumerate(values):
                if old is None or old[i] != value:
                    cols.append(i + 1)
                    vals.append(value)
            if cols:
                self.store.set(it, cols, vals)
            self._cache[key] = tuple(values)

        for key in list(self._rows):
            if key not in seen:
                it = self._rows.pop(key)
                self._cache.pop(key, None)
                if self.store.iter_is_valid(it):
                    self.store.remove(it)

    def clear(self):
        self.store.clear()
        self._rows.clear()
        self._cache.clear()

    # -- selection ----------------------------------------------------------
    def selected_keys(self):
        model, paths = self.view.get_selection().get_selected_rows()
        return [model.get_value(model.get_iter(p), 0) for p in paths]

    def selected_values(self, index):
        model, paths = self.view.get_selection().get_selected_rows()
        return [model.get_value(model.get_iter(p), index + 1) for p in paths]

    def connect_activate(self, callback):
        self.view.connect("row-activated", lambda *_: callback())

    def connect_context_menu(self, builder):
        def on_press(view, event):
            if event.button != Gdk.BUTTON_SECONDARY:
                return False
            hit = view.get_path_at_pos(int(event.x), int(event.y))
            selection = view.get_selection()
            if hit and not selection.path_is_selected(hit[0]):
                selection.unselect_all()
                selection.select_path(hit[0])
            menu = builder()
            if menu is None:
                return False
            menu.show_all()
            menu.popup_at_pointer(event)
            return True
        self.view.connect("button-press-event", on_press)
