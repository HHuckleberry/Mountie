from PyQt5 import QtCore, QtGui, QtWidgets

from mountie import __version__
from mountie.logging_setup import LOG_PATH, read_log
from mountie.settings import BACKUP_PATH, CONFIG_PATH


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


class AboutDialog(QtWidgets.QDialog):
    def __init__(self, share_count, never_save_credentials=False, parent=None):
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
        self.never_save = QtWidgets.QCheckBox("Never save passwords")
        self.never_save.setChecked(never_save_credentials)
        self.never_save.setToolTip(
            "Ask for passwords when connecting and do not store them in the system keyring"
        )
        security_layout.addWidget(self.never_save)
        explanation = QtWidgets.QLabel(
            "When enabled, existing Mountie passwords are removed from the keyring. "
            "Authenticated shares ask for a password each time they connect."
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
