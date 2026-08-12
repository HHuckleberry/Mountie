"""Mountie's main application window."""

import uuid

from gi.repository import Gio
from PyQt5 import QtCore, QtGui, QtWidgets

from mountie import __version__
from mountie.scheduler import DisconnectScheduler
from mountie.session_monitor import SessionMonitor
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
from mountie.settings import (
    CREDENTIAL_ASK,
    CREDENTIAL_PERMANENT,
    CREDENTIAL_SESSION,
    CREDENTIAL_USE_GLOBAL,
    ConfigError,
    THEMES,
    default_config,
    credential_key,
    credential_profile,
    effective_credential_policy,
    export_config,
    load_config,
    load_config_file,
    save_config,
    share_with_credentials,
)
from mountie.ui.about import AboutDialog, CredentialProfilesDialog
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
        self._bulk_queue = []
        self.scheduler = DisconnectScheduler(self)
        self.scheduler.due.connect(self._auto_disconnect)
        self.session_monitor = SessionMonitor(self)
        self.session_monitor.locked.connect(
            lambda: self._disconnect_for_policy("disconnect_on_lock")
        )
        self.session_monitor.suspending.connect(
            lambda: self._disconnect_for_policy("disconnect_on_suspend")
        )
        self.volume_monitor = Gio.VolumeMonitor.get()
        for signal in ("mount-added", "mount-removed", "mount-changed"):
            self.volume_monitor.connect(signal, self._on_mount_inventory_changed)
        self._mount_refresh_timer = QtCore.QTimer(self)
        self._mount_refresh_timer.setSingleShot(True)
        self._mount_refresh_timer.timeout.connect(self.refresh_all_status)

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

        actions_btn = icon_button(
            ["application-menu-symbolic", "application-menu"], "Connection actions"
        )
        actions_menu = QtWidgets.QMenu(self)
        actions_menu.addAction("Connect All", self.connect_all)
        actions_menu.addAction("Disconnect All", self.disconnect_all)
        actions_menu.addSeparator()
        actions_menu.addAction("Credential Profiles…", self.manage_credential_profiles)
        actions_menu.addAction("Export Configuration…", self.export_configuration)
        actions_menu.addAction("Import Configuration…", self.import_configuration)
        actions_btn.setMenu(actions_menu)
        actions_btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        header.addWidget(actions_btn)

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

        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Search shares")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        layout.addWidget(self.search)

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
            self.cfg["credential_policy"],
            self,
        )
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            self.set_credential_policy(dialog.credential_policy.currentData())

    def set_credential_policy(self, policy):
        previous = self.cfg["credential_policy"]
        if policy == previous:
            return
        if policy == CREDENTIAL_PERMANENT:
            answer = QtWidgets.QMessageBox.warning(
                self,
                "Enable permanent password storage?",
                "Passwords will be stored in your desktop's system keyring. They are "
                "never written to Mountie's configuration or logs, but protection "
                "depends on how your keyring is configured and unlocked.\n\n"
                "Remember until logout is the safer convenient option.",
                QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Cancel,
                QtWidgets.QMessageBox.Cancel,
            )
            if answer != QtWidgets.QMessageBox.Save:
                return
        self.cfg["credential_policy"] = policy
        if not self._save_config():
            self.cfg["credential_policy"] = previous
            return
        if policy == CREDENTIAL_PERMANENT:
            return

        failures = []
        cleared = set()
        for share in self.cfg["shares"]:
            profile = credential_profile(self.cfg, share)
            uses_global = (
                profile.get("credential_policy", CREDENTIAL_USE_GLOBAL)
                == CREDENTIAL_USE_GLOBAL
                if profile is not None else
                share.get("credential_policy", CREDENTIAL_USE_GLOBAL)
                == CREDENTIAL_USE_GLOBAL
            )
            secret_id = credential_key(self.cfg, share)
            if not uses_global or secret_id in cleared:
                continue
            try:
                clear_password(secret_id)
                cleared.add(secret_id)
            except CredentialError as error:
                failures.append(f"{share['label']}: {error}")
        for profile in self.cfg["credential_profiles"]:
            if (
                profile.get("credential_policy", CREDENTIAL_USE_GLOBAL)
                != CREDENTIAL_USE_GLOBAL
                or profile["id"] in cleared
            ):
                continue
            try:
                clear_password(profile["id"])
                cleared.add(profile["id"])
            except CredentialError as error:
                failures.append(f"{profile['label']}: {error}")
        if failures:
            QtWidgets.QMessageBox.warning(
                self,
                "Could not remove every password",
                "The new policy is active, but some older entries could not be removed.\n\n"
                + "\n".join(failures),
            )

    def manage_credential_profiles(self):
        dialog = CredentialProfilesDialog(
            self.cfg["credential_profiles"],
            self.cfg["shares"],
            self.cfg["credential_policy"],
            self,
        )
        if dialog.exec_() != QtWidgets.QDialog.Accepted:
            return
        previous = self.cfg["credential_profiles"]
        old_policies = {
            profile["id"]: profile.get("credential_policy", CREDENTIAL_USE_GLOBAL)
            for profile in previous
        }
        enables_permanent = any(
            profile.get("credential_policy") == CREDENTIAL_PERMANENT
            and old_policies.get(profile["id"]) != CREDENTIAL_PERMANENT
            for profile in dialog.profiles
        )
        if enables_permanent:
            answer = QtWidgets.QMessageBox.warning(
                self,
                "Enable permanent password storage?",
                "One or more profiles will store replacement or future passwords in "
                "your desktop's system keyring. Protection depends on how that keyring "
                "is configured and unlocked.",
                QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Cancel,
                QtWidgets.QMessageBox.Cancel,
            )
            if answer != QtWidgets.QMessageBox.Save:
                return
        self.cfg["credential_profiles"] = dialog.profiles
        if not self._save_config():
            self.cfg["credential_profiles"] = previous
            return
        profiles = {profile["id"]: profile for profile in dialog.profiles}
        old_profiles = {profile["id"]: profile for profile in previous}
        failures = []
        for profile_id in dialog.deleted_ids:
            try:
                clear_password(profile_id)
            except CredentialError as error:
                failures.append(str(error))
        for profile_id, profile in profiles.items():
            policy = profile.get("credential_policy", CREDENTIAL_USE_GLOBAL)
            if policy == CREDENTIAL_USE_GLOBAL:
                policy = self.cfg["credential_policy"]
            old_policy = old_profiles.get(profile_id, {}).get(
                "credential_policy", CREDENTIAL_USE_GLOBAL
            )
            if old_policy == CREDENTIAL_USE_GLOBAL:
                old_policy = self.cfg["credential_policy"]
            try:
                if policy == CREDENTIAL_ASK:
                    clear_password(profile_id)
                elif profile_id in dialog.password_updates:
                    set_password(profile_id, dialog.password_updates[profile_id], policy)
                elif policy == CREDENTIAL_SESSION and old_policy == CREDENTIAL_PERMANENT:
                    # Do not leave a permanent secret behind after selecting
                    # the explicitly temporary policy. It will be requested
                    # and stored in the session collection on next connect.
                    clear_password(profile_id)
            except (CredentialError, ValueError) as error:
                failures.append(f"{profile['label']}: {error}")
        if failures:
            QtWidgets.QMessageBox.warning(
                self,
                "Keyring error",
                "Some keyring changes failed:\n\n" + "\n".join(failures),
            )

    def export_configuration(self):
        path, _selected_filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Export Mountie Configuration",
            "mountie-config.json",
            "JSON files (*.json)",
        )
        if not path:
            return
        try:
            export_config(self.cfg, path)
        except ConfigError as error:
            QtWidgets.QMessageBox.critical(self, "Export failed", str(error))
            return
        QtWidgets.QMessageBox.information(
            self,
            "Configuration exported",
            "Share settings and identities were exported. Passwords were not included.",
        )

    def import_configuration(self):
        if self._active_operations:
            QtWidgets.QMessageBox.warning(
                self, "Connection in progress", "Wait for the current operation to finish."
            )
            return
        if any(is_mounted(share) for share in self.cfg["shares"]):
            QtWidgets.QMessageBox.warning(
                self,
                "Disconnect shares first",
                "Disconnect managed shares before replacing the configuration.",
            )
            return
        path, _selected_filter = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Import Mountie Configuration",
            "",
            "JSON files (*.json)",
        )
        if not path:
            return
        try:
            imported = load_config_file(path)
        except ConfigError as error:
            QtWidgets.QMessageBox.critical(self, "Import failed", str(error))
            return
        answer = QtWidgets.QMessageBox.warning(
            self,
            "Replace current configuration?",
            f"This will replace {len(self.cfg['shares'])} configured share(s) with "
            f"{len(imported['shares'])} imported share(s). Passwords are not imported.",
            QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Cancel,
            QtWidgets.QMessageBox.Cancel,
        )
        if answer != QtWidgets.QMessageBox.Save:
            return
        previous = self.cfg
        self.cfg = imported
        if not self._save_config():
            self.cfg = previous
            return
        self.theme.set_mode(self.cfg["theme"])
        self.reload_list(query_status=True)

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
        self._apply_filter()

    def _apply_filter(self):
        query = self.search.text().strip().casefold() if hasattr(self, "search") else ""
        for index in range(self.list.count()):
            item = self.list.item(index)
            widget = self.list.itemWidget(item)
            label = widget.label_lbl.text() if hasattr(widget, "label_lbl") else ""
            target = widget.target_lbl.text() if hasattr(widget, "target_lbl") else ""
            text = f"{label} {target}".casefold()
            item.setHidden(bool(query) and query not in text)

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
            global_credential_policy=self.cfg["credential_policy"],
            credential_profiles=self.cfg["credential_profiles"],
        )
        dialog.setWindowTitle("Import External Share")
        if dialog.exec_() != QtWidgets.QDialog.Accepted:
            return
        values, password = dialog.values()
        created_profile = self._materialize_new_profile(values)
        if created_profile is False:
            return
        if not self._validate_values(values):
            self._discard_profile(created_profile)
            return
        if not self._confirm_permanent_storage(values):
            self._discard_profile(created_profile)
            return
        values["id"] = uuid.uuid4().hex
        self.cfg["shares"].append(values)
        if not self._save_config():
            self.cfg["shares"].remove(values)
            self._discard_profile(created_profile)
            return
        policy = effective_credential_policy(self.cfg, values)
        if password and policy != CREDENTIAL_ASK:
            try:
                set_password(credential_key(self.cfg, values), password, policy)
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
            if mounted:
                if self.scheduler.remaining_seconds(cfg["id"]) is None:
                    self.scheduler.schedule(
                        cfg["id"], cfg.get("disconnect_after_minutes", 0)
                    )
            else:
                self.scheduler.cancel(cfg["id"])

        # connect only after initial state is set, so setChecked() above
        # never itself triggers a mount/unmount action
        card.toggle.toggled.connect(lambda checked, cid=cfg["id"]: self.on_toggle(cid, checked))
        active_operation = self._active_operations.get(cfg["id"])
        if active_operation:
            card.set_operations_enabled(False)
            card.badge.set_status(
                "connecting..." if active_operation == "mount" else "disconnecting..."
            )

    def _materialize_new_profile(self, values):
        name = values.pop("_new_profile_name", "").strip()
        if not name:
            return None
        if values.get("credential_profile_id"):
            return None
        if any(
            profile["label"].casefold() == name.casefold()
            for profile in self.cfg["credential_profiles"]
        ):
            QtWidgets.QMessageBox.warning(
                self, "Duplicate credential profile", "Choose a unique profile name."
            )
            return False
        profile = {
            "id": uuid.uuid4().hex,
            "label": name,
            "username": values.get("username", ""),
            "domain": values.get("domain", ""),
            "credential_policy": values.get(
                "credential_policy", CREDENTIAL_USE_GLOBAL
            ),
        }
        self.cfg["credential_profiles"].append(profile)
        values["credential_profile_id"] = profile["id"]
        return profile

    def _discard_profile(self, profile):
        if profile:
            self.cfg["credential_profiles"].remove(profile)

    def _confirm_permanent_storage(self, values, old_values=None):
        new_policy = effective_credential_policy(self.cfg, values)
        old_policy = (
            effective_credential_policy(self.cfg, old_values)
            if old_values is not None else None
        )
        if (
            new_policy != CREDENTIAL_PERMANENT
            or old_policy == CREDENTIAL_PERMANENT
            or self.cfg["credential_policy"] == CREDENTIAL_PERMANENT
        ):
            return True
        answer = QtWidgets.QMessageBox.warning(
            self,
            "Enable permanent password storage?",
            "This share or profile will store replacement or future passwords in "
            "your desktop's system keyring. Protection depends on how that keyring "
            "is configured and unlocked.",
            QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Cancel,
            QtWidgets.QMessageBox.Cancel,
        )
        return answer == QtWidgets.QMessageBox.Save

    def refresh_all_status(self):
        self.reload_list(query_status=True)

    def _on_mount_inventory_changed(self, *_args):
        # GVfs can emit several signals for one transition. Coalesce them so
        # cards aren't repeatedly destroyed and rebuilt.
        self._mount_refresh_timer.start(250)

    def _auto_disconnect(self, share_id):
        card = self.cards.get(share_id)
        if card is None or share_id in self._active_operations:
            return
        if not card.toggle.isChecked():
            return
        card.toggle.setChecked(False)

    def _disconnect_for_policy(self, field):
        self._start_bulk([
            share["id"] for share in self.cfg["shares"]
            if share.get(field, False)
            and share["id"] in self.cards
            and self.cards[share["id"]].toggle.isChecked()
        ], False)

    def connect_all(self):
        self._start_bulk([
            share["id"] for share in self.cfg["shares"]
            if share["id"] in self.cards and not self.cards[share["id"]].toggle.isChecked()
        ], True)

    def disconnect_all(self):
        self._start_bulk([
            share["id"] for share in self.cfg["shares"]
            if share["id"] in self.cards and self.cards[share["id"]].toggle.isChecked()
        ], False)

    def _start_bulk(self, share_ids, checked):
        if self._bulk_queue or self._active_operations:
            return
        self._bulk_queue = [(share_id, checked) for share_id in share_ids]
        self._run_next_bulk()

    def _run_next_bulk(self):
        if self._active_operations:
            return
        while self._bulk_queue:
            share_id, checked = self._bulk_queue.pop(0)
            card = self.cards.get(share_id)
            if card is None or card.toggle.isChecked() == checked:
                continue
            card.toggle.setChecked(checked)
            return

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
            policy = effective_credential_policy(self.cfg, cfg)
            credential_id = credential_key(self.cfg, cfg)
            resolved_cfg = share_with_credentials(self.cfg, cfg)
            if policy == CREDENTIAL_ASK:
                password = self._prompt_for_password(resolved_cfg)
                if password is None:
                    self._cancel_pending_toggle(share_id, card)
                    return
            else:
                try:
                    password = get_password(credential_id) or ""
                except CredentialError as error:
                    self._cancel_pending_toggle(share_id, card, "keyring error")
                    QtWidgets.QMessageBox.critical(self, "Keyring error", str(error))
                    return
                if not password and (
                    resolved_cfg.get("username") or resolved_cfg.get("domain")
                ):
                    password = self._prompt_for_password(resolved_cfg)
                    if password is None:
                        self._cancel_pending_toggle(share_id, card)
                        return
                    try:
                        set_password(credential_id, password, policy)
                    except (CredentialError, ValueError) as error:
                        self._cancel_pending_toggle(share_id, card, "keyring error")
                        QtWidgets.QMessageBox.critical(self, "Keyring error", str(error))
                        return
            mount_share(resolved_cfg, password,
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
        QtCore.QTimer.singleShot(0, self._run_next_bulk)

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
        if mounted:
            self.scheduler.schedule(
                share_id, share.get("disconnect_after_minutes", 0)
            )
        else:
            self.scheduler.cancel(share_id)
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
        QtCore.QTimer.singleShot(0, self._run_next_bulk)

    # ---- add/edit/delete ----

    def add_share(self):
        last_host = self.cfg["shares"][-1]["host"] if self.cfg["shares"] else ""
        dlg = ShareDialog(
            self,
            default_host=last_host,
            global_credential_policy=self.cfg["credential_policy"],
            credential_profiles=self.cfg["credential_profiles"],
        )
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return
        values, password = dlg.values()
        created_profile = self._materialize_new_profile(values)
        if created_profile is False:
            return
        if not self._validate_values(values):
            self._discard_profile(created_profile)
            return
        if not self._confirm_permanent_storage(values):
            self._discard_profile(created_profile)
            return
        values["id"] = uuid.uuid4().hex
        self.cfg["shares"].append(values)
        if not self._save_config():
            self.cfg["shares"].remove(values)
            self._discard_profile(created_profile)
            return
        policy = effective_credential_policy(self.cfg, values)
        if password and policy != CREDENTIAL_ASK:
            try:
                set_password(credential_key(self.cfg, values), password, policy)
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
            global_credential_policy=self.cfg["credential_policy"],
            credential_profiles=self.cfg["credential_profiles"],
        )
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return
        values, password = dlg.values()
        created_profile = self._materialize_new_profile(values)
        if created_profile is False:
            return
        if not self._validate_values(values, exclude_id=share_id):
            self._discard_profile(created_profile)
            return
        if not self._confirm_permanent_storage(values, cfg):
            self._discard_profile(created_profile)
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
                new=values, secret=password, profile=created_profile:
                self._finish_edit(sid, old, new, secret, profile, ok, status, err),
            )
            return
        self._apply_edit(share_id, old_values, values, password, created_profile)

    def _finish_edit(self, share_id, old_values, values, password, created_profile,
                     success, status, error):
        if not success:
            self._discard_profile(created_profile)
            card = self.cards.get(share_id)
            if card:
                card.set_operations_enabled(True)
            self._on_op_done(share_id, False, status, error)
            return
        self._apply_edit(share_id, old_values, values, password, created_profile)

    def _apply_edit(self, share_id, old_values, values, password, created_profile=None):
        cfg = self._cfg_for_id(share_id)
        if cfg is None:
            return
        old_credential_id = credential_key(self.cfg, old_values)
        old_policy = effective_credential_policy(self.cfg, old_values)
        remove_link(self.cfg, old_values)
        cfg.update(values)
        if not self._save_config():
            cfg.clear()
            cfg.update(old_values)
            self._discard_profile(created_profile)
            self.reload_list(query_status=True)
            return
        policy = effective_credential_policy(self.cfg, cfg)
        new_credential_id = credential_key(self.cfg, cfg)
        if old_credential_id != new_credential_id and old_credential_id == share_id:
            try:
                clear_password(old_credential_id)
            except CredentialError as error:
                QtWidgets.QMessageBox.warning(self, "Keyring error", str(error))
        if policy == CREDENTIAL_ASK:
            try:
                clear_password(new_credential_id)
            except CredentialError as error:
                QtWidgets.QMessageBox.warning(self, "Keyring error", str(error))
        elif (
            policy == CREDENTIAL_SESSION
            and old_policy == CREDENTIAL_PERMANENT
            and new_credential_id == old_credential_id
            and not password
        ):
            try:
                clear_password(new_credential_id)
            except CredentialError as error:
                QtWidgets.QMessageBox.warning(self, "Keyring error", str(error))
        elif password:
            try:
                set_password(new_credential_id, password, policy)
            except CredentialError as error:
                QtWidgets.QMessageBox.critical(self, "Keyring error", str(error))
        self.reload_list(query_status=True)

    def delete_share(self, share_id):
        cfg = self._cfg_for_id(share_id)
        if cfg is None:
            return
        profile_id = cfg.get("credential_profile_id", "")
        detail = (
            " Its shared credential profile will remain available."
            if profile_id else " This removes its saved password too."
        )
        reply = QtWidgets.QMessageBox.question(
            self, "Delete share", f"Delete '{cfg['label']}'?{detail}",
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
        if not cfg.get("credential_profile_id"):
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
