"""Application entry point."""

from __future__ import annotations

import sys

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gio, GLib  # noqa: E402

from . import APP_ID, APP_NAME, __version__
from .backend.config import Config
from .ui.window import MainWindow


class TanksManager(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.cfg = Config()
        self.window = None
        self.add_main_option("tab", ord("t"), GLib.OptionFlags.NONE,
                             GLib.OptionArg.STRING, "Open a tab by name "
                             "(applications, processes, performance, "
                             "users, services)", "NAME")
        self.add_main_option("tank-mode", 0, GLib.OptionFlags.NONE,
                             GLib.OptionArg.NONE,
                             "Start with Tank Mode armed (this session only)",
                             None)
        self.add_main_option("version", ord("v"), GLib.OptionFlags.NONE,
                             GLib.OptionArg.NONE, "Show the version and exit", None)

    TAB_NAMES = ["applications", "processes", "performance",
                 "users", "services"]

    def do_command_line(self, command_line):
        options = command_line.get_options_dict().end().unpack()
        if options.get("version"):
            command_line.print_literal(f"{APP_NAME} {__version__}\n")
            return 0
        if options.get("tank-mode"):
            # A session setting, so the flag is the only way to ask for it
            # up front; it is still never written back to the config file.
            self.cfg["tank_mode"] = True
            if self.window is not None:
                self.window.set_tank_mode(True)
        tab = options.get("tab")
        if tab:
            tab = tab.lower()
            if tab not in self.TAB_NAMES:
                command_line.printerr_literal(
                    f"Unknown tab {tab!r}; expected one of "
                    f"{', '.join(self.TAB_NAMES)}\n")
                return 1
            self.cfg["tab"] = self.TAB_NAMES.index(tab)
        self.activate()
        return 0

    def do_activate(self):
        if self.window is None:
            self.window = MainWindow(self, self.cfg)
        else:
            self.window.notebook.set_current_page(int(self.cfg["tab"]))
        self.window.present()

    def do_shutdown(self):
        if self.window is not None:
            self.window.sampler.stop()
            self.window.save_state()
        Gtk.Application.do_shutdown(self)


def main(argv=None):
    return TanksManager().run(argv if argv is not None else sys.argv)


if __name__ == "__main__":
    sys.exit(main())
