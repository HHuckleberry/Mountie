from PyQt5 import QtCore, QtGui, QtWidgets

from mountie import __version__
from mountie.logging_setup import LOG_PATH, read_log
from mountie.settings import (
    BACKUP_PATH,
    CONFIG_PATH,
    CREDENTIAL_POLICIES,
    CREDENTIAL_USE_GLOBAL,
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


class AboutDialog(QtWidgets.QDialog):
    def __init__(self, share_count, credential_policy="ask", parent=None):
        super().__init__(parent)
        self.setWindowTitle("About Mountie")
        self.setMinimumWidth(520)

        layout = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel("Mountie")
        title.setObjectName("headerTitle")
        layout.addWidget(title)
        layout.addWidget(QtWidgets.QLabel(
            f"Version {__version__}\nMount and manage network shares with GVfs."
        ))

        details = QtWidgets.QFormLayout()
        details.addRow("Configured shares:", QtWidgets.QLabel(str(share_count)))
        details.addRow("Configuration:", self._selectable(str(CONFIG_PATH)))
        details.addRow("Backup:", self._selectable(str(BACKUP_PATH)))
        details.addRow("Log:", self._selectable(str(LOG_PATH)))
        layout.addLayout(details)

        security = QtWidgets.QGroupBox("Credentials")
        security_layout = QtWidgets.QVBoxLayout(security)
        policy_row = QtWidgets.QHBoxLayout()
        policy_row.addWidget(QtWidgets.QLabel("Default policy:"))
        self.credential_policy = QtWidgets.QComboBox()
        for key, label in CREDENTIAL_POLICIES:
            self.credential_policy.addItem(label, key)
        self.credential_policy.setCurrentIndex(max(
            0, self.credential_policy.findData(credential_policy)
        ))
        policy_row.addWidget(self.credential_policy, 1)
        security_layout.addLayout(policy_row)
        explanation = QtWidgets.QLabel(
            "Ask every time stores nothing. Remember until logout uses the keyring's "
            "temporary session collection. Permanent storage uses the default system keyring."
        )
        explanation.setWordWrap(True)
        security_layout.addWidget(explanation)
        layout.addWidget(security)

        links = QtWidgets.QHBoxLayout()
        repository = QtWidgets.QPushButton("Repository")
        repository.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(
            QtCore.QUrl(REPOSITORY_URL)
        ))
        issues = QtWidgets.QPushButton("Report an Issue")
        issues.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(
            QtCore.QUrl(ISSUES_URL)
        ))
        log = QtWidgets.QPushButton("View Log")
        log.clicked.connect(self._show_log)
        links.addWidget(repository)
        links.addWidget(issues)
        links.addWidget(log)
        layout.addLayout(links)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _selectable(text):
        label = QtWidgets.QLabel(text)
        label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        label.setWordWrap(True)
        return label

    def _show_log(self):
        LogDialog(self).exec_()
