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
                window.perf_tab.set_tank_mode(True)
                graphs = [g for pane in window.perf_tab.panes.values()
                          for g in _history_graphs(pane)]
                assert graphs, "Tank Mode found no graphs to arm"
                assert all(g._battlefield is not None for g in graphs)
                target = graphs[0]
                alloc = target.get_allocation()
                target._battlefield.fire_at(alloc.width * 0.6,
                                            alloc.height * 0.4,
                                            alloc.width, alloc.height)
                for _ in range(40):
                    while Gtk.events_pending():
                        Gtk.main_iteration()
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
