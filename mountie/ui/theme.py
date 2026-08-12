"""Desktop appearance integration and themed icon helpers."""

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib
from PyQt5 import QtCore, QtGui, QtWidgets

from mountie.settings import THEMES, THEME_SYSTEM
from mountie.ui.visuals import APP_STYLESHEET, COSMIC_TOKENS, PALETTE_COLORS


def is_dark_palette(widget):
    return widget.palette().color(QtGui.QPalette.Window).lightness() < 128


def cosmic_tokens(widget):
    return COSMIC_TOKENS["dark" if is_dark_palette(widget) else "light"]


APPEARANCE_NS = "org.freedesktop.appearance"
COLOR_SCHEME_DARK = 1
COLOR_SCHEME_LIGHT = 2

# Neutral greys in the same range COSMIC uses, so an explicit Light/Dark
# choice still sits comfortably next to native COSMIC windows. The highlight
# is only a fallback - the portal's real accent color wins when available.


def portal_proxy():
    try:
        return Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SESSION, Gio.DBusProxyFlags.NONE, None,
            "org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
            "org.freedesktop.portal.Settings", None,
        )
    except GLib.Error:
        return None


def portal_read(proxy, key):
    if proxy is None:
        return None
    try:
        result = proxy.call_sync(
            "Read", GLib.Variant("(ss)", (APPEARANCE_NS, key)),
            Gio.DBusCallFlags.NONE, -1, None,
        )
    except GLib.Error:
        # No portal, or a portal too old to know this key. Either way the
        # caller falls back rather than failing.
        return None
    value = result.unpack()[0]
    while isinstance(value, GLib.Variant):  # Read() wraps its result in a variant
        value = value.unpack()
    return value


def system_color_scheme(proxy):
    """'dark', 'light', or None when the desktop states no preference."""
    value = portal_read(proxy, "color-scheme")
    if value == COLOR_SCHEME_DARK:
        return "dark"
    if value == COLOR_SCHEME_LIGHT:
        return "light"
    return None


def system_accent(proxy):
    value = portal_read(proxy, "accent-color")
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        return None
    return QtGui.QColor.fromRgbF(*[min(1.0, max(0.0, float(c))) for c in value])


def build_palette(mode, accent=None):
    c = PALETTE_COLORS[mode]
    window = QtGui.QColor(c["window"])
    base = QtGui.QColor(c["base"])
    text = QtGui.QColor(c["text"])
    mid = QtGui.QColor(c["mid"])
    button = QtGui.QColor(c["button"])
    disabled = QtGui.QColor(c["disabled"])
    highlight = accent or QtGui.QColor(c["highlight"])
    # COSMIC's accent colors are light enough that white-on-accent is often
    # unreadable, so pick the contrasting foreground from the accent itself.
    highlight_text = QtGui.QColor("#101010" if highlight.lightness() > 140 else "#ffffff")

    pal = QtGui.QPalette()
    for role, color in (
        (QtGui.QPalette.Window, window),
        (QtGui.QPalette.WindowText, text),
        (QtGui.QPalette.Base, base),
        (QtGui.QPalette.AlternateBase, QtGui.QColor(c["alt_base"])),
        (QtGui.QPalette.Text, text),
        (QtGui.QPalette.Button, button),
        (QtGui.QPalette.ButtonText, text),
        (QtGui.QPalette.Mid, mid),
        (QtGui.QPalette.Midlight, mid.lighter(115)),
        (QtGui.QPalette.Dark, window.darker(120)),
        (QtGui.QPalette.Shadow, window.darker(160)),
        (QtGui.QPalette.ToolTipBase, base),
        (QtGui.QPalette.ToolTipText, text),
        (QtGui.QPalette.PlaceholderText, disabled),
        (QtGui.QPalette.Highlight, highlight),
        (QtGui.QPalette.HighlightedText, highlight_text),
    ):
        pal.setColor(role, color)

    for role in (QtGui.QPalette.WindowText, QtGui.QPalette.Text,
                 QtGui.QPalette.ButtonText, QtGui.QPalette.HighlightedText):
        pal.setColor(QtGui.QPalette.Disabled, role, disabled)
    return pal


