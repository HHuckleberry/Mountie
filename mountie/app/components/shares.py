"""Share editing and main-window share cards."""

import os
from pathlib import Path

from PyQt5 import QtCore, QtWidgets

from mountie.settings import (
    CREDENTIAL_POLICIES,
    CREDENTIAL_USE_GLOBAL,
    DEFAULT_PROTOCOL,
    DISCONNECT_OPTIONS,
    PROTOCOLS,
    SHARE_PRESETS,
)
from mountie.app.components.common import StatusBadge, ToggleSwitch
from mountie.app.components.discovery import DiscoveryPanel
from mountie.app.theme import cosmic_tokens, icon_button


class ShareDialog(QtWidgets.QDialog):
    def __init__(
        self,
        parent=None,
        existing=None,
        initial=None,
        default_host="",
        global_credential_policy="ask",
        credential_profiles=None,
        configured_shares=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Edit Share" if existing else "Add Share")
        self.resize(560, 500)
        self.setMinimumSize(520, 450)
        self.existing = existing
        source = existing or initial or {}
        self.credential_profiles = credential_profiles or []
        self._configured_shares = configured_shares or []

        # Only offer templates/discovery on a genuinely blank "Add Share" —
        # editing an existing share or importing a discovered/external one
        # already has real connection details that these shouldn't overwrite.
        is_blank_add = existing is None and initial is None
        self.template_combo = None
        if is_blank_add:
            self.template_combo = QtWidgets.QComboBox()
            self.template_combo.addItem("Custom", None)
            for preset in SHARE_PRESETS:
                self.template_combo.addItem(preset["menu_label"], preset["key"])

        outer = QtWidgets.QVBoxLayout(self)
        self.tabs = tabs = QtWidgets.QTabWidget()
        tabs.setObjectName("shareSettingsTabs")
        outer.addWidget(tabs, 1)

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
        self.pass_edit = QtWidgets.QLineEdit(source.get("_password", ""))
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

        if self.template_combo is not None:
            self.template_combo.currentIndexChanged.connect(self._template_changed)

        connection_page = QtWidgets.QWidget()
        connection = QtWidgets.QFormLayout(connection_page)
        connection.setContentsMargins(18, 18, 18, 18)
        connection.setVerticalSpacing(12)
        if self.template_combo is not None:
            connection.addRow("Start from:", self.template_combo)
        connection.addRow("Protocol:", self.protocol_combo)
        connection.addRow("Display name:", self.label_edit)
        connection.addRow("Server:", self.host_edit)
        connection.addRow("Share or path:", self.share_edit)
        connection_hint = QtWidgets.QLabel(
            "Use a hostname or IP address. Mountie safely encodes the share path for GVfs."
        )
        connection_hint.setObjectName("settingsHint")
        connection_hint.setWordWrap(True)
        connection.addRow("", connection_hint)
        tabs.addTab(connection_page, "Connection")

        self.discovery_panel = None
        if is_blank_add:
            self.discovery_panel = DiscoveryPanel(self._configured_shares, self)
            self.discovery_panel.import_requested.connect(self._on_discovered)
            tabs.addTab(self.discovery_panel, "Discover")

        credentials_page = QtWidgets.QWidget()
        credentials = QtWidgets.QFormLayout(credentials_page)
        credentials.setContentsMargins(18, 18, 18, 18)
        credentials.setVerticalSpacing(12)
        credentials.addRow("Credential profile:", self.profile_combo)
        credentials.addRow("Domain / workgroup:", self.domain_edit)
        credentials.addRow("Username:", self.user_edit)
        credentials.addRow("Password storage:", self.credential_policy_combo)
        credentials.addRow("Password:", self.pass_edit)
        credentials.addRow("Save as new profile:", self.new_profile_name)
        credentials_hint = QtWidgets.QLabel(
            "Leave domain/workgroup blank for local accounts and shares that do not use one. "
            "Passwords are stored only according to the selected policy."
        )
        credentials_hint.setObjectName("settingsHint")
        credentials_hint.setWordWrap(True)
        credentials.addRow("", credentials_hint)
        tabs.addTab(credentials_page, "Credentials")

        disconnect_page = QtWidgets.QWidget()
        disconnect = QtWidgets.QVBoxLayout(disconnect_page)
        disconnect.setContentsMargins(18, 18, 18, 18)
        disconnect.setSpacing(12)
        heading = QtWidgets.QLabel("Automatic disconnect")
        heading.setObjectName("sectionTitle")
        disconnect.addWidget(heading)
        disconnect_hint = QtWidgets.QLabel(
            "The timer measures total connected time, not file inactivity. "
            "These options work while Mountie is running."
        )
        disconnect_hint.setObjectName("settingsHint")
        disconnect_hint.setWordWrap(True)
        disconnect.addWidget(disconnect_hint)
        timer_row = QtWidgets.QHBoxLayout()
        timer_row.addWidget(QtWidgets.QLabel("Disconnect:"))
        timer_row.addWidget(self.disconnect_combo, 1)
        disconnect.addLayout(timer_row)
        disconnect.addWidget(self.disconnect_on_lock)
        disconnect.addWidget(self.disconnect_on_suspend)
        disconnect.addStretch()
        tabs.addTab(disconnect_page, "Disconnect")

        self._update_password_hint(global_credential_policy)
        self._profile_changed(global_credential_policy)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

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

    def _template_changed(self):
        key = self.template_combo.currentData()
        if key is None:
            return
        preset = next(preset for preset in SHARE_PRESETS if preset["key"] == key)
        self._apply_preset_fields(preset["initial"])

    def _on_discovered(self, initial):
        self._apply_discovered_fields(initial)
        self.tabs.setCurrentIndex(0)

    def _apply_discovered_fields(self, initial):
        self._apply_preset_fields(initial)
        self.host_edit.setText(initial.get("host", ""))
        self.share_edit.setText(initial.get("share", ""))
        self.domain_edit.setText(initial.get("domain", ""))
        self.user_edit.setText(initial.get("username", ""))
        if "_password" in initial:
            self.pass_edit.setText(initial["_password"])

    def _apply_preset_fields(self, values):
        self.protocol_combo.setCurrentIndex(max(
            0, self.protocol_combo.findData(values.get("protocol", DEFAULT_PROTOCOL))
        ))
        self.label_edit.setText(values.get("label", ""))
        minutes = values.get("disconnect_after_minutes", 0)
        index = self.disconnect_combo.findData(minutes)
        if index < 0 and minutes:
            self.disconnect_combo.addItem(f"After {minutes} minutes", minutes)
            index = self.disconnect_combo.count() - 1
        self.disconnect_combo.setCurrentIndex(max(0, index))
        self.disconnect_on_lock.setChecked(values.get("disconnect_on_lock", False))
        self.disconnect_on_suspend.setChecked(values.get("disconnect_on_suspend", False))

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

    def done(self, result):
        if self.discovery_panel is not None:
            self.discovery_panel.cancel_pending()
        super().done(result)


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
