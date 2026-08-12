"""Best-effort desktop lock and suspend event monitoring."""

import logging

from gi.repository import Gio, GLib
from PyQt5 import QtCore


logger = logging.getLogger(__name__)


class SessionMonitor(QtCore.QObject):
    locked = QtCore.pyqtSignal()
    suspending = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._connections = []
        self._subscribe_session_lock()
        self._subscribe_suspend()

    def _subscribe_session_lock(self):
        try:
            connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            connection.signal_subscribe(
                None,
                "org.freedesktop.ScreenSaver",
                "ActiveChanged",
                None,
                None,
                Gio.DBusSignalFlags.NONE,
                self._on_lock_signal,
            )
            self._connections.append(connection)
        except GLib.Error as error:
            logger.info("Screen-lock monitoring unavailable: %s", error.message)

    def _subscribe_suspend(self):
        try:
            connection = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            connection.signal_subscribe(
                "org.freedesktop.login1",
                "org.freedesktop.login1.Manager",
                "PrepareForSleep",
                "/org/freedesktop/login1",
                None,
                Gio.DBusSignalFlags.NONE,
                self._on_sleep_signal,
            )
            self._connections.append(connection)
        except GLib.Error as error:
            logger.info("Suspend monitoring unavailable: %s", error.message)

    def _on_lock_signal(self, _connection, _sender, _path, _interface, _signal, params):
        if params.unpack()[0]:
            self.locked.emit()

    def _on_sleep_signal(self, _connection, _sender, _path, _interface, _signal, params):
        if params.unpack()[0]:
            self.suspending.emit()
