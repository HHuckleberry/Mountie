"""Mountie's main application window."""

import uuid

from PyQt5 import QtCore, QtGui, QtWidgets

from mountie import __version__
from mountie.credentials import (
    CredentialError,
    clear_password,
    get_password,
    set_password,
)
from mountie.mounts import (
    external_network_mounts,
    is_mounted,
    link_name_collision,
    mount_share,
    prune_links,
    remove_link,
    unmount_share,
    update_link,
    validate_share,
)
from mountie.settings import ConfigError, THEMES, default_config, load_config, save_config
from mountie.ui.about import AboutDialog
from mountie.ui.components import Bridge, ExternalMountCard, ShareCard, ShareDialog
from mountie.ui.theme import appearance_icon, icon_button, retint_icon_button, tinted_icon


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, theme):
        super().__init__()
        self.setWindowTitle(f"Mountie {__version__}")
        self.resize(560, 420)

        try:
            self.cfg = load_config()
        except ConfigError as error:
            self.cfg = default_config()
            QtWidgets.QMessageBox.critical(self, "Configuration error", str(error))
        self.theme = theme
        # Applied before any widgets exist, so nothing is built against a
        # palette that's about to be replaced.
        self.theme.set_mode(self.cfg["theme"])

        self.bridge = Bridge()
        self.bridge.done.connect(self._on_op_done)
        self.cards = {}  # share_id -> ShareCard
        self.external_cards = []
        self._active_operations = {}  # share_id -> "mount" or "unmount"

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Network Shares")
        title.setObjectName("headerTitle")
        header.addWidget(title)
        version = QtWidgets.QLabel(f"v{__version__}")
        version.setObjectName("versionLabel")
        version.setToolTip("Installed Mountie version")
        header.addWidget(version)
        header.addStretch()

        refresh_btn = icon_button(["view-refresh-symbolic", "view-refresh"], "Refresh status")
        refresh_btn.clicked.connect(self.refresh_all_status)
        header.addWidget(refresh_btn)

        header.addWidget(self._build_theme_button())

        about_btn = icon_button(
            ["help-about-symbolic", "help-about", "preferences-system-symbolic"],
            "About and diagnostics",
        )
        about_btn.clicked.connect(self.show_about)
        header.addWidget(about_btn)

        # Sits on the highlight color, so it tints against that, not the window.
        self.add_btn = add_btn = QtWidgets.QPushButton(" Add Share")
        add_btn.setIcon(tinted_icon(
            self.palette().color(QtGui.QPalette.HighlightedText),
            "list-add-symbolic", "list-add",
        ))
        add_btn.setObjectName("primaryButton")
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

    def show_about(self):
        dialog = AboutDialog(
            len(self.cfg["shares"]),
            self.cfg.get("never_save_credentials", False),
            self,
        )
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            self.set_never_save_credentials(dialog.never_save.isChecked())

    def set_never_save_credentials(self, enabled):
        enabled = bool(enabled)
        previous = self.cfg.get("never_save_credentials", False)
        if enabled == previous:
            return
        self.cfg["never_save_credentials"] = enabled
        if not self._save_config():
            self.cfg["never_save_credentials"] = previous
            return
        if not enabled:
            return

        failures = []
        for share in self.cfg["shares"]:
            try:
                clear_password(share["id"])
            except CredentialError as error:
                failures.append(f"{share['label']}: {error}")
        if failures:
            QtWidgets.QMessageBox.warning(
                self,
                "Could not remove every password",
                "Mountie will not use or save passwords while this setting is enabled.\n\n"
                + "\n".join(failures),
            )

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
        previous_mode = self.cfg["theme"]
        self.cfg["theme"] = mode
        if not self._save_config():
            self.cfg["theme"] = previous_mode
            return
        self.theme.set_mode(mode)

    def _save_config(self):
        try:
            save_config(self.cfg)
            return True
        except ConfigError as error:
            QtWidgets.QMessageBox.critical(self, "Configuration error", str(error))
            return False

    def _validate_values(self, values, exclude_id=None):
        error = validate_share(values)
        if error:
            QtWidgets.QMessageBox.warning(self, "Invalid share", error)
            return False
        if link_name_collision(self.cfg, values, exclude_id):
            QtWidgets.QMessageBox.warning(
                self,
                "Duplicate link name",
                "That label produces the same local link name as another share. "
                "Choose a different label.",
            )
            return False
        return True

    def _on_theme_changed(self):
        # Badges bake their colors into a stylesheet, and icons are pixmaps
        # already painted in the old color - unlike palette-driven widgets,
        # both have to be told to recompute.
        for card in self.cards.values():
            card.refresh_theme()
        for card in self.external_cards:
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
        self.external_cards = []
        for cfg in self.cfg["shares"]:
            self._add_card(cfg, query_status)
        if query_status:
            for connection in external_network_mounts(self.cfg["shares"]):
                self._add_external_card(connection)

    def _add_external_card(self, connection):
        card = ExternalMountCard(connection)
        card.import_requested.connect(self.import_external)
        item = QtWidgets.QListWidgetItem(self.list)
        item.setSizeHint(card.sizeHint())
        self.list.addItem(item)
        self.list.setItemWidget(item, card)
        self.external_cards.append(card)

    def import_external(self, initial):
        dialog = ShareDialog(
            self,
            initial=initial,
            never_save_credentials=self.cfg.get("never_save_credentials", False),
        )
        dialog.setWindowTitle("Import External Share")
        if dialog.exec_() != QtWidgets.QDialog.Accepted:
            return
        values, password = dialog.values()
        if not self._validate_values(values):
            return
        values["id"] = uuid.uuid4().hex
        self.cfg["shares"].append(values)
        if not self._save_config():
            self.cfg["shares"].remove(values)
            return
        if password and not self.cfg.get("never_save_credentials", False):
            try:
                set_password(values["id"], password)
            except CredentialError as error:
                QtWidgets.QMessageBox.critical(self, "Keyring error", str(error))
        self.reload_list(query_status=True)

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
        active_operation = self._active_operations.get(cfg["id"])
        if active_operation:
            card.set_operations_enabled(False)
            card.badge.set_status(
                "connecting..." if active_operation == "mount" else "disconnecting..."
            )

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
        if share_id in self._active_operations:
            return

        self._active_operations[share_id] = "mount" if checked else "unmount"
        card.set_enabled_toggle(False)

        if checked:
            card.badge.set_status("connecting...")
            if self.cfg.get("never_save_credentials", False):
                password = self._prompt_for_password(cfg)
                if password is None:
                    self._cancel_pending_toggle(share_id, card)
                    return
            else:
                try:
                    password = get_password(share_id) or ""
                except CredentialError as error:
                    self._cancel_pending_toggle(share_id, card, "keyring error")
                    QtWidgets.QMessageBox.critical(self, "Keyring error", str(error))
                    return
            mount_share(cfg, password,
                         lambda ok, status, err, sid=share_id:
                         self.bridge.done.emit(sid, ok, status or "", err or ""))
        else:
            card.badge.set_status("disconnecting...")
            unmount_share(cfg,
                          lambda ok, status, err, sid=share_id:
                          self.bridge.done.emit(sid, ok, status or "", err or ""))

    def _prompt_for_password(self, share):
        # An entirely blank identity represents an anonymous/passwordless
        # share, so it should remain one-click even in never-save mode.
        if not share.get("username") and not share.get("domain"):
            return ""
        password, accepted = QtWidgets.QInputDialog.getText(
            self,
            "Password required",
            f"Password for {share['label']}:",
            QtWidgets.QLineEdit.Password,
        )
        return password if accepted else None

    def _cancel_pending_toggle(self, share_id, card, status="disconnected"):
        self._active_operations.pop(share_id, None)
        card.set_enabled_toggle(True)
        card.toggle.blockSignals(True)
        card.toggle.setChecked(False)
        card.toggle.blockSignals(False)
        card.badge.set_status(status)

    def _on_op_done(self, share_id, success, status, error):
        self._active_operations.pop(share_id, None)
        card = self.cards.get(share_id)
        if card is None:
            return
        card.set_enabled_toggle(True)
        share = self._cfg_for_id(share_id)
        if share is None:
            return
        mounted = is_mounted(share)
        card.set_link(update_link(self.cfg, share))
        card.toggle.blockSignals(True)
        card.toggle.setChecked(mounted)
        card.toggle.blockSignals(False)
        if success:
            card.badge.set_status("connected" if mounted else "disconnected")
        else:
            card.badge.set_status(status or "error")
            title, _, detail = error.partition(": ")
            QtWidgets.QMessageBox.critical(
                self, title if detail else "Connection error",
                detail or error or "Unknown error",
            )

    # ---- add/edit/delete ----

    def add_share(self):
        last_host = self.cfg["shares"][-1]["host"] if self.cfg["shares"] else ""
        dlg = ShareDialog(
            self,
            default_host=last_host,
            never_save_credentials=self.cfg.get("never_save_credentials", False),
        )
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return
        values, password = dlg.values()
        if not self._validate_values(values):
            return
        values["id"] = uuid.uuid4().hex
        self.cfg["shares"].append(values)
        if not self._save_config():
            self.cfg["shares"].remove(values)
            return
        if password and not self.cfg.get("never_save_credentials", False):
            try:
                set_password(values["id"], password)
            except CredentialError as error:
                QtWidgets.QMessageBox.critical(self, "Keyring error", str(error))
        self.reload_list(query_status=True)

    def edit_share(self, share_id):
        cfg = self._cfg_for_id(share_id)
        if cfg is None:
            return
        dlg = ShareDialog(
            self,
            existing=cfg,
            never_save_credentials=self.cfg.get("never_save_credentials", False),
        )
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return
        values, password = dlg.values()
        if not self._validate_values(values, exclude_id=share_id):
            return
        old_values = cfg.copy()
        if is_mounted(cfg) and any(cfg.get(key) != value for key, value in values.items()):
            card = self.cards.get(share_id)
            if card:
                card.set_operations_enabled(False)
                card.badge.set_status("disconnecting...")
            unmount_share(
                old_values,
                lambda ok, status, err, sid=share_id, old=old_values,
                new=values, secret=password:
                self._finish_edit(sid, old, new, secret, ok, status, err),
            )
            return
        self._apply_edit(share_id, old_values, values, password)

    def _finish_edit(self, share_id, old_values, values, password, success, status, error):
        if not success:
            card = self.cards.get(share_id)
            if card:
                card.set_operations_enabled(True)
            self._on_op_done(share_id, False, status, error)
            return
        self._apply_edit(share_id, old_values, values, password)

    def _apply_edit(self, share_id, old_values, values, password):
        cfg = self._cfg_for_id(share_id)
        if cfg is None:
            return
        remove_link(self.cfg, old_values)
        cfg.update(values)
        if not self._save_config():
            cfg.clear()
            cfg.update(old_values)
            self.reload_list(query_status=True)
            return
        if password and not self.cfg.get("never_save_credentials", False):
            try:
                set_password(share_id, password)
            except CredentialError as error:
                QtWidgets.QMessageBox.critical(self, "Keyring error", str(error))
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
        card = self.cards.get(share_id)
        if card:
            card.set_operations_enabled(False)
        if is_mounted(cfg):
            unmount_share(
                cfg.copy(),
                lambda ok, status, err, sid=share_id:
                self._finish_delete(sid, ok, status, err),
            )
            return
        self._finish_delete(share_id, True, "", "")

    def _finish_delete(self, share_id, success, status, error):
        if not success:
            card = self.cards.get(share_id)
            if card:
                card.set_operations_enabled(True)
            self._on_op_done(share_id, False, status, error)
            return
        cfg = self._cfg_for_id(share_id)
        if cfg is None:
            return
        previous_shares = self.cfg["shares"]
        self.cfg["shares"] = [c for c in self.cfg["shares"] if c["id"] != share_id]
        if not self._save_config():
            self.cfg["shares"] = previous_shares
            card = self.cards.get(share_id)
            if card:
                card.set_operations_enabled(True)
            return
        try:
            clear_password(share_id)
        except CredentialError as keyring_error:
            self.cfg["shares"] = previous_shares
            self._save_config()
            card = self.cards.get(share_id)
            if card:
                card.set_operations_enabled(True)
            QtWidgets.QMessageBox.critical(self, "Keyring error", str(keyring_error))
            return
        remove_link(self.cfg, cfg)
        self.reload_list(query_status=True)
