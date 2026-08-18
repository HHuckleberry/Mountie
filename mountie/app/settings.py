"""Application settings, diagnostics, and credential-profile dialogs."""

import shlex

from PyQt5 import QtCore, QtGui, QtWidgets

from mountie import __version__, native_mount
from mountie.logging_setup import LOG_PATH, read_log
from mountie.sbom import format_sbom, load_sbom
from mountie.settings import (
    BACKUP_PATH,
    CONFIG_PATH,
    CREDENTIAL_POLICIES,
    CREDENTIAL_USE_GLOBAL,
    THEMES,
)


REPOSITORY_URL = "https://github.com/HHuckleberry/Mountie"
ISSUES_URL = f"{REPOSITORY_URL}/issues"


class LogDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Mountie Log")
        self.resize(720, 440)

        layout = QtWidgets.QVBoxLayout(self)
        path_label = QtWidgets.QLabel(f"Log file: {LOG_PATH}")
        path_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addWidget(path_label)

        self.output = QtWidgets.QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        layout.addWidget(self.output)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        refresh = buttons.addButton("Refresh", QtWidgets.QDialogButtonBox.ActionRole)
        refresh.clicked.connect(self.refresh)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh()

    def refresh(self):
        self.output.setPlainText(read_log())
        cursor = self.output.textCursor()
        cursor.movePosition(QtGui.QTextCursor.End)
        self.output.setTextCursor(cursor)


