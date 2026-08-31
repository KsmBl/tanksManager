"""The Windows 10 style card strip down the left of the Performance tab.

Each card is a live sparkline plus a name and a one-line reading; selecting one
swaps the detail pane on the right.
"""

from __future__ import annotations

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Pango  # noqa: E402

from .graph import HistoryGraph


class ResourceCard(Gtk.ListBoxRow):
    def __init__(self, key, title, classic=False, series=1,
                 classic_series=None, stacked=False):
        super().__init__()
        self.key = key
        self.set_activatable(True)

        self.graph = HistoryGraph(capacity=70, height=44, series=series,
                                  squash=False,
                                  classic=classic, grid=(6, 4), stacked=stacked,
                                  classic_series=classic_series)
        self.graph.set_size_request(78, 44)

        self.title = Gtk.Label(label=title, xalign=0.0)
        self.title.set_ellipsize(Pango.EllipsizeMode.END)
        self.title.get_style_context().add_class("tm-card-title")
        self.subtitle = Gtk.Label(label="", xalign=0.0)
        self.subtitle.set_ellipsize(Pango.EllipsizeMode.END)
        self.subtitle.get_style_context().add_class("dim-label")

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        text.set_valign(Gtk.Align.CENTER)
        text.pack_start(self.title, False, False, 0)
        text.pack_start(self.subtitle, False, False, 0)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        box.set_border_width(7)
        box.pack_start(self.graph, False, False, 0)
        box.pack_start(text, True, True, 0)
        self.add(box)

    def push(self, *values):
        self.graph.push(*values)

    def set_title(self, text):
        if self.title.get_text() != text:
            self.title.set_text(text)

    def set_subtitle(self, text):
        if self.subtitle.get_text() != text:
            self.subtitle.set_text(text)

    def set_classic(self, classic):
        self.graph.classic = classic
        self.graph.queue_draw()
