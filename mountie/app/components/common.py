"""Small controls shared by Mountie's application widgets."""

from PyQt5 import QtCore, QtGui, QtWidgets

from mountie.app.theme import cosmic_tokens
from mountie.app.visuals import STATUS_TOKEN_KEY


class ToggleSwitch(QtWidgets.QAbstractButton):
    """A small animated pill-style on/off switch, used in place of a checkbox."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(42, 24)
        self._offset = 3.0
        self._anim = QtCore.QPropertyAnimation(self, b"offset", self)
        self._anim.setDuration(120)
        self.toggled.connect(self._animate)

    def _animate(self, checked):
        self._anim.stop()
        self._anim.setStartValue(self._offset)
        self._anim.setEndValue(21.0 if checked else 3.0)
        self._anim.start()

    def getOffset(self):
        return self._offset

    def setOffset(self, value):
        self._offset = value
        self.update()

    offset = QtCore.pyqtProperty(float, getOffset, setOffset)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)

        palette = self.palette()
        if self.isChecked():
            track_color = palette.color(QtGui.QPalette.Highlight)
        elif not self.isEnabled():
            track_color = palette.color(QtGui.QPalette.Button).lighter(105)
        else:
            track_color = palette.color(QtGui.QPalette.Mid)

        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(track_color)
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)

        painter.setBrush(QtGui.QColor("white"))
        thumb_d = rect.height() - 6
        painter.drawEllipse(QtCore.QRectF(self._offset, 4, thumb_d, thumb_d))




class StatusBadge(QtWidgets.QLabel):
    def __init__(self, text=""):
        super().__init__()
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.set_status(text)

    def set_status(self, text):
        self.setText(text)
        r, g, b = cosmic_tokens(self)[STATUS_TOKEN_KEY.get(text, "muted")]
        self.setStyleSheet(
            f"QLabel {{ color: rgb({r},{g},{b}); background: rgba({r},{g},{b},40); "
            f"border-radius: 8px; padding: 2px 10px; font-size: 11px; font-weight: 600; }}"
        )
