"""Reliable fixed-duration disconnect scheduling.

This intentionally measures connected duration, not filesystem inactivity:
applications access GVfs mounts without going through Mountie, so Mountie
cannot authoritatively observe reads and writes.
"""

from PyQt5 import QtCore


class DisconnectScheduler(QtCore.QObject):
    due = QtCore.pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._timers = {}

    def schedule(self, share_id, minutes):
        self.cancel(share_id)
        try:
            milliseconds = int(minutes) * 60 * 1000
        except (TypeError, ValueError):
            return
        if milliseconds <= 0:
            return
        timer = QtCore.QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda sid=share_id: self._fire(sid))
        timer.start(milliseconds)
        self._timers[share_id] = timer

    def cancel(self, share_id):
        timer = self._timers.pop(share_id, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()

    def remaining_seconds(self, share_id):
        timer = self._timers.get(share_id)
        return max(0, timer.remainingTime() // 1000) if timer else None

    def _fire(self, share_id):
        self._timers.pop(share_id, None)
        self.due.emit(share_id)
