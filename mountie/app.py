#!/usr/bin/env python3
import os
import re
import sys
import json
import uuid
from pathlib import Path

import gi
gi.require_version("Gio", "2.0")
gi.require_version("Secret", "1")
from gi.repository import Gio, GLib, Secret

from PyQt5 import QtCore, QtGui, QtWidgets

CONFIG_DIR = Path.home() / ".config" / "mountie"
CONFIG_PATH = CONFIG_DIR / "config.json"

THEME_SYSTEM, THEME_LIGHT, THEME_DARK = "system", "light", "dark"
THEMES = [(THEME_SYSTEM, "System"), (THEME_LIGHT, "Light"), (THEME_DARK, "Dark")]

# Reverse-DNS ID for this app, used for the libsecret schema, the desktop
# entry, the icon name, and (eventually) the Flatpak ID. Change it in one
# place only. Pick an ID under a domain you actually control before
# submitting to a store, so instances of this app from different sources
# don't collide over the same stored secrets.
APP_ID = "io.github.HHuckleberry.Mountie"

SECRET_SCHEMA = Secret.Schema.new(
    APP_ID,
    Secret.SchemaFlags.NONE,
    {"share_id": Secret.SchemaAttributeType.STRING},
)


# ---------------------------------------------------------------- config ---

def load_config():
    if not CONFIG_PATH.exists():
        return {"shares": [], "theme": THEME_SYSTEM}
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    # Configs written before the theme setting existed are still valid.
    cfg.setdefault("shares", [])
    cfg.setdefault("theme", THEME_SYSTEM)
    cfg.setdefault("link_dir", DEFAULT_LINK_DIR)
    cfg.setdefault("links_enabled", True)
    return cfg


