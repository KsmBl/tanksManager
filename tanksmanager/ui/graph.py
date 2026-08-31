"""Cairo widgets for the Performance and Networking tabs.

Colours come from the GTK style context on every draw, so the graphs follow
the desktop theme. 'Classic' mode is the deliberate exception: it reproduces
the Windows XP Task Manager palette, sampled from a screenshot of the real
thing -

    plate        #000000
    unlit / grid #004000
    user time    #00FF00
    kernel time  #FF0000
    page file    #FFFF00

with the meters drawn as 2px segments separated by a 1px gap, every segment
painted (dark green when unlit), and the reading printed in green inside the
plate underneath the bar.
"""

from __future__ import annotations

import colorsys
import math
from collections import deque

import cairo
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Gdk, Pango, PangoCairo  # noqa: E402

from .tankmode import Battlefield

XP_PLATE = (0.0, 0.0, 0.0)
XP_DARK = (0.0, 0.25, 0.0)          # #004000
XP_GREEN = (0.0, 1.0, 0.0)          # #00FF00
XP_RED = (1.0, 0.0, 0.0)            # #FF0000
XP_YELLOW = (1.0, 1.0, 0.0)         # #FFFF00
CHAR = (0.34, 0.11, 0.03)           # burnt ends of a trace cut by Tank Mode

SEGMENT = 2                          # lit bar height, in pixels
SEGMENT_GAP = 1


def grid_shape(n):
    """Lay n cells out wider than tall, the way Task Manager did."""
    if n <= 1:
        return 1, 1
    rows = max(1, math.ceil(math.sqrt(n / 2.0)))
    cols = math.ceil(n / rows)
    return cols, rows


def _rgba(context: Gtk.StyleContext, name: str, fallback):
    found, colour = context.lookup_color(name)
    if not found:
        return fallback
    return (colour.red, colour.green, colour.blue)


def _mix(a, b, t):
    return tuple(a[i] * (1.0 - t) + b[i] * t for i in range(3))


def _rotate(rgb, turns):
    h, l, s = colorsys.rgb_to_hls(*rgb)
    return colorsys.hls_to_rgb((h + turns) % 1.0, l, max(s, 0.45))


class Palette:
    """Resolved once per draw and shared by every element of a widget."""

    def __init__(self, widget: Gtk.Widget, classic: bool):
        ctx = widget.get_style_context()
        self.classic = classic
        if classic:
            self.bg = XP_PLATE
            self.grid = XP_DARK
            self.unlit = XP_DARK
            self.frame = XP_DARK
            self.text = XP_GREEN
            self._series = [XP_GREEN, XP_YELLOW, XP_RED, (0.0, 0.85, 0.55)]
            self.fill_alpha = 1.0
            self.line_width = 1.0
            return
        base = _rgba(ctx, "theme_base_color", (1, 1, 1))
        fg = _rgba(ctx, "theme_fg_color", (0, 0, 0))
        accent = _rgba(ctx, "theme_selected_bg_color", (0.2, 0.5, 0.9))
        self.bg = _mix(base, fg, 0.06)
        self.grid = _mix(self.bg, fg, 0.16)
        self.unlit = _mix(self.bg, fg, 0.13)
        self.frame = _mix(self.bg, fg, 0.32)
        self.text = _mix(self.bg, fg, 0.75)
        self._series = [accent, _rotate(accent, 0.45), _rotate(accent, 0.18),
                        _rotate(accent, 0.72)]
        self.fill_alpha = 0.22
        self.line_width = 1.4

    def series(self, index: int):
        return self._series[index % len(self._series)]


def _draw_text(cr, x, y, text, colour, size=9.5, widget=None):
    """Centre `text` on x, baseline-ish at y, using the theme font family."""
    layout = PangoCairo.create_layout(cr)
    desc = Pango.FontDescription("Sans")
    desc.set_size(int(size * Pango.SCALE))
    layout.set_font_description(desc)
    layout.set_text(text, -1)
    w, h = layout.get_pixel_size()
    cr.set_source_rgb(*colour)
    cr.move_to(x - w / 2.0, y - h)
    PangoCairo.show_layout(cr, layout)
    return h


class _Geometry:
    """Translates between pixels and positions in the sample history.

    Tank Mode stores everything it has damaged in sample space so the damage
    travels with the data; this is the only thing that knows how that maps
    onto the widget, and it is asked afresh every frame because a resize
    changes the answer.
    """

    def __init__(self, graph):
        self.graph = graph

    def _dx(self):
        alloc = self.graph.get_allocation()
        n = max(2, self.graph.capacity)
        return max(1e-6, alloc.width / (n - 1))

    def to_x(self, index):
        return index * self._dx()

    def to_index(self, x):
        return x / self._dx()

    def samples_for(self, pixels):
        return pixels / self._dx()

    def trace_y(self, index):
        """Pixel y of the topmost series at a sample position."""
        alloc = self.graph.get_allocation()
        h = max(1, alloc.height)
        outline = self.graph._outline
        if not outline:
            return h - 1.0
        i = max(0, min(len(outline) - 1, int(round(index))))
        return h - outline[i] * (h - 1)