class ThemeManager(QtCore.QObject):
    """Owns the application palette and keeps it in step with the desktop."""

    changed = QtCore.pyqtSignal()

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.proxy = portal_proxy()
        # What Qt gave us at startup - restored when the desktop states no
        # preference, so a working qt5ct setup isn't overridden for nothing.
        self.fallback_palette = app.palette()
        self.mode = THEME_SYSTEM
        if self.proxy is not None:
            self.proxy.connect("g-signal", self._on_portal_signal)

    def _on_portal_signal(self, proxy, sender, signal, params):
        if signal != "SettingChanged":
            return
        unpacked = params.unpack()
        if len(unpacked) < 2:
            return
        namespace, key = unpacked[0], unpacked[1]
        if namespace != APPEARANCE_NS or key not in ("color-scheme", "accent-color"):
            return
        # An accent change matters in every mode; a scheme change only when
        # we're the one following the system.
        if key == "accent-color" or self.mode == THEME_SYSTEM:
            self.apply()

    def set_mode(self, mode):
        self.mode = mode if mode in dict(THEMES) else THEME_SYSTEM
        self.apply()

    def effective_mode(self):
        if self.mode != THEME_SYSTEM:
            return self.mode
        return system_color_scheme(self.proxy)

    def apply(self):
        mode = self.effective_mode()
        if mode is None:
            self.app.setPalette(self.fallback_palette)
        else:
            self.app.setPalette(build_palette(mode, system_accent(self.proxy)))
        # Re-setting the stylesheet forces Qt to re-polish every widget, which
        # is what makes palette(...) references in it pick up the new colors.
        self.app.setStyleSheet(APP_STYLESHEET)
        self.changed.emit()


def themed_icon(*names):
    for name in names:
        icon = QtGui.QIcon.fromTheme(name)
        if not icon.isNull():
            return icon
    return QtGui.QIcon()


def tinted_icon(color, *names):
    """Recolors a monochrome themed icon to `color`.

    Icon themes are built for one background: the desktop's own. Here that's
    breeze-dark, whose glyphs are near-white and so disappear entirely
    against the Light theme. Since every icon requested here is symbolic,
    repainting it in the current text color is safe and keeps the buttons
    legible in whichever theme the user picked.
    """
    icon = themed_icon(*names)
    if icon.isNull():
        return icon
    out = QtGui.QIcon()
    for size in (16, 22, 32, 48):  # cover HiDPI without relying on availableSizes()
        source = icon.pixmap(QtCore.QSize(size, size))
        if source.isNull():
            continue
        tinted = QtGui.QPixmap(source.size())
        tinted.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(tinted)
        painter.drawPixmap(0, 0, source)
        painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceIn)
        painter.fillRect(tinted.rect(), color)
        painter.end()
        out.addPixmap(tinted)
    return out


def icon_button(icon_names, tooltip):
    btn = QtWidgets.QToolButton()
    btn.setIconSize(QtCore.QSize(16, 16))
    btn.setToolTip(tooltip)
    btn.setAutoRaise(True)
    btn.setCursor(QtCore.Qt.PointingHandCursor)
    btn.setProperty("class", "iconButton")
    # Remembered so the icon can be repainted when the theme changes.
    btn.setProperty("iconNames", list(icon_names))
    retint_icon_button(btn)
    return btn


def appearance_icon(color):
    """Half-filled circle: the conventional light/dark glyph.

    Drawn rather than pulled from the icon theme because the desktop's
    "appearance" icons are filled artwork - recoloring one collapses it into
    a featureless blob.
    """
    icon = QtGui.QIcon()
    for size in (16, 22, 32, 48):
        pixmap = QtGui.QPixmap(size, size)
        pixmap.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(pixmap)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        inset = max(1.5, size * 0.12)
        rect = QtCore.QRectF(inset, inset, size - 2 * inset, size - 2 * inset)

        painter.setPen(QtGui.QPen(color, max(1.2, size * 0.085)))
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.drawEllipse(rect)

        half = QtGui.QPainterPath()
        half.moveTo(rect.center().x(), rect.top())
        half.arcTo(rect, 90, -180)  # top -> right -> bottom
        half.closeSubpath()
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(color)
        painter.drawPath(half)
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def retint_icon_button(btn):
    color = btn.palette().color(QtGui.QPalette.WindowText)
    painter_fn = getattr(btn, "icon_painter", None)
    if painter_fn is not None:
        btn.setIcon(painter_fn(color))
        return
    names = btn.property("iconNames") or []
    if names:
        btn.setIcon(tinted_icon(color, *names))
