#!/usr/bin/env python3
"""Build the whole window once, let it settle, and shut it down.

Run under Xvfb in CI.  This is deliberately not a pytest module: it needs a
display and a GTK main loop, so it stays a standalone script that exits
non-zero if anything raises.  It exists because the bugs that actually got
shipped here were construction-order bugs - a Gtk.Stack child selected
before it was shown, an option applied before the cards existed - and none
of them is reachable from a parser test.
"""

import os
import sys
import tempfile
import time

# Run as a script, sys.path[0] is tests/ rather than the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Point the config at a scratch directory before anything imports it: this
# script drives the real window, and saving its state over whatever the
# person running it had configured would be a rude way to run a test.
_scratch = tempfile.mkdtemp(prefix="tanksmanager-smoke-")
os.environ["XDG_CONFIG_HOME"] = _scratch

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib  # noqa: E402

from tanksmanager.backend.config import Config  # noqa: E402
from tanksmanager.ui.window import MainWindow  # noqa: E402

failures = []

# PyGObject routes an exception raised inside a signal handler through
# sys.excepthook and carries on, so a draw handler that blows up every frame
# still lets the process exit zero. Catching them here is the difference
# between this script testing something and just printing tracebacks.
_default_excepthook = sys.excepthook


def _record(exc_type, exc, tb):
    failures.append(exc)
    _default_excepthook(exc_type, exc, tb)


sys.excepthook = _record


def main():
    app = Gtk.Application(application_id="de.synthelicz.TanksManagerSmoke")

    def on_activate(application):
        window = MainWindow(application, Config())

        def exercise():
            try:
                # Visit every tab: several of them only build their contents
                # the first time they are shown.
                for page in range(window.notebook.get_n_pages()):
                    window.notebook.set_current_page(page)
                    while Gtk.events_pending():
                        Gtk.main_iteration()

                # A snapshot has to survive the trip through the UI.
                window.sampler.refresh_now()
                for cid in ("gpu", "unit", "cmd", "read", "write"):
                    window.proc_tab.apply_visible_columns(
                        window.proc_tab.visible_columns() + [cid])
                window.proc_tab.export_rows()
                window.perf_tab.apply_options()

                # Tank Mode: arm every graph, fire a round through one, and
                # stand down again. This is the path that adds an event
                # handler and a frame timer to a live widget, so it is worth
                # walking end to end rather than trusting the wiring.
                from tanksmanager.ui.performance import _history_graphs

                # Nothing on a notebook page that is not current gets mapped,
                # and an unmapped widget is never drawn - so bring the
                # Performance tab back to the front before shooting at it.
                window.notebook.set_current_page(2)
                while Gtk.events_pending():
                    Gtk.main_iteration()

                window.perf_tab.set_tank_mode(True)
                graphs = [g for pane in window.perf_tab.panes.values()
                          for g in _history_graphs(pane)]
                assert graphs, "Tank Mode found no graphs to arm"
                assert all(g._battlefield is not None for g in graphs)
                # A round is not fired, flown or landed by a widget that
                # never gets a draw call, and a card holds both the single
                # history graph and the per-core grid with only one of them
                # mapped at a time. Pick one that is actually on screen.
                visible = window.perf_tab.stack.get_visible_child()
                on_screen = [g for g in _history_graphs(visible)
                             if g.get_mapped()]
                assert on_screen, "no graph on the visible card is mapped"
                target = on_screen[0]
                alloc = target.get_allocation()
                target._battlefield.fire_at(alloc.width * 0.6,
                                            alloc.height * 0.4,
                                            alloc.width, alloc.height)
                # The shell needs its flight time before it lands, and the
                # widget has to actually redraw for the impact to register,
                # so pump the loop against the clock rather than a count.
                deadline = time.monotonic() + 1.5
                while time.monotonic() < deadline:
                    while Gtk.events_pending():
                        Gtk.main_iteration()
                    time.sleep(0.01)

                # The round should have left damage anchored to the samples
                # it destroyed, and that damage should walk left as new
                # readings arrive rather than sitting still on the plate.
                field = target._battlefield
                assert field.craters, "the round left no crater"
                before = field.craters[0].index
                target.push(0.5)
                target.push(0.5)
                assert field.craters[0].index == before - 2, (
                    "damage did not scroll with the data")

                # And the trace has to be genuinely broken across it.
                holes = field.holes()
                assert holes, "a crater produced no hole in the line"
                runs = target._intact_runs(target.capacity, holes)
                assert runs, "the whole trace was destroyed"
                assert sum(end - start for start, end in runs) < target.capacity - 1, (
                    "the trace was drawn straight through the hole")

                # The time axis: the newest half at full resolution, older
                # readings folded into the left half, and an inverse that
                # actually inverts - Tank Mode aims through it.
                width = target.get_allocation().width
                lin = target.linear_samples()
                n = target.capacity
                xs = target.positions(width)
                assert all(xs[i] <= xs[i + 1] for i in range(n - 1)), (
                    "the time axis is not monotonic")
                assert abs(xs[-1] - width) < 0.01, "the newest sample is not at the edge"
                assert xs[0] < width * 0.02, "the oldest sample is not near the left edge"
                middle = target.sample_x((n - 1) - lin, width)
                assert abs(middle - width / 2.0) < 0.01, (
                    "the linear half does not end at the middle")
                # Even spacing in the linear half, tightening beyond it.
                near = xs[-1] - xs[-2]
                mid = target.sample_x(n - 1 - 10, width) - target.sample_x(n - 1 - 11, width)
                far = target.sample_x(10, width) - target.sample_x(9, width)
                assert abs(near - mid) < 0.01, "the recent half is not linear"
                assert far < near / 4.0, "older samples are not compressed"
                for probe in (1.0, width * 0.25, width * 0.5, width * 0.9, width - 1.0):
                    back = target.sample_x(target.x_sample(probe, width), width)
                    assert abs(back - probe) < 0.5, f"axis does not invert at {probe}"

                window.perf_tab.set_tank_mode(False)
                assert all(g._battlefield is None for g in graphs)
            except Exception as exc:            # noqa: BLE001 - report it all
                import traceback
                traceback.print_exc()
                failures.append(exc)
            finally:
                window.destroy()
                application.quit()
            return GLib.SOURCE_REMOVE

        # Give the sampler thread a moment to deliver a real snapshot first.
        GLib.timeout_add(1500, exercise)

    app.connect("activate", on_activate)
    app.run([])
    if failures:
        print(f"GUI smoke test FAILED: {len(failures)} unhandled "
              f"exception(s), first was {failures[0]!r}", file=sys.stderr)
        return 1
    print("GUI smoke test passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
