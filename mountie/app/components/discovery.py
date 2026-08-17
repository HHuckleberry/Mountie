"""Network discovery, authentication, and share-selection widgets."""

from urllib.parse import urlsplit

from gi.repository import Gio
from PyQt5 import QtCore, QtGui, QtWidgets

from mountie.discovery import (
    DISCOVERY_ROOT_URI,
    DISCOVERY_SCHEMES,
    authenticate_network_uri_async,
    discover_network_async,
)
from mountie.app.theme import cosmic_tokens


class DiscoveryCredentialsDialog(QtWidgets.QDialog):
    """Collect one-use credentials before asking GVfs to browse a server."""

    def __init__(self, server_name, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Sign In to {server_name}")
        self.setMinimumWidth(460)

        layout = QtWidgets.QVBoxLayout(self)
        summary = QtWidgets.QLabel(
            "Enter the credentials needed to explore this server. They are used "
            "for this connection and are not saved by GVfs. If you choose a share, "
            "they will be copied into Mountie's normal Add Share dialog."
        )
        summary.setTextFormat(QtCore.Qt.PlainText)
        summary.setWordWrap(True)
        summary.setObjectName("settingsDescription")
        layout.addWidget(summary)

        form = QtWidgets.QFormLayout()
        self.domain_edit = QtWidgets.QLineEdit()
        self.domain_edit.setPlaceholderText("Optional")
        self.user_edit = QtWidgets.QLineEdit()
        self.pass_edit = QtWidgets.QLineEdit()
        self.pass_edit.setEchoMode(QtWidgets.QLineEdit.Password)
        form.addRow("Domain / workgroup:", self.domain_edit)
        form.addRow("Username:", self.user_edit)
        form.addRow("Password:", self.pass_edit)
        layout.addLayout(form)

        show_password = QtWidgets.QCheckBox("Show password")
        show_password.toggled.connect(
            lambda visible: self.pass_edit.setEchoMode(
                QtWidgets.QLineEdit.Normal
                if visible else QtWidgets.QLineEdit.Password
            )
        )
        layout.addWidget(show_password)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("Connect")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self):
        return {
            "domain": self.domain_edit.text().strip(),
            "username": self.user_edit.text().strip(),
            "password": self.pass_edit.text(),
        }


class DiscoveryBridge(QtCore.QObject):
    completed = QtCore.pyqtSignal(int, object, str)
    authenticated = QtCore.pyqtSignal(int, str, str, object, str)


class DiscoveryCard(QtWidgets.QFrame):
    import_requested = QtCore.pyqtSignal(dict)
    browse_requested = QtCore.pyqtSignal(str, str)

    def __init__(self, result, credentials=None):
        super().__init__()
        self.result = result
        self._credentials = dict(credentials or {})
        self.setObjectName("shareCard")
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 10, 10)
        layout.setSpacing(12)

        icon_label = QtWidgets.QLabel()
        icon_label.setFixedSize(36, 36)
        icon = QtGui.QIcon()
        for name in result.icon_names:
            candidate = QtGui.QIcon.fromTheme(name)
            if not candidate.isNull():
                icon = candidate
                break
        icon_label.setPixmap(icon.pixmap(32, 32))
        layout.addWidget(icon_label)

        text_col = QtWidgets.QVBoxLayout()
        text_col.setSpacing(2)
        self.label_lbl = QtWidgets.QLabel(result.name)
        self.label_lbl.setTextFormat(QtCore.Qt.PlainText)
        self.label_lbl.setObjectName("shareLabel")
        self.target_lbl = QtWidgets.QLabel(result.uri)
        self.target_lbl.setTextFormat(QtCore.Qt.PlainText)
        self.target_lbl.setObjectName("shareTarget")
        text_col.addWidget(self.label_lbl)
        text_col.addWidget(self.target_lbl)
        layout.addLayout(text_col, 1)

        badge_text = (result.protocol or "network").upper()
        badge = QtWidgets.QLabel(badge_text)
        badge.setAlignment(QtCore.Qt.AlignCenter)
        badge.setProperty("class", "protocolBadge")
        layout.addWidget(badge)

        if result.configured:
            action = QtWidgets.QPushButton("Already Added")
            action.setEnabled(False)
            layout.addWidget(action)
        elif result.kind == "server":
            browse = QtWidgets.QPushButton("Sign In & Browse")
            browse.setToolTip("Enter credentials and list shares exposed by this server")
            browse.clicked.connect(
                lambda: self.browse_requested.emit(result.uri, result.name)
            )
            layout.addWidget(browse)
            add = QtWidgets.QPushButton("Add…")
            add.setToolTip("Enter a share name and credentials for this server")
            add.clicked.connect(
                lambda: self.import_requested.emit(self._import_initial())
            )
            layout.addWidget(add)
        elif result.importable:
            action = QtWidgets.QPushButton("Add to Mountie")
            action.clicked.connect(
                lambda: self.import_requested.emit(self._import_initial())
            )
            layout.addWidget(action)
        else:
            action = QtWidgets.QPushButton("Browse")
            action.setToolTip("Look for shares advertised by this server")
            action.clicked.connect(
                lambda: self.browse_requested.emit(result.uri, result.name)
            )
            layout.addWidget(action)

        r, g, b = cosmic_tokens(self)["secondary"]
        self.target_lbl.setStyleSheet(f"#shareTarget {{ color: rgb({r},{g},{b}); }}")

    def _import_initial(self):
        initial = self.result.initial.copy()
        if self._credentials:
            initial["domain"] = self._credentials.get("domain", "")
            initial["username"] = self._credentials.get("username", "")
            initial["_password"] = self._credentials.get("password", "")
        return initial