def save_config(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    tmp.replace(CONFIG_PATH)
    CONFIG_PATH.chmod(0o600)


def get_password(share_id):
    return Secret.password_lookup_sync(SECRET_SCHEMA, {"share_id": share_id}, None)


def set_password(share_id, password):
    Secret.password_store_sync(
        SECRET_SCHEMA, {"share_id": share_id}, Secret.COLLECTION_DEFAULT,
        "Mountie credentials", password, None,
    )


def clear_password(share_id):
    Secret.password_clear_sync(SECRET_SCHEMA, {"share_id": share_id}, None)


# GVfs mounts all of these through the same scheme://host/path URI shape,
# so one code path handles every protocol - only the scheme changes.
PROTOCOLS = [
    ("smb", "SMB / CIFS (Windows, Samba, Synology, etc.)"),
    ("afp", "AFP (older macOS file sharing)"),
    ("nfs", "NFS"),
    ("sftp", "SFTP / SSH"),
    ("ftp", "FTP"),
    ("ftps", "FTPS"),
    ("dav", "WebDAV"),
    ("davs", "WebDAV (secure)"),
]
DEFAULT_PROTOCOL = "smb"


def share_uri(cfg):
    protocol = cfg.get("protocol", DEFAULT_PROTOCOL)
    return f"{protocol}://{cfg['host']}/{cfg['share']}/"


# ------------------------------------------------------------ share links ---
# A GVfs mount lives at /run/user/<uid>/gvfs/<backend-encoded-name> - accurate,
# but not something anyone wants to type or bookmark. So each mounted share
# also gets a symlink under one predictable directory, giving it a short
# stable path like ~/Shares/home.
#
# That directory defaults somewhere the user already owns rather than /mnt,
# which is root-owned: symlinking there would need a privileged helper on
# every single mount, which is exactly the sudo dependency this app avoids.
# To use /mnt anyway, take ownership of a subdirectory once -
#
#     sudo mkdir -p /mnt/shares && sudo chown "$USER" /mnt/shares
#
# - then set "link_dir": "/mnt/shares" in config.json.

DEFAULT_LINK_DIR = "~/Shares"


def link_dir(cfg):
    return Path(cfg.get("link_dir", DEFAULT_LINK_DIR)).expanduser()


def link_name(share):
    """A filesystem-safe directory name derived from the share's label."""
    raw = (share.get("label") or share.get("share") or share["id"]).strip()
    return re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-.") or share["id"]


def link_path(cfg, share):
    return link_dir(cfg) / link_name(share)


def local_path(share):
    """Where the share is actually mounted, or None if it isn't.

    Asked of GVfs rather than assembled by hand - the encoding of that
    directory name is a backend implementation detail and differs per
    protocol.
    """
    try:
        mount = Gio.File.new_for_uri(share_uri(share)).find_enclosing_mount(None)
    except GLib.Error:
        return None
    return mount.get_default_location().get_path()


def update_link(cfg, share):
    """Points the share's link at its current mount, or clears it if the
    share isn't mounted. Returns the link path, or None if there isn't one."""
    if not cfg.get("links_enabled", True):
        return None
    target = local_path(share)
    if target is None:
        remove_link(cfg, share)
        return None

    path = link_path(cfg, share)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            if os.readlink(path) == target:
                return path
            path.unlink()
        elif path.exists():
            return None  # something real is there; never clobber it
        path.symlink_to(target)
        return path
    except OSError:
        # An unwritable link_dir shouldn't take the mount down with it.
        return None


def remove_link(cfg, share):
    path = link_path(cfg, share)
    try:
        if path.is_symlink():  # only ever unlinks symlinks, never real files
            path.unlink()
    except OSError:
        pass


def prune_links(cfg):
    """Clears links left dangling by a crash, or by an unmount done elsewhere."""
    directory = link_dir(cfg)
    if not directory.is_dir():
        return
    ours = {link_name(share) for share in cfg["shares"]}
    for entry in directory.iterdir():
        # entry.exists() follows the link, so this is "points at nothing".
        if entry.name in ours and entry.is_symlink() and not entry.exists():
            try:
                entry.unlink()
            except OSError:
                pass


# ------------------------------------------------------------- GLib loop ---
# gio's mount/unmount calls are async and their completion callbacks are
# delivered by whatever pumps the default GLib main context. Qt has its own
# event loop and never touches GLib's, so a QTimer drains GLib's default
# context on the same (Qt main) thread. A separate GLib thread was tried
# first and segfaulted from contention with GDBus sync calls made off the
# Qt thread - single-threaded pumping avoids that entirely.

def pump_glib(_=None):
    ctx = GLib.MainContext.default()
    while ctx.iteration(False):
        pass


def start_glib_pump(qt_app):
    timer = QtCore.QTimer()
    timer.timeout.connect(pump_glib)
    timer.start(50)
    qt_app._glib_pump_timer = timer  # keep a reference alive


# --------------------------------------------------------- mount backend ---

class CredMountOperation(Gio.MountOperation):
    def __init__(self, username, password):
        super().__init__()
        self._username = username or ""
        self._password = password or ""
        self.connect("ask-password", self._on_ask_password)

    def _on_ask_password(self, op, message, default_user, default_domain, flags):
        if flags & Gio.AskPasswordFlags.NEED_USERNAME:
            op.set_username(self._username)
        if flags & Gio.AskPasswordFlags.NEED_PASSWORD:
            op.set_password(self._password)
        op.set_password_save(Gio.PasswordSave.NEVER)
        op.reply(Gio.MountOperationResult.HANDLED)


def is_mounted(cfg):
    gfile = Gio.File.new_for_uri(share_uri(cfg))
    try:
        gfile.find_enclosing_mount(None)
        return True
    except GLib.Error:
        return False


def mount_share(cfg, password, on_done):
    """on_done(success, error_message) is called on the GLib worker thread."""
    gfile = Gio.File.new_for_uri(share_uri(cfg))
    op = CredMountOperation(cfg.get("username", ""), password)

    def cb(source, result):
        try:
            source.mount_enclosing_volume_finish(result)
            on_done(True, None)
        except GLib.Error as e:
            on_done(False, e.message)

    gfile.mount_enclosing_volume(Gio.MountMountFlags.NONE, op, None, cb)


def unmount_share(cfg, on_done):
    gfile = Gio.File.new_for_uri(share_uri(cfg))
    try:
        mount = gfile.find_enclosing_mount(None)
    except GLib.Error as e:
        on_done(False, e.message)
        return

    def cb(source, result):
        try:
            source.unmount_with_operation_finish(result)
            on_done(True, None)
        except GLib.Error as e:
            on_done(False, e.message)

    mount.unmount_with_operation(Gio.MountUnmountFlags.NONE, None, None, cb)


# --------------------------------------------------------------- theming ---
# Widgets read their colors from Qt's palette, so everything re-themes at once
# when the palette is swapped. The one place that needs explicit colors - the
# status badges - uses the same semantic tokens COSMIC ships in
# /usr/share/color-schemes/Cosmic{Light,Dark}.colors (ForegroundPositive /
# ForegroundNegative / ForegroundNeutral / ForegroundInactive) rather than
# arbitrary hardcoded hex values, so badges look native in both themes.
#
# Which palette to use is decided by the XDG settings portal rather than by
# inspecting whatever palette Qt happened to start with. Qt only knows
# COSMIC's colors when qt5ct/qt6ct is configured to tell it, which is not the
# default and is never true inside a Flatpak sandbox - in those cases palette
# sniffing silently reports "light" on a dark desktop. The portal answers
# correctly in both cases, is what COSMIC itself drives, and additionally
# reports the user's accent color.

COSMIC_TOKENS = {
    "dark": {
        "positive": (94, 219, 140),
        "negative": (255, 160, 154),
        "neutral": (255, 163, 125),
        "muted": (211, 211, 211),
        "secondary": (185, 188, 192),
    },
    "light": {
        "positive": (0, 87, 44),
        "negative": (137, 4, 24),
        "neutral": (121, 44, 0),
        "muted": (95, 99, 104),
        "secondary": (95, 99, 104),
    },
}


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
PALETTE_COLORS = {
    "dark": {
        "window": "#1c1c1c", "base": "#252525", "alt_base": "#2e2e2e",
        "text": "#e8e8e8", "mid": "#4a4a4a", "button": "#2e2e2e",
        "disabled": "#7a7a7a", "highlight": "#63d0df",
    },
    "light": {
        "window": "#f5f5f5", "base": "#ffffff", "alt_base": "#ededed",
        "text": "#1b1b1b", "mid": "#c6c6c6", "button": "#ededed",
        "disabled": "#9a9a9a", "highlight": "#2a7fb8",
    },
}


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


class ToggleSwitch(QtWidgets.QAbstractButton):
    """A small animated pill-style on/off switch, used in place of a checkbox."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(QtCore.Qt.PointingHandCursor)
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


STATUS_TOKEN_KEY = {
    "connected": "positive",
    "disconnected": "muted",
    "connecting...": "neutral",
    "disconnecting...": "neutral",
    "checking...": "muted",
    "unknown": "muted",
    "error": "negative",
    "no saved password": "negative",
}


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


APP_STYLESHEET = """
QMainWindow, QDialog {
    background: palette(window);
}
QListWidget {
    border: none;
    background: transparent;
}
QListWidget::item {
    border: none;
}
QListWidget::item:selected {
    background: transparent;
}
#shareCard {
    background: palette(base);
    border: 1px solid palette(mid);
    border-radius: 10px;
}
#shareLabel {
    font-size: 13px;
    font-weight: 600;
}
/* Color is set per-card from the "secondary" token, not here: palette(mid)
   is a border/separator color and lands around 1.7:1 against the card
   background, which is unreadable for body text in both themes. */