class HistoryGraph(Gtk.DrawingArea):
    """A scrolling history plot - the 'CPU Usage History' box.

    With `stacked` the series are drawn one on top of the other, lowest index
    at the bottom: that is how Task Manager shows kernel time (red) beneath
    user time (green).
    """

    def __init__(self, capacity=120, series=1, height=90, classic=False,
                 grid=(12, 6), stacked=False, classic_series=None):
        super().__init__()
        self.capacity = capacity
        self.classic = classic
        self.stacked = stacked
        self.grid_cells = grid
        # Optional per-series colour override, used for the red/green split.
        self.classic_series = classic_series
        self._data = [deque([0.0] * capacity, maxlen=capacity) for _ in range(series)]
        self._phase = 0
        self._outline = []              # topmost series, for Tank Mode
        self._battlefield = None
        self.set_size_request(-1, height)
        self.connect("draw", self._on_draw)

    # -- tank mode ----------------------------------------------------------
    def set_tank_mode(self, enabled: bool):
        """Options > Tank Mode. Clicking the plate puts a round through it."""
        if enabled and self._battlefield is None:
            self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
            self._battlefield = Battlefield(self, geometry=_Geometry(self))
            self._press = self.connect("button-press-event", self._on_shoot)
            self.set_tooltip_text("Tank Mode: click to fire.")
        elif not enabled and self._battlefield is not None:
            self._battlefield.clear()
            self.disconnect(self._press)
            self._battlefield = None
            self.set_tooltip_text(None)
            self.queue_draw()

    def _on_shoot(self, _widget, event):
        if event.button != Gdk.BUTTON_PRIMARY or self._battlefield is None:
            return False
        alloc = self.get_allocation()
        self._battlefield.fire_at(event.x, event.y, alloc.width, alloc.height)
        return True

    @staticmethod
    def _intact_runs(n, holes):
        """Index ranges of the trace that are still there.

        A hole is a stretch of the history that was blown away; the line is
        drawn as one run per surviving stretch, so it ends at the lip of the
        crater and picks up again on the far side.
        """
        if not holes:
            return [(0, n - 1)]
        gone = bytearray(n)
        for centre, half in holes:
            lo = max(0, int(math.floor(centre - half)))
            hi = min(n - 1, int(math.ceil(centre + half)))
            for i in range(lo, hi + 1):
                gone[i] = 1
        runs, start = [], None
        for i in range(n):
            if gone[i]:
                if start is not None:
                    runs.append((start, i - 1))
                    start = None
            elif start is None:
                start = i
        if start is not None:
            runs.append((start, n - 1))
        return [r for r in runs if r[1] > r[0]]

    # -- data ---------------------------------------------------------------
    def push(self, *values):
        for i, value in enumerate(values):
            if i < len(self._data):
                self._data[i].append(max(0.0, min(1.0, float(value))))
        self._phase = (self._phase + 1) % max(1, self.capacity)
        if self._battlefield is not None:
            # Damage belongs to the samples it was done to, so it moves left
            # with them and leaves the plate when they do.
            self._battlefield.scroll()
        self.queue_draw()

    def set_series(self, count, classic_series=None, stacked=None):
        if stacked is not None:
            self.stacked = stacked
        self.classic_series = classic_series
        while len(self._data) < count:
            self._data.append(deque([0.0] * self.capacity, maxlen=self.capacity))
        while len(self._data) > count:
            self._data.pop()
        self.clear()

    def clear(self):
        for d in self._data:
            d.clear()
            d.extend([0.0] * self.capacity)
        self.queue_draw()

    def latest(self, index=0):
        return self._data[index][-1] if self._data and self._data[index] else 0.0

    def _colour(self, pal, index):
        if pal.classic and self.classic_series:
            return self.classic_series[index % len(self.classic_series)]
        return pal.series(index)

    def series_hex(self, index):
        """The colour this series is drawn in right now, for legend swatches."""
        r, g, b = self._colour(Palette(self, self.classic), index)
        return "#%02X%02X%02X" % (int(r * 255), int(g * 255), int(b * 255))

    # -- drawing ------------------------------------------------------------
    def _on_draw(self, widget, cr):
        alloc = widget.get_allocation()
        w, h = alloc.width, alloc.height
        pal = Palette(widget, self.classic)

        cr.set_source_rgb(*pal.bg)
        cr.rectangle(0, 0, w, h)
        cr.fill()

        cols, rows = self.grid_cells
        cr.set_line_width(1.0)
        cr.set_source_rgb(*pal.grid)
        step_x = w / cols
        # The grid drifts left with the data, as it did in the original.
        offset = (self._phase % max(1, int(self.capacity / cols))) * (w / self.capacity)
        x = -offset
        while x < w:
            cr.move_to(round(x) + 0.5, 0)
            cr.line_to(round(x) + 0.5, h)
            x += step_x
        for i in range(1, rows):
            y = round(h * i / rows) + 0.5
            cr.move_to(0, y)
            cr.line_to(w, y)
        cr.stroke()

        n = self.capacity
        dx = w / max(1, n - 1)
        solid = pal.classic

        # Work out the outline each series is filled to, then paint from the
        # tallest down so nothing is buried by an opaque fill above it.
        outlines = []
        if self.stacked:
            running = [0.0] * n
            for data in self._data:
                values = self._padded(data, n)
                running = [min(1.0, a + b) for a, b in zip(running, values)]
                outlines.append(list(running))
            order = list(range(len(outlines) - 1, -1, -1))
        else:
            outlines = [self._padded(d, n) for d in self._data]
            order = list(range(len(outlines) - 1, -1, -1))
            if solid and len(outlines) > 1:
                order.sort(key=lambda i: outlines[i][-1], reverse=True)

        holes = self._battlefield.holes() if self._battlefield is not None else []
        runs = self._intact_runs(n, holes)

        for index in order:
            values = outlines[index]
            colour = self._colour(pal, index)

            def y_at(i, _v=values):
                return h - _v[i] * (h - 1)

            # The XP graph was a line on a plate, not a block of colour, so
            # classic mode strokes the trace and leaves the plate showing
            # through underneath. Theme mode keeps a soft wash, which is
            # what makes it read as the modern alternative rather than as
            # the same drawing in different colours.
            if not solid:
                for start, end in runs:
                    cr.new_path()
                    cr.move_to(start * dx, h)
                    for i in range(start, end + 1):
                        cr.line_to(i * dx, y_at(i))
                    cr.line_to(end * dx, h)
                    cr.close_path()
                    cr.set_source_rgba(*colour, pal.fill_alpha)
                    cr.fill()

            cr.set_source_rgb(*colour)
            cr.set_line_width(pal.line_width)
            cr.set_line_join(cairo.LINE_JOIN_ROUND)
            for start, end in runs:
                cr.new_path()
                cr.move_to(start * dx, y_at(start))
                for i in range(start + 1, end + 1):
                    cr.line_to(i * dx, y_at(i))
                cr.stroke()

            # Torn ends where the line was cut, so a break reads as damage
            # rather than as missing data.
            if holes:
                cr.set_source_rgb(*CHAR)
                cr.set_line_width(max(1.0, pal.line_width))
                for start, end in runs:
                    for edge, direction in ((start, -1), (end, 1)):
                        if (edge == 0 and direction < 0) or (edge == n - 1
                                                             and direction > 0):
                            continue
                        ex, ey = edge * dx, y_at(edge)
                        cr.new_path()
                        cr.move_to(ex, ey)
                        cr.line_to(ex + direction * dx * 0.9, ey + 3.5)
                        cr.stroke()

        # Keep the highest line for Tank Mode to set its fires on.
        if outlines:
            self._outline = [max(values[i] for values in outlines)
                             for i in range(n)]

        if self._battlefield is not None:
            # Craters go over the finished graph so the hole is punched
            # through the data, then everything alight goes over the lot.
            self._battlefield.draw_damage(cr, w, h)
            self._battlefield.draw_fire(cr, w, h)

        cr.set_source_rgb(*pal.frame)
        cr.set_line_width(1.0)
        cr.rectangle(0.5, 0.5, w - 1, h - 1)
        cr.stroke()
        return False

    @staticmethod
    def _padded(data, n):
        values = list(data)
        if len(values) < n:
            return [0.0] * (n - len(values)) + values
        return values


