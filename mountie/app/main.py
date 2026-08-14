"""Create and run the Mountie desktop application."""
import sys

import gi
gi.require_version("Gio", "2.0")
from gi.repository import GLib

from PyQt5 import QtCore, QtGui, QtWidgets

from mountie.logging_setup import configure_logging
from mountie.settings import APP_ID
from mountie.app.theme import ThemeManager, initialize_icon_theme
from mountie.app.window import MainWindow


def pump_glib(_=None):
    context = GLib.MainContext.default()
    while context.iteration(False):
        pass


def start_glib_pump(qt_app):
    timer = QtCore.QTimer()
    timer.timeout.connect(pump_glib)
    timer.start(50)
    qt_app._glib_pump_timer = timer

def main():
    configure_logging()
    # Both set before the QApplication so they're in place when the platform
    # plugin creates the first window.
    #
    # setDesktopFileName is what makes the dock show the right icon. On
    # Wayland the compositor identifies a window by its app_id and looks up
    # the matching .desktop file; without this Qt reports a generic app_id,
    # no .desktop file matches, and the dock falls back to a placeholder
    # regardless of what setWindowIcon says. The name must equal the desktop
    # file's basename, hence APP_ID.
    QtWidgets.QApplication.setDesktopFileName(APP_ID)
    QtWidgets.QApplication.setApplicationName("Mountie")

    app = QtWidgets.QApplication(sys.argv)
    initialize_icon_theme()
    # Still worth setting: it's what X11/XWayland and the window's own title
    # bar use, where app_id doesn't apply.
    app.setWindowIcon(QtGui.QIcon.fromTheme(APP_ID))
    # The pump has to be running before ThemeManager subscribes to the portal,
    # or SettingChanged signals would never be delivered.
    start_glib_pump(app)
    win = MainWindow(ThemeManager(app))
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