#shareTarget {
    font-size: 12px;
}
#headerTitle {
    font-size: 18px;
    font-weight: 700;
}
QPushButton#primaryButton {
    background: palette(highlight);
    color: palette(highlighted-text);
    border: none;
    border-radius: 8px;
    padding: 7px 16px;
    font-weight: 600;
}
QPushButton#primaryButton:hover {
    background: palette(highlight);
}
QToolButton[class="iconButton"] {
    border: none;
    border-radius: 6px;
    padding: 5px;
}
QToolButton[class="iconButton"]:hover {
    background: palette(mid);
}
"""


# -------------------------------------------------------------- add/edit ---

class ShareDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, existing=None, default_host=""):
        super().__init__(parent)
        self.setWindowTitle("Edit Share" if existing else "Add Share")
        self.existing = existing

        form = QtWidgets.QFormLayout(self)

        self.protocol_combo = QtWidgets.QComboBox()
        for key, label in PROTOCOLS:
            self.protocol_combo.addItem(label, key)
        current_protocol = existing.get("protocol", DEFAULT_PROTOCOL) if existing else DEFAULT_PROTOCOL
        self.protocol_combo.setCurrentIndex(max(0, self.protocol_combo.findData(current_protocol)))

        self.label_edit = QtWidgets.QLineEdit(existing["label"] if existing else "")
        self.host_edit = QtWidgets.QLineEdit(existing["host"] if existing else default_host)
        self.share_edit = QtWidgets.QLineEdit(existing["share"] if existing else "")
        self.user_edit = QtWidgets.QLineEdit(existing["username"] if existing else "")
        self.pass_edit = QtWidgets.QLineEdit()
        self.pass_edit.setEchoMode(QtWidgets.QLineEdit.Password)
        self.pass_edit.setPlaceholderText(
            "(leave blank to keep current password)" if existing else ""
        )

        form.addRow("Protocol:", self.protocol_combo)
        form.addRow("Label:", self.label_edit)
        form.addRow("Host / IP:", self.host_edit)
        form.addRow("Share / path:", self.share_edit)
        form.addRow("Username:", self.user_edit)
        form.addRow("Password:", self.pass_edit)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self):
        return {
            "protocol": self.protocol_combo.currentData(),
            "label": self.label_edit.text().strip(),
            "host": self.host_edit.text().strip(),
            "share": self.share_edit.text().strip(),
            "username": self.user_edit.text().strip(),
        }, self.pass_edit.text()


# ------------------------------------------------------------ main window --

class Bridge(QtCore.QObject):
    done = QtCore.pyqtSignal(str, bool, str)  # share_id, success, error


class ShareCard(QtWidgets.QFrame):
    def __init__(self, cfg):
        super().__init__()
        self.share_id = cfg["id"]
        self.setObjectName("shareCard")

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 10, 10)
        layout.setSpacing(12)

        self.toggle = ToggleSwitch()
        layout.addWidget(self.toggle)

        text_col = QtWidgets.QVBoxLayout()
        text_col.setSpacing(2)
        self.label_lbl = QtWidgets.QLabel(cfg["label"])
        self.label_lbl.setObjectName("shareLabel")
        protocol = cfg.get("protocol", DEFAULT_PROTOCOL)
        self.target_lbl = QtWidgets.QLabel(f"{protocol}://{cfg['host']}/{cfg['share']}")
        self.target_lbl.setObjectName("shareTarget")
        text_col.addWidget(self.label_lbl)
        text_col.addWidget(self.target_lbl)
        layout.addLayout(text_col, 1)

        self.badge = StatusBadge("unknown")
        layout.addWidget(self.badge)

        self.edit_btn = icon_button(
            ["document-edit-symbolic", "document-edit"], "Edit share"
        )
        layout.addWidget(self.edit_btn)

        self.delete_btn = icon_button(
            ["user-trash-symbolic", "edit-delete-symbolic", "edit-delete"], "Delete share"
        )
        layout.addWidget(self.delete_btn)

        self.uri_text = self.target_lbl.text()
        self.refresh_theme()

    def set_link(self, path):
        """Shows the short local path once the share is mounted, since that's
        the one worth knowing; the URI stays in the tooltip."""
        if path:
            display = str(path)
            home = str(Path.home())
            if display.startswith(home + os.sep):
                display = "~" + display[len(home):]
            self.target_lbl.setText(display)
            self.setToolTip(f"{self.uri_text}\nMounted at {path}")
        else:
            self.target_lbl.setText(self.uri_text)
            self.setToolTip(self.uri_text)

    def refresh_theme(self):
        """Recomputes the colors that are baked into stylesheets rather than
        read live from the palette."""
        r, g, b = cosmic_tokens(self)["secondary"]
        self.target_lbl.setStyleSheet(f"#shareTarget {{ color: rgb({r},{g},{b}); }}")
        self.badge.set_status(self.badge.text())

    def set_enabled_toggle(self, enabled):
        self.toggle.setEnabled(enabled)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, theme):
        super().__init__()
        self.setWindowTitle("Mountie")
        self.resize(560, 420)

        self.cfg = load_config()
        self.theme = theme
        # Applied before any widgets exist, so nothing is built against a
        # palette that's about to be replaced.
        self.theme.set_mode(self.cfg["theme"])

        self.bridge = Bridge()
        self.bridge.done.connect(self._on_op_done)
        self.cards = {}  # share_id -> ShareCard

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Network Shares")
        title.setObjectName("headerTitle")
        header.addWidget(title)
        header.addStretch()

        refresh_btn = icon_button(["view-refresh-symbolic", "view-refresh"], "Refresh status")
        refresh_btn.clicked.connect(self.refresh_all_status)
        header.addWidget(refresh_btn)

        header.addWidget(self._build_theme_button())

        # Sits on the highlight color, so it tints against that, not the window.
        self.add_btn = add_btn = QtWidgets.QPushButton(" Add Share")
        add_btn.setIcon(tinted_icon(
            self.palette().color(QtGui.QPalette.HighlightedText),
            "list-add-symbolic", "list-add",
        ))
        add_btn.setObjectName("primaryButton")
        add_btn.setCursor(QtCore.Qt.PointingHandCursor)
        add_btn.clicked.connect(self.add_share)
        header.addWidget(add_btn)
        layout.addLayout(header)

        self.list = QtWidgets.QListWidget()
        self.list.setFocusPolicy(QtCore.Qt.NoFocus)
        self.list.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.list.setSpacing(6)
        layout.addWidget(self.list)

        self.reload_list(query_status=True)
        self.theme.changed.connect(self._on_theme_changed)

    # ---- theme ----

    def _build_theme_button(self):
        btn = icon_button([], "Appearance")
        btn.icon_painter = appearance_icon
        retint_icon_button(btn)
        menu = QtWidgets.QMenu(self)
        group = QtWidgets.QActionGroup(self)
        group.setExclusive(True)
        for key, label in THEMES:
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(key == self.cfg["theme"])
            action.triggered.connect(lambda _, k=key: self.set_theme(k))
            group.addAction(action)
        btn.setMenu(menu)
        btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        return btn

    def set_theme(self, mode):
        self.cfg["theme"] = mode
        save_config(self.cfg)
        self.theme.set_mode(mode)

    def _on_theme_changed(self):
        # Badges bake their colors into a stylesheet, and icons are pixmaps
        # already painted in the old color - unlike palette-driven widgets,
        # both have to be told to recompute.
        for card in self.cards.values():
            card.refresh_theme()
        for btn in self.findChildren(QtWidgets.QToolButton):
            retint_icon_button(btn)
        self.add_btn.setIcon(tinted_icon(
            self.palette().color(QtGui.QPalette.HighlightedText),
            "list-add-symbolic", "list-add",
        ))

    # ---- list population ----

    def reload_list(self, query_status=False):
        if query_status:
            prune_links(self.cfg)
        self.list.clear()
        self.cards = {}
        for cfg in self.cfg["shares"]:
            self._add_card(cfg, query_status)

    def _add_card(self, cfg, query_status):
        card = ShareCard(cfg)
        card.edit_btn.clicked.connect(lambda _, cid=cfg["id"]: self.edit_share(cid))
        card.delete_btn.clicked.connect(lambda _, cid=cfg["id"]: self.delete_share(cid))

        item = QtWidgets.QListWidgetItem(self.list)
        item.setSizeHint(card.sizeHint())
        self.list.addItem(item)
        self.list.setItemWidget(item, card)
        self.cards[cfg["id"]] = card

        if query_status:
            mounted = is_mounted(cfg)
            card.set_link(update_link(self.cfg, cfg))
            card.badge.set_status("connected" if mounted else "disconnected")
            card.toggle.blockSignals(True)
            card.toggle.setChecked(mounted)
            card.toggle._offset = 21.0 if mounted else 3.0
            card.toggle.blockSignals(False)

        # connect only after initial state is set, so setChecked() above
        # never itself triggers a mount/unmount action
        card.toggle.toggled.connect(lambda checked, cid=cfg["id"]: self.on_toggle(cid, checked))

    def refresh_all_status(self):
        self.reload_list(query_status=True)

    def _cfg_for_id(self, share_id):
        for cfg in self.cfg["shares"]:
            if cfg["id"] == share_id:
                return cfg
        return None

    # ---- toggle -> mount/unmount ----

    def on_toggle(self, share_id, checked):
        card = self.cards.get(share_id)
        cfg = self._cfg_for_id(share_id)
        if cfg is None or card is None:
            return

        card.set_enabled_toggle(False)

        if checked:
            card.badge.set_status("connecting...")
            password = get_password(share_id) or ""
            if not password:
                card.set_enabled_toggle(True)
                card.toggle.blockSignals(True)
                card.toggle.setChecked(False)
                card.toggle.blockSignals(False)
                card.badge.set_status("no saved password")
                QtWidgets.QMessageBox.warning(
                    self, "No password",
                    f"No saved password for '{cfg['label']}'. Edit the share to set one.",
                )
                return
            mount_share(cfg, password,
                         lambda ok, err, sid=share_id: self.bridge.done.emit(sid, ok, err or ""))
        else:
            card.badge.set_status("disconnecting...")
            unmount_share(cfg,
                          lambda ok, err, sid=share_id: self.bridge.done.emit(sid, ok, err or ""))

    def _on_op_done(self, share_id, success, error):
        card = self.cards.get(share_id)
        if card is None:
            return
        card.set_enabled_toggle(True)
        if success:
            share = self._cfg_for_id(share_id)
            mounted = is_mounted(share)
            card.set_link(update_link(self.cfg, share))
            card.badge.set_status("connected" if mounted else "disconnected")
            card.toggle.blockSignals(True)
            card.toggle.setChecked(mounted)
            card.toggle.blockSignals(False)
        else:
            card.toggle.blockSignals(True)
            card.toggle.setChecked(False)
            card.toggle.blockSignals(False)
            card.badge.set_status("error")
            QtWidgets.QMessageBox.critical(self, "Error", error or "Unknown error")

    # ---- add/edit/delete ----

    def add_share(self):
        last_host = self.cfg["shares"][-1]["host"] if self.cfg["shares"] else ""
        dlg = ShareDialog(self, default_host=last_host)
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return
        values, password = dlg.values()
        if not values["label"] or not values["host"] or not values["share"]:
            QtWidgets.QMessageBox.warning(self, "Missing info", "Label, host, and share name are required.")
            return
        values["id"] = uuid.uuid4().hex
        self.cfg["shares"].append(values)
        save_config(self.cfg)
        if password:
            set_password(values["id"], password)
        self.reload_list(query_status=True)

    def edit_share(self, share_id):
        cfg = self._cfg_for_id(share_id)
        if cfg is None:
            return
        dlg = ShareDialog(self, existing=cfg)
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return
        values, password = dlg.values()
        if not values["label"] or not values["host"] or not values["share"]:
            QtWidgets.QMessageBox.warning(self, "Missing info", "Label, host, and share name are required.")
            return
        cfg.update(values)
        save_config(self.cfg)
        if password:
            set_password(share_id, password)
        self.reload_list(query_status=True)

    def delete_share(self, share_id):
        cfg = self._cfg_for_id(share_id)
        if cfg is None:
            return
        reply = QtWidgets.QMessageBox.question(
            self, "Delete share", f"Delete '{cfg['label']}'? This removes its saved password too.",
        )
        if reply != QtWidgets.QMessageBox.Yes:
            return
        if is_mounted(cfg):
            unmount_share(cfg, lambda ok, err: None)
        remove_link(self.cfg, cfg)
        clear_password(share_id)
        self.cfg["shares"] = [c for c in self.cfg["shares"] if c["id"] != share_id]
        save_config(self.cfg)
        self.reload_list(query_status=True)


def main():
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
