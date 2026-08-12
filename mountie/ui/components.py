"""Reusable widgets and dialogs used by Mountie's main window."""

import os
from pathlib import Path

from PyQt5 import QtCore, QtGui, QtWidgets

from mountie.mounts import share_uri
from mountie.settings import (
    CREDENTIAL_POLICIES,
    CREDENTIAL_USE_GLOBAL,
    DEFAULT_PROTOCOL,
    DISCONNECT_OPTIONS,
    PROTOCOLS,
)
from mountie.ui.theme import cosmic_tokens, icon_button
from mountie.ui.visuals import STATUS_TOKEN_KEY


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




# -------------------------------------------------------------- add/edit ---

class ShareDialog(QtWidgets.QDialog):
    def __init__(
        self,
        parent=None,
        existing=None,
        initial=None,
        default_host="",
        global_credential_policy="ask",
        credential_profiles=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Edit Share" if existing else "Add Share")
        self.existing = existing
        source = existing or initial or {}
        self.credential_profiles = credential_profiles or []

        form = QtWidgets.QFormLayout(self)

        self.protocol_combo = QtWidgets.QComboBox()
        for key, label in PROTOCOLS:
            self.protocol_combo.addItem(label, key)
        current_protocol = source.get("protocol", DEFAULT_PROTOCOL)
        self.protocol_combo.setCurrentIndex(max(0, self.protocol_combo.findData(current_protocol)))

        self.label_edit = QtWidgets.QLineEdit(source.get("label", ""))
        self.host_edit = QtWidgets.QLineEdit(source.get("host", default_host))
        self.share_edit = QtWidgets.QLineEdit(source.get("share", ""))
        self.domain_edit = QtWidgets.QLineEdit(source.get("domain", ""))
        self.domain_edit.setPlaceholderText("Optional")
        self.user_edit = QtWidgets.QLineEdit(source.get("username", ""))
        self.profile_combo = QtWidgets.QComboBox()
        self.profile_combo.addItem("This share only", "")
        for profile in self.credential_profiles:
            self.profile_combo.addItem(profile["label"], profile["id"])
        self.profile_combo.setCurrentIndex(max(
            0, self.profile_combo.findData(source.get("credential_profile_id", ""))
        ))
        self.new_profile_name = QtWidgets.QLineEdit()
        self.new_profile_name.setPlaceholderText("Optional reusable profile name")
        self.pass_edit = QtWidgets.QLineEdit()
        self.pass_edit.setEchoMode(QtWidgets.QLineEdit.Password)
        self.credential_policy_combo = QtWidgets.QComboBox()
        self.credential_policy_combo.addItem(
            f"Use global ({dict(CREDENTIAL_POLICIES)[global_credential_policy]})",
            CREDENTIAL_USE_GLOBAL,
        )
        for key, policy_label in CREDENTIAL_POLICIES:
            self.credential_policy_combo.addItem(policy_label, key)
        current_policy = source.get("credential_policy", CREDENTIAL_USE_GLOBAL)
        self.credential_policy_combo.setCurrentIndex(max(
            0, self.credential_policy_combo.findData(current_policy)
        ))
        self.credential_policy_combo.currentIndexChanged.connect(
            lambda: self._update_password_hint(global_credential_policy)
        )
        self.profile_combo.currentIndexChanged.connect(
            lambda: self._profile_changed(global_credential_policy)
        )
        self.disconnect_combo = QtWidgets.QComboBox()
        for minutes, disconnect_label in DISCONNECT_OPTIONS:
            self.disconnect_combo.addItem(disconnect_label, minutes)
        current_minutes = source.get("disconnect_after_minutes", 0)
        index = self.disconnect_combo.findData(current_minutes)
        if index < 0 and current_minutes:
            self.disconnect_combo.addItem(
                f"After {current_minutes} minutes", current_minutes
            )
            index = self.disconnect_combo.count() - 1
        self.disconnect_combo.setCurrentIndex(max(0, index))
        self.disconnect_on_lock = QtWidgets.QCheckBox("Disconnect when screen locks")
        self.disconnect_on_lock.setChecked(source.get("disconnect_on_lock", False))
        self.disconnect_on_suspend = QtWidgets.QCheckBox("Disconnect before suspend")
        self.disconnect_on_suspend.setChecked(source.get("disconnect_on_suspend", False))

        form.addRow("Protocol:", self.protocol_combo)
        form.addRow("Label:", self.label_edit)
        form.addRow("Host / IP:", self.host_edit)
        form.addRow("Share / path:", self.share_edit)
        form.addRow("Domain / workgroup:", self.domain_edit)
        form.addRow("Username:", self.user_edit)
        form.addRow("Credential profile:", self.profile_combo)
        form.addRow("Save identity as profile:", self.new_profile_name)
        form.addRow("Credential policy:", self.credential_policy_combo)
        form.addRow("Password:", self.pass_edit)
        form.addRow("Auto-disconnect:", self.disconnect_combo)
        form.addRow("", self.disconnect_on_lock)
        form.addRow("", self.disconnect_on_suspend)
        self._update_password_hint(global_credential_policy)
        self._profile_changed(global_credential_policy)

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
            "domain": self.domain_edit.text().strip(),
            "username": self.user_edit.text().strip(),
            "credential_profile_id": self.profile_combo.currentData(),
            "credential_policy": self.credential_policy_combo.currentData(),
            "disconnect_after_minutes": self.disconnect_combo.currentData(),
            "disconnect_on_lock": self.disconnect_on_lock.isChecked(),
            "disconnect_on_suspend": self.disconnect_on_suspend.isChecked(),
            "_new_profile_name": self.new_profile_name.text().strip(),
        }, self.pass_edit.text()

    def _update_password_hint(self, global_policy):
        policy = self.credential_policy_combo.currentData()
        if policy == CREDENTIAL_USE_GLOBAL:
            policy = global_policy
        if policy == "ask":
            self.pass_edit.clear()
            self.pass_edit.setEnabled(False)
            self.pass_edit.setPlaceholderText("Requested when connecting")
        else:
            self.pass_edit.setEnabled(True)
            self.pass_edit.setPlaceholderText(
                "(leave blank to keep current password)" if self.existing else ""
            )

    def _profile_changed(self, global_policy):
        profile_id = self.profile_combo.currentData()
        profile = next(
            (item for item in self.credential_profiles if item["id"] == profile_id),
            None,
        )
        using_profile = profile is not None
        for field in (self.domain_edit, self.user_edit, self.credential_policy_combo):
            field.setEnabled(not using_profile)
        self.new_profile_name.setEnabled(not using_profile)
        if profile is not None:
            self.domain_edit.setText(profile["domain"])
            self.user_edit.setText(profile["username"])
            policy = profile.get("credential_policy", CREDENTIAL_USE_GLOBAL)
            self.credential_policy_combo.setCurrentIndex(max(
                0, self.credential_policy_combo.findData(policy)
            ))
        self._update_password_hint(global_policy)