class DiscoveryPanel(QtWidgets.QWidget):
    """Browse GVfs advertisements inline, mounting a chosen server only on request."""

    import_requested = QtCore.pyqtSignal(dict)
    DISCOVERY_TIMEOUT_MS = 10000

    def __init__(
            self, configured_shares, parent=None, discover_fn=discover_network_async,
            authenticate_fn=authenticate_network_uri_async):
        super().__init__(parent)
        self._configured_shares = configured_shares
        self._discover_fn = discover_fn
        self._authenticate_fn = authenticate_fn
        self._generation = 0
        self._cancellable = None
        self._current_uri = DISCOVERY_ROOT_URI
        self._history = []
        self._current_credentials = None
        self._timeout_context = "discovery"
        self._timeout_generation = 0
        self._timeout_timer = QtCore.QTimer(self)
        self._timeout_timer.setSingleShot(True)
        self._timeout_timer.timeout.connect(self._on_timeout)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        heading = QtWidgets.QLabel("Advertised Network Shares")
        heading.setObjectName("settingsTitle")
        layout.addWidget(heading)
        summary = QtWidgets.QLabel(
            "Mountie asks your desktop's network service for advertised devices. "
            "It does not scan addresses or connect until you choose a server to browse."
        )
        summary.setObjectName("settingsDescription")
        summary.setWordWrap(True)
        layout.addWidget(summary)

        toolbar = QtWidgets.QHBoxLayout()
        self.back_btn = QtWidgets.QPushButton("Back")
        self.back_btn.setEnabled(False)
        self.back_btn.clicked.connect(self._go_back)
        toolbar.addWidget(self.back_btn)
        self.location_lbl = QtWidgets.QLabel("Network")
        self.location_lbl.setTextFormat(QtCore.Qt.PlainText)
        toolbar.addWidget(self.location_lbl)
        toolbar.addStretch()
        self.refresh_btn = QtWidgets.QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh)
        toolbar.addWidget(self.refresh_btn)
        layout.addLayout(toolbar)

        self.message = QtWidgets.QLabel()
        self.message.setAlignment(QtCore.Qt.AlignCenter)
        self.message.setWordWrap(True)
        layout.addWidget(self.message)

        self.results = QtWidgets.QListWidget()
        self.results.setObjectName("discoveryResults")
        self.results.setFocusPolicy(QtCore.Qt.NoFocus)
        self.results.setSpacing(6)
        layout.addWidget(self.results, 1)

        self.bridge = DiscoveryBridge(self)
        self.bridge.completed.connect(self._on_finished)
        self.bridge.authenticated.connect(self._on_authenticated)
        self.refresh()

    def refresh(self):
        self._start_discovery(self._current_uri)

    def _start_discovery(self, uri):
        if self._cancellable is not None:
            self._cancellable.cancel()
        self._generation += 1
        generation = self._generation
        self._cancellable = Gio.Cancellable()
        cancellable = self._cancellable
        self.results.clear()
        self.results.setEnabled(True)
        self.results.setVisible(False)
        self.message.setText("Looking for advertised network shares…")
        self.message.setVisible(True)
        self.refresh_btn.setEnabled(False)
        self._timeout_generation = generation
        self._timeout_context = "discovery"
        self._timeout_timer.start(self.DISCOVERY_TIMEOUT_MS)

        def completed(found, error):
            self.bridge.completed.emit(generation, found, error)

        try:
            self._discover_fn(
                self._configured_shares, uri, cancellable, completed
            )
        except Exception as unexpected_error:
            completed([], str(unexpected_error))

    def _on_timeout(self):
        if self._timeout_generation != self._generation:
            return
        if self._cancellable is not None:
            self._cancellable.cancel()
        # Make the worker's eventual cancellation result stale before showing
        # the timeout, so it cannot replace this message with an empty state.
        self._generation += 1
        self._timeout_timer.stop()
        self.refresh_btn.setEnabled(True)
        self.results.setEnabled(True)
        if self._timeout_context == "authentication":
            self.message.setText("Connecting to the server timed out. You can try again.")
            self.results.setVisible(self.results.count() > 0)
        else:
            self.message.setText(
                "Network discovery timed out. The desktop service did not respond."
            )
            self.results.setVisible(False)
        self.message.setVisible(True)

    def _on_finished(self, generation, found, error):
        if generation != self._generation:
            return
        self._timeout_timer.stop()
        self.refresh_btn.setEnabled(True)
        if error:
            if "not supported" in error.casefold():
                text = (
                    "Network discovery is unavailable through this desktop's "
                    "GVfs installation. You can still add a share manually."
                )
            elif "not mounted" in error.casefold():
                text = (
                    "This server connection is no longer available. Go Back and "
                    "choose Sign In & Browse to reconnect, or choose Add… if you "
                    "already know the share name."
                )
            else:
                text = f"Could not discover network shares: {error}"
            self.message.setText(text)
            self.message.setVisible(True)
            self.results.setVisible(False)
            return
        if not found:
            self.message.setText("No advertised network shares were found.")
            self.message.setVisible(True)
            self.results.setVisible(False)
            return

        self.message.setVisible(False)
        self.results.setVisible(True)
        for result in found:
            card = DiscoveryCard(result, self._current_credentials)
            card.import_requested.connect(self.import_requested)
            card.browse_requested.connect(self._browse)
            item = QtWidgets.QListWidgetItem(self.results)
            item.setSizeHint(card.sizeHint())
            self.results.addItem(item)
            self.results.setItemWidget(item, card)

    def _browse(self, uri, name):
        if urlsplit(uri).scheme.casefold() not in DISCOVERY_SCHEMES:
            dialog = DiscoveryCredentialsDialog(name, self)
            if dialog.exec_() != QtWidgets.QDialog.Accepted:
                return
            self._authenticate_and_browse(uri, name, dialog.values())
            return
        self._enter_location(uri, name, None)

    def _authenticate_and_browse(self, uri, name, credentials):
        if self._cancellable is not None:
            self._cancellable.cancel()
        self._generation += 1
        generation = self._generation
        self._cancellable = Gio.Cancellable()
        cancellable = self._cancellable
        self.message.setText(f"Connecting to {name}…")
        self.message.setVisible(True)
        self.results.setEnabled(False)
        self.refresh_btn.setEnabled(False)
        self._timeout_generation = generation
        self._timeout_context = "authentication"
        self._timeout_timer.start(self.DISCOVERY_TIMEOUT_MS)

        def authenticated(error):
            self.bridge.authenticated.emit(
                generation, uri, name, credentials, error
            )

        try:
            self._authenticate_fn(uri, credentials, cancellable, authenticated)
        except Exception as unexpected_error:
            authenticated(str(unexpected_error))

    def _on_authenticated(self, generation, uri, name, credentials, error):
        if generation != self._generation:
            return
        self._timeout_timer.stop()
        self.refresh_btn.setEnabled(True)
        self.results.setEnabled(True)
        if error:
            self.message.setText(f"Could not browse {name}: {error}")
            self.message.setVisible(True)
            self.results.setVisible(True)
            return
        self._enter_location(uri, name, dict(credentials))

    def _enter_location(self, uri, name, credentials):
        self._history.append((
            self._current_uri, self.location_lbl.text(), self._current_credentials
        ))
        self._current_uri = uri
        self._current_credentials = credentials
        self.location_lbl.setText(name)
        self.back_btn.setEnabled(True)
        self._start_discovery(uri)

    def _go_back(self):
        if not self._history:
            return
        self._current_uri, label, self._current_credentials = self._history.pop()
        self.location_lbl.setText(label)
        self.back_btn.setEnabled(bool(self._history))
        self._start_discovery(self._current_uri)

    def _cancel_discovery(self):
        self._generation += 1
        self._timeout_timer.stop()
        if self._cancellable is not None:
            self._cancellable.cancel()
        self._current_credentials = None
        self._history.clear()

    def cancel_pending(self):
        """Called by whatever embeds this panel when it goes away, so an
        in-flight GVfs lookup doesn't keep running against a closed dialog."""
        self._cancel_discovery()

    def closeEvent(self, event):
        self._cancel_discovery()
        super().closeEvent(event)