class Meter(Gtk.DrawingArea):
    """The segmented 'CPU Usage' bar.

    Every segment is painted: dark green when unlit, bright green for user
    time, red for the kernel share at the bottom. The reading is printed
    inside the plate below the bar, exactly as XP did it.
    """

    def __init__(self, classic=False, width=44, height=68, caption="",
                 show_caption=True):
        super().__init__()
        self.classic = classic
        self.show_caption = show_caption
        self._value = 0.0
        self._kernel = 0.0
        self._caption = caption
        self.set_size_request(width, height)
        self.connect("draw", self._on_draw)

    def set_value(self, value, kernel=0.0, caption=None):
        value = max(0.0, min(1.0, float(value)))
        kernel = max(0.0, min(value, float(kernel)))
        if caption is None:
            caption = f"{value * 100:.0f}%"
        if (abs(value - self._value) > 0.0005
                or abs(kernel - self._kernel) > 0.0005
                or caption != self._caption):
            self._value, self._kernel, self._caption = value, kernel, caption
            self.queue_draw()

    def get_value(self):
        return self._value

    def _on_draw(self, widget, cr):
        alloc = widget.get_allocation()
        w, h = alloc.width, alloc.height
        pal = Palette(widget, self.classic)

        cr.set_source_rgb(*pal.bg)
        cr.rectangle(0, 0, w, h)
        cr.fill()

        text_h = 15 if self.show_caption else 0
        top, bottom = 3, h - text_h - 3
        bar_h = max(1, bottom - top)
        bar_x, bar_w = 3, max(1, w - 6)

        step = SEGMENT + SEGMENT_GAP
        count = max(1, bar_h // step)
        lit = int(round(count * self._value))
        red = int(round(count * self._kernel))
        user_colour = self._colour(pal, 0)
        kernel_colour = XP_RED if pal.classic else self._colour(pal, 2)

        for i in range(count):
            y = bottom - (i + 1) * step + SEGMENT_GAP
            if i < red:
                cr.set_source_rgb(*kernel_colour)
            elif i < lit:
                cr.set_source_rgb(*user_colour)
            else:
                cr.set_source_rgb(*pal.unlit)
            cr.rectangle(bar_x, y, bar_w, SEGMENT)
            cr.fill()

        if self.show_caption and self._caption:
            _draw_text(cr, w / 2.0, h - 2, self._caption,
                       pal.text if pal.classic else pal.text, 9.0)

        cr.set_source_rgb(*pal.frame)
        cr.set_line_width(1.0)
        cr.rectangle(0.5, 0.5, w - 1, h - 1)
        cr.stroke()
        return False

    def _colour(self, pal, index):
        return pal.series(0) if index == 0 else pal.series(index)


class MeterGrid(Gtk.Grid):
    """One segmented meter per logical CPU, the way a multiprocessor XP box
    showed a separate bar for every processor."""

    def __init__(self, count, classic=False, width=44, height=68):
        super().__init__(column_spacing=4, row_spacing=4)
        cols, _rows = grid_shape(count)
        self.meters = []
        for i in range(count):
            meter = Meter(classic=classic, width=width, height=height)
            self.attach(meter, i % cols, i // cols, 1, 1)
            self.meters.append(meter)

    def update(self, values, kernels=None):
        for i, meter in enumerate(self.meters):
            value = values[i] / 100.0 if i < len(values) else 0.0
            kernel = (kernels[i] / 100.0) if kernels and i < len(kernels) else 0.0
            meter.set_value(value, kernel)

    def set_classic(self, classic):
        for meter in self.meters:
            meter.classic = classic
            meter.queue_draw()


class CoreGrid(Gtk.Grid):
    """One history graph per logical CPU - View > CPU History > One Graph Per
    CPU in the original."""

    def __init__(self, ncores, classic=False, classic_series=None, stacked=False):
        super().__init__(column_spacing=4, row_spacing=4,
                         column_homogeneous=True, row_homogeneous=True)
        cols, _rows = grid_shape(ncores)
        self.graphs = []
        for i in range(ncores):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            graph = HistoryGraph(capacity=90, height=52, classic=classic,
                                 grid=(6, 4), series=2 if stacked else 1,
                                 stacked=stacked, classic_series=classic_series)
            label = Gtk.Label(label=f"CPU {i}", xalign=0.0)
            label.get_style_context().add_class("dim-label")
            label.set_name("core-label")
            box.pack_start(graph, True, True, 0)
            box.pack_start(label, False, False, 0)
            self.attach(box, i % cols, i // cols, 1, 1)
            self.graphs.append(graph)

    def push(self, values, kernels=None):
        for i, graph in enumerate(self.graphs):
            total = values[i] / 100.0 if i < len(values) else 0.0
            if kernels is not None:
                kernel = kernels[i] / 100.0 if i < len(kernels) else 0.0
                graph.push(kernel, max(0.0, total - kernel))
            else:
                graph.push(total)

    def set_series(self, count, classic_series=None, stacked=None):
        for graph in self.graphs:
            graph.set_series(count, classic_series, stacked)

    def set_classic(self, classic):
        for graph in self.graphs:
            graph.classic = classic
            graph.queue_draw()