# ------------------------------------------------------------ main window --

class Bridge(QtCore.QObject):
    # share_id, success, status, error detail
    done = QtCore.pyqtSignal(str, bool, str, str)


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
        self.label_lbl.setTextFormat(QtCore.Qt.PlainText)
        self.label_lbl.setObjectName("shareLabel")
        protocol = cfg.get("protocol", DEFAULT_PROTOCOL)
        self.target_lbl = QtWidgets.QLabel(f"{protocol}://{cfg['host']}/{cfg['share']}")
        self.target_lbl.setTextFormat(QtCore.Qt.PlainText)
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

    def set_operations_enabled(self, enabled):
        self.toggle.setEnabled(enabled)
        self.edit_btn.setEnabled(enabled)
        self.delete_btn.setEnabled(enabled)


class ExternalMountCard(QtWidgets.QFrame):
    """Read-only row for a network mount managed outside Mountie."""

    import_requested = QtCore.pyqtSignal(dict)

    def __init__(self, connection):
        super().__init__()
        self.setObjectName("shareCard")
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 10, 10)
        layout.setSpacing(12)
        layout.addSpacing(42)

        text_col = QtWidgets.QVBoxLayout()
        text_col.setSpacing(2)
        self.label_lbl = QtWidgets.QLabel(connection["name"])
        self.label_lbl.setTextFormat(QtCore.Qt.PlainText)
        self.label_lbl.setObjectName("shareLabel")
        self.target_lbl = QtWidgets.QLabel(connection["uri"])
        self.target_lbl.setTextFormat(QtCore.Qt.PlainText)
        self.target_lbl.setObjectName("shareTarget")
        text_col.addWidget(self.label_lbl)
        text_col.addWidget(self.target_lbl)
        layout.addLayout(text_col, 1)

        self.badge = StatusBadge("external")
        layout.addWidget(self.badge)
        if connection.get("config"):
            self.import_btn = QtWidgets.QPushButton("Import")
            self.import_btn.setToolTip("Add this connection to Mountie")
            self.import_btn.clicked.connect(
                lambda: self.import_requested.emit(connection["config"].copy())
            )
            layout.addWidget(self.import_btn)
        self.setToolTip("Mounted outside Mountie; shown here for visibility only")
        self.refresh_theme()

    def refresh_theme(self):
        r, g, b = cosmic_tokens(self)["secondary"]
        self.target_lbl.setStyleSheet(f"#shareTarget {{ color: rgb({r},{g},{b}); }}")
        self.badge.set_status("external")