class SbomDialog(QtWidgets.QDialog):
    """Read-only view of the software bill of materials bundled at build time."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Software Bill of Materials")
        self.resize(560, 480)

        layout = QtWidgets.QVBoxLayout(self)
        sbom = load_sbom()

        self.output = QtWidgets.QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        if sbom is None:
            layout.addWidget(QtWidgets.QLabel(
                "This build did not bundle a software bill of materials."
            ))
            self.output.setVisible(False)
        else:
            self.output.setPlainText(format_sbom(sbom))
        layout.addWidget(self.output)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class CredentialProfilesDialog(QtWidgets.QDialog):
    """Edit reusable identities without ever displaying stored passwords."""

    def __init__(self, profiles, shares, global_policy, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Credential Profiles")
        self.resize(700, 390)
        self._profiles = [profile.copy() for profile in profiles]
        self._shares = shares
        self.password_updates = {}
        self.deleted_ids = set()
        self._loading = False

        outer = QtWidgets.QVBoxLayout(self)
        explanation = QtWidgets.QLabel(
            "Profiles reuse one identity across multiple shares. Passwords stay in "
            "the keyring and are never shown here. Enter a password only to replace it."
        )
        explanation.setWordWrap(True)
        outer.addWidget(explanation)

        body = QtWidgets.QHBoxLayout()
        left = QtWidgets.QVBoxLayout()
        self.list = QtWidgets.QListWidget()
        self.list.currentRowChanged.connect(self._load_current)
        left.addWidget(self.list)
        list_buttons = QtWidgets.QHBoxLayout()
        add = QtWidgets.QPushButton("Add")
        add.clicked.connect(self._add)
        remove = QtWidgets.QPushButton("Remove")
        remove.clicked.connect(self._remove)
        list_buttons.addWidget(add)
        list_buttons.addWidget(remove)
        left.addLayout(list_buttons)
        body.addLayout(left, 1)

        form = QtWidgets.QFormLayout()
        self.name = QtWidgets.QLineEdit()
        self.username = QtWidgets.QLineEdit()
        self.domain = QtWidgets.QLineEdit()
        self.domain.setPlaceholderText("Optional")
        self.policy = QtWidgets.QComboBox()
        self.policy.addItem(
            f"Use global ({dict(CREDENTIAL_POLICIES)[global_policy]})",
            CREDENTIAL_USE_GLOBAL,
        )
        for key, label in CREDENTIAL_POLICIES:
            self.policy.addItem(label, key)
        self.password = QtWidgets.QLineEdit()
        self.password.setEchoMode(QtWidgets.QLineEdit.Password)
        self.password.setPlaceholderText("Leave blank to keep current password")
        self.usage = QtWidgets.QLabel()
        self.usage.setWordWrap(True)
        form.addRow("Profile name:", self.name)
        form.addRow("Domain / workgroup:", self.domain)
        form.addRow("Username:", self.username)
        form.addRow("Credential policy:", self.policy)
        form.addRow("New password:", self.password)
        form.addRow("Used by:", self.usage)
        for widget in (self.name, self.username, self.domain):
            widget.textChanged.connect(self._store_current)
        self.policy.currentIndexChanged.connect(self._store_current)
        self.password.textChanged.connect(self._store_password)
        body.addLayout(form, 2)
        outer.addLayout(body)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self._rebuild_list()

    @property
    def profiles(self):
        return [profile.copy() for profile in self._profiles]

    def _rebuild_list(self, row=0):
        self.list.blockSignals(True)
        self.list.clear()
        self.list.addItems([
            profile["label"] or "Untitled profile" for profile in self._profiles
        ])
        self.list.blockSignals(False)
        if self._profiles:
            self.list.setCurrentRow(min(row, len(self._profiles) - 1))
        else:
            self._load_current(-1)

    def _set_form_enabled(self, enabled):
        for widget in (self.name, self.username, self.domain, self.policy, self.password):
            widget.setEnabled(enabled)

    def _load_current(self, row):
        self._loading = True
        if row < 0 or row >= len(self._profiles):
            for widget in (self.name, self.username, self.domain, self.password):
                widget.clear()
            self.usage.clear()
            self._set_form_enabled(False)
        else:
            profile = self._profiles[row]
            self._set_form_enabled(True)
            self.name.setText(profile["label"])
            self.username.setText(profile["username"])
            self.domain.setText(profile["domain"])
            self.policy.setCurrentIndex(max(0, self.policy.findData(
                profile.get("credential_policy", CREDENTIAL_USE_GLOBAL)
            )))
            self.password.clear()
            labels = [
                share["label"] for share in self._shares
                if share.get("credential_profile_id") == profile["id"]
            ]
            self.usage.setText(", ".join(labels) if labels else "No shares")
        self._loading = False

    def _store_current(self):
        row = self.list.currentRow()
        if self._loading or row < 0:
            return
        profile = self._profiles[row]
        profile.update({
            "label": self.name.text().strip(),
            "username": self.username.text().strip(),
            "domain": self.domain.text().strip(),
            "credential_policy": self.policy.currentData(),
        })
        self.list.item(row).setText(profile["label"] or "Untitled profile")

    def _store_password(self, password):
        row = self.list.currentRow()
        if self._loading or row < 0:
            return
        profile_id = self._profiles[row]["id"]
        if password:
            self.password_updates[profile_id] = password
        else:
            self.password_updates.pop(profile_id, None)

    def _add(self):
        import uuid
        self._profiles.append({
            "id": uuid.uuid4().hex,
            "label": "New profile",
            "username": "",
            "domain": "",
            "credential_policy": CREDENTIAL_USE_GLOBAL,
        })
        self._rebuild_list(len(self._profiles) - 1)
        self.name.selectAll()
        self.name.setFocus()

    def _remove(self):
        row = self.list.currentRow()
        if row < 0:
            return
        profile = self._profiles[row]
        used = any(
            share.get("credential_profile_id") == profile["id"]
            for share in self._shares
        )
        if used:
            QtWidgets.QMessageBox.warning(
                self, "Profile is in use",
                "Change those shares to another profile before removing this one.",
            )
            return
        self.deleted_ids.add(profile["id"])
        self.password_updates.pop(profile["id"], None)
        self._profiles.pop(row)
        self._rebuild_list(row)

    def _accept_if_valid(self):
        self._store_current()
        labels = [profile["label"].casefold() for profile in self._profiles]
        if any(not profile["label"] for profile in self._profiles):
            QtWidgets.QMessageBox.warning(
                self, "Missing name", "Every profile needs a name."
            )
            return
        if len(labels) != len(set(labels)):
            QtWidgets.QMessageBox.warning(
                self, "Duplicate profile", "Credential profile names must be unique."
            )
            return
        self.accept()


class SettingsDialog(QtWidgets.QDialog):
    """A compact, navigable home for preferences and diagnostics."""

    def __init__(
        self,
        share_count,
        credential_policy="ask",
        theme="system",
        profile_count=0,
        manage_profiles=None,
        check_for_updates=True,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Mountie Settings")
        self.resize(720, 620)
        self.setMinimumSize(660, 560)

        outer = QtWidgets.QVBoxLayout(self)
        body = QtWidgets.QHBoxLayout()
        body.setSpacing(18)

        self.navigation = QtWidgets.QListWidget()
        self.navigation.setObjectName("settingsNavigation")
        self.navigation.setFixedWidth(155)
        self.navigation.setSpacing(3)
        self.navigation.addItems(("General", "Credentials", "Diagnostics", "About"))
        body.addWidget(self.navigation)

        self.pages = QtWidgets.QStackedWidget()
        self.pages.setObjectName("settingsPages")
        body.addWidget(self.pages, 1)
        outer.addLayout(body, 1)

        self.pages.addWidget(self._general_page(theme, check_for_updates))
        self.pages.addWidget(self._credentials_page(
            credential_policy, profile_count, manage_profiles
        ))
        self.pages.addWidget(self._diagnostics_page(share_count))
        self.pages.addWidget(self._about_page())
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.navigation.setCurrentRow(0)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _page(self, title, description):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(4, 4, 4, 4)
        heading = QtWidgets.QLabel(title)
        heading.setObjectName("settingsTitle")
        layout.addWidget(heading)
        summary = QtWidgets.QLabel(description)
        summary.setObjectName("settingsDescription")
        summary.setWordWrap(True)
        layout.addWidget(summary)
        layout.addSpacing(12)
        return page, layout

    def _general_page(self, theme, check_for_updates=True):
        page, layout = self._page(
            "General", "Choose how Mountie fits into your desktop."
        )
        card = QtWidgets.QGroupBox("Appearance")
        form = QtWidgets.QFormLayout(card)
        self.theme = QtWidgets.QComboBox()
        for key, label in THEMES:
            self.theme.addItem(label, key)
        self.theme.setCurrentIndex(max(0, self.theme.findData(theme)))
        form.addRow("Color scheme:", self.theme)
        hint = QtWidgets.QLabel(
            "System follows your desktop and updates automatically when its appearance changes."
        )
        hint.setObjectName("settingsHint")
        hint.setWordWrap(True)
        form.addRow("", hint)
        layout.addWidget(card)

        updates_card = QtWidgets.QGroupBox("Updates")
        updates_layout = QtWidgets.QVBoxLayout(updates_card)
        self.check_for_updates = QtWidgets.QCheckBox(
            "Automatically check for updates on startup"
        )
        self.check_for_updates.setChecked(check_for_updates)
        updates_layout.addWidget(self.check_for_updates)
        updates_hint = QtWidgets.QLabel(
            "Checks GitHub for a newer release and links to it. Never downloads "
            "or installs anything automatically."
        )
        updates_hint.setObjectName("settingsHint")
        updates_hint.setWordWrap(True)
        updates_layout.addWidget(updates_hint)
        layout.addWidget(updates_card)

        layout.addWidget(self._native_mount_card())

        layout.addStretch()

        # General has grown past a fixed dialog height (Appearance + Updates
        # + Native mount, the last of which can grow further still once its
        # setup command is revealed) - scroll rather than clip or keep
        # guessing at pixel sizes.
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setWidget(page)
        return scroll

    def _native_mount_card(self):
        card = QtWidgets.QGroupBox("Native mount")
        card_layout = QtWidgets.QVBoxLayout(card)

        installed = native_mount.is_helper_installed()
        self.native_helper_installed = installed
        self.native_status_lbl = QtWidgets.QLabel(
            "Installed — native SMB/CIFS mounts are ready to use."
            if installed else
            "Not set up — native SMB/CIFS mounts need a one-time host setup."
        )
        self.native_status_lbl.setWordWrap(True)
        card_layout.addWidget(self.native_status_lbl)

        self.native_setup_btn = QtWidgets.QPushButton(
            "Show removal command…" if installed else "Show setup command…"
        )
        self.native_setup_btn.clicked.connect(self._show_native_setup_command)
        card_layout.addWidget(self.native_setup_btn)

        command_row = QtWidgets.QHBoxLayout()
        self.native_command_edit = QtWidgets.QLineEdit()
        self.native_command_edit.setReadOnly(True)
        self.native_command_edit.setVisible(False)
        self.native_command_edit.setMinimumWidth(0)
        self.native_command_edit.setSizePolicy(
            QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed
        )
        command_row.addWidget(self.native_command_edit, 1)
        self.native_copy_btn = QtWidgets.QPushButton("Copy")
        self.native_copy_btn.setVisible(False)
        self.native_copy_btn.clicked.connect(self._copy_native_setup_command)
        command_row.addWidget(self.native_copy_btn)
        card_layout.addLayout(command_row)

        native_hint = QtWidgets.QLabel(
            "Runs a small root-owned helper via a system authentication prompt "
            "so a share can be mounted with the kernel's own CIFS driver "
            "instead of GVfs, for better throughput on large transfers. "
            "Opt-in per share (Add Share > Mount using); see "
            "docs/native-mount-backend.md for exactly what this grants."
        )
        native_hint.setObjectName("settingsHint")
        native_hint.setWordWrap(True)
        card_layout.addWidget(native_hint)
        return card

    def _show_native_setup_command(self):
        try:
            _write_path, host_path = native_mount.export_installer_for_host()
        except OSError as error:
            QtWidgets.QMessageBox.critical(
                self, "Could not prepare setup command",
                f"Could not write the setup script: {error}",
            )
            return
        command = f"sudo bash -- {shlex.quote(str(host_path))}"
        if self.native_helper_installed:
            command += " --uninstall"
        self.native_command_edit.setText(command)
        self.native_command_edit.setVisible(True)
        self.native_command_edit.selectAll()
        self.native_copy_btn.setVisible(True)
        self.native_setup_btn.setVisible(False)

    def _copy_native_setup_command(self):
        QtWidgets.QApplication.clipboard().setText(self.native_command_edit.text())

    def _credentials_page(self, credential_policy, profile_count, manage_profiles):
        page, layout = self._page(
            "Credentials",
            "Control whether passwords are retained and manage identities shared by multiple connections.",
        )
        card = QtWidgets.QGroupBox("Password storage")
        form = QtWidgets.QFormLayout(card)
        self.credential_policy = QtWidgets.QComboBox()
        for key, label in CREDENTIAL_POLICIES:
            self.credential_policy.addItem(label, key)
        self.credential_policy.setCurrentIndex(max(
            0, self.credential_policy.findData(credential_policy)
        ))
        form.addRow("Default policy:", self.credential_policy)
        explanation = QtWidgets.QLabel(
            "Ask every time stores nothing. Remember until logout uses the keyring's "
            "temporary session collection. Permanent storage uses the default system keyring."
        )
        explanation.setObjectName("settingsHint")
        explanation.setWordWrap(True)
        form.addRow("", explanation)
        layout.addWidget(card)

        profiles = QtWidgets.QGroupBox("Credential profiles")
        profile_layout = QtWidgets.QHBoxLayout(profiles)
        count = QtWidgets.QLabel(
            f"{profile_count} reusable profile{'s' if profile_count != 1 else ''}"
        )
        profile_layout.addWidget(count)
        profile_layout.addStretch()
        manage = QtWidgets.QPushButton("Manage Profiles…")
        manage.setEnabled(manage_profiles is not None)
        if manage_profiles is not None:
            manage.clicked.connect(
                lambda _checked=False: manage_profiles(self)
            )
        profile_layout.addWidget(manage)
        layout.addWidget(profiles)
        layout.addStretch()
        return page

    def _diagnostics_page(self, share_count):
        page, layout = self._page(
            "Diagnostics", "Locations and logs that can help troubleshoot Mountie."
        )
        card = QtWidgets.QGroupBox("Application data")
        details = QtWidgets.QFormLayout(card)
        details.addRow("Configured shares:", QtWidgets.QLabel(str(share_count)))
        details.addRow("Configuration:", self._selectable(str(CONFIG_PATH)))
        details.addRow("Backup:", self._selectable(str(BACKUP_PATH)))
        details.addRow("Log:", self._selectable(str(LOG_PATH)))
        layout.addWidget(card)
        log = QtWidgets.QPushButton("View Application Log…")
        log.clicked.connect(self._show_log)
        layout.addWidget(log, 0, QtCore.Qt.AlignLeft)
        sbom = QtWidgets.QPushButton("View Software Bill of Materials…")
        sbom.clicked.connect(self._show_sbom)
        layout.addWidget(sbom, 0, QtCore.Qt.AlignLeft)
        layout.addStretch()
        return page

    def _about_page(self):
        page, layout = self._page(
            "Mountie", "Mount and manage network shares with GVfs."
        )
        version = QtWidgets.QLabel(f"Version {__version__}")
        version.setObjectName("aboutVersion")
        layout.addWidget(version)
        links = QtWidgets.QHBoxLayout()
        repository = QtWidgets.QPushButton("Repository")
        repository.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(
            QtCore.QUrl(REPOSITORY_URL)
        ))
        issues = QtWidgets.QPushButton("Report an Issue")
        issues.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(
            QtCore.QUrl(ISSUES_URL)
        ))
        links.addWidget(repository)
        links.addWidget(issues)
        links.addStretch()
        layout.addLayout(links)
        layout.addStretch()
        return page

    @staticmethod
    def _selectable(text):
        label = QtWidgets.QLabel(text)
        label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        label.setWordWrap(True)
        return label

    def _show_log(self):
        LogDialog(self).exec_()

    def _show_sbom(self):
        SbomDialog(self).exec_()


# Kept as an import-compatible alias for callers from older integrations.
AboutDialog = SettingsDialog
