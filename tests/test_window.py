import os
import unittest
from unittest import mock

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt5 import QtCore, QtGui, QtWidgets

from mountie import __version__
from mountie.app.theme import initialize_icon_theme
from mountie.app.window import MainWindow


SHARE = {
    "id": "share-id",
    "protocol": "smb",
    "label": "Data",
    "host": "server.example",
    "share": "data",
    "username": "",
}


class FakeTheme(QtCore.QObject):
    changed = QtCore.pyqtSignal()

    def set_mode(self, mode):
        self.mode = mode


class WindowLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def make_window(self):
        config = {
            "shares": [SHARE.copy()],
            "credential_profiles": [],
            "theme": "system",
            "link_dir": "~/Shares",
            "links_enabled": False,
            "credential_policy": "permanent",
            # Off so tests never spawn a real background network request;
            # UpdateCheckerTests below cover the check itself.
            "check_for_updates": False,
        }
        patches = (
            mock.patch("mountie.app.window.load_config", return_value=config),
            mock.patch("mountie.app.window.prune_links"),
            mock.patch("mountie.app.window.is_mounted", return_value=False),
            mock.patch("mountie.app.window.update_link", return_value=None),
            mock.patch("mountie.app.window.external_network_mounts", return_value=[]),
        )
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        window = MainWindow(FakeTheme())
        self.addCleanup(window.close)
        return window

    def test_breeze_is_the_icon_fallback(self):
        with mock.patch.object(QtGui.QIcon, "themeName", return_value="hicolor"), \
             mock.patch.object(QtGui.QIcon, "themeSearchPaths", return_value=[":/icons"]), \
             mock.patch.object(QtGui.QIcon, "setThemeSearchPaths") as set_paths, \
             mock.patch.object(QtCore.QStandardPaths, "standardLocations", return_value=["/usr/share"]), \
             mock.patch.object(QtGui.QIcon, "setFallbackThemeName") as fallback, \
             mock.patch.object(QtGui.QIcon, "setThemeName") as active:
            initialize_icon_theme()
        set_paths.assert_called_once_with([":/icons", "/usr/share/icons"])
        fallback.assert_called_once_with("breeze")
        active.assert_called_once_with("breeze")

    def test_desktop_icon_theme_is_not_replaced(self):
        with mock.patch.object(QtGui.QIcon, "themeName", return_value="Papirus"), \
             mock.patch.object(QtGui.QIcon, "themeSearchPaths", return_value=[":/icons"]), \
             mock.patch.object(QtGui.QIcon, "setThemeSearchPaths"), \
             mock.patch.object(QtGui.QIcon, "setFallbackThemeName"), \
             mock.patch.object(QtGui.QIcon, "setThemeName") as active:
            initialize_icon_theme()
        active.assert_not_called()

    def test_passwordless_share_is_passed_to_gvfs(self):
        window = self.make_window()
        with mock.patch("mountie.app.window.get_password", return_value=None), \
             mock.patch("mountie.app.window.mount_share") as mount:
            window.on_toggle("share-id", True)
        mount.assert_called_once()
        self.assertEqual(mount.call_args.args[1], "")

    def test_ask_policy_prompts_without_reading_keyring(self):
        window = self.make_window()
        window.cfg["credential_policy"] = "ask"
        window.cfg["shares"][0]["username"] = "user"
        with mock.patch.object(
            QtWidgets.QInputDialog, "getText", return_value=("temporary", True)
        ), mock.patch("mountie.app.window.get_password") as lookup, \
             mock.patch("mountie.app.window.mount_share") as mount:
            window.on_toggle("share-id", True)
        lookup.assert_not_called()
        self.assertEqual(mount.call_args.args[1], "temporary")

    def test_ask_policy_keeps_passwordless_share_one_click(self):
        window = self.make_window()
        window.cfg["credential_policy"] = "ask"
        with mock.patch.object(QtWidgets.QInputDialog, "getText") as prompt, \
             mock.patch("mountie.app.window.mount_share") as mount:
            window.on_toggle("share-id", True)
        prompt.assert_not_called()
        self.assertEqual(mount.call_args.args[1], "")

    def test_switching_global_policy_to_ask_clears_existing_passwords(self):
        window = self.make_window()
        with mock.patch.object(window, "_save_config", return_value=True), \
             mock.patch("mountie.app.window.clear_password") as clear:
            window.set_credential_policy("ask")
        self.assertEqual(window.cfg["credential_policy"], "ask")
        clear.assert_called_once_with("share-id")

    def test_switching_global_policy_clears_shared_profile_once(self):
        window = self.make_window()
        window.cfg["credential_profiles"] = [{
            "id": "work", "label": "Work", "username": "alice",
            "domain": "ACME", "credential_policy": "global",
        }]
        window.cfg["shares"][0]["credential_profile_id"] = "work"
        second = {**SHARE, "id": "share-two", "label": "Other",
                  "credential_profile_id": "work"}
        window.cfg["shares"].append(second)
        with mock.patch.object(window, "_save_config", return_value=True), \
             mock.patch("mountie.app.window.clear_password") as clear:
            window.set_credential_policy("ask")
        clear.assert_called_once_with("work")

    def test_switching_global_policy_clears_unused_inheriting_profile(self):
        window = self.make_window()
        window.cfg["credential_profiles"] = [{
            "id": "unused", "label": "Unused", "username": "alice",
            "domain": "", "credential_policy": "global",
        }]
        with mock.patch.object(window, "_save_config", return_value=True), \
             mock.patch("mountie.app.window.clear_password") as clear:
            window.set_credential_policy("ask")
        self.assertCountEqual(
            [call.args[0] for call in clear.call_args_list], ["share-id", "unused"]
        )

    def test_profile_permanent_to_session_removes_permanent_secret(self):
        window = self.make_window()
        original = {
            "id": "work", "label": "Work", "username": "alice",
            "domain": "ACME", "credential_policy": "permanent",
        }
        window.cfg["credential_profiles"] = [original]
        window.cfg["shares"][0]["credential_profile_id"] = "work"
        dialog = mock.Mock()
        dialog.exec_.return_value = QtWidgets.QDialog.Accepted
        dialog.profiles = [{**original, "credential_policy": "session"}]
        dialog.password_updates = {}
        dialog.deleted_ids = set()
        with mock.patch(
            "mountie.app.window.CredentialProfilesDialog", return_value=dialog
        ), mock.patch.object(window, "_save_config", return_value=True), \
             mock.patch("mountie.app.window.clear_password") as clear:
            window.manage_credential_profiles()
        clear.assert_called_once_with("work")

    def test_search_filters_by_label_and_target(self):
        window = self.make_window()
        item = window.list.item(0)
        window.search.setText("server.example")
        self.assertFalse(item.isHidden())
        window.search.setText("does-not-exist")
        self.assertTrue(item.isHidden())

    def test_connect_all_runs_mounts_sequentially(self):
        window = self.make_window()
        second = {**SHARE, "id": "share-two", "label": "Other"}
        window.cfg["shares"].append(second)
        window.reload_list(query_status=True)
        callbacks = []
        with mock.patch("mountie.app.window.get_password", return_value=None), \
             mock.patch(
                 "mountie.app.window.mount_share",
                 side_effect=lambda cfg, password, callback: callbacks.append(callback),
             ) as mount:
            window.connect_all()
            self.assertEqual(mount.call_count, 1)
            callbacks.pop(0)(True, "connected", "")
            self.app.processEvents()
            self.assertEqual(mount.call_count, 2)

    def test_version_is_visible_in_main_window(self):
        window = self.make_window()
        version = window.findChild(QtWidgets.QLabel, "versionLabel")
        self.assertIsNotNone(version)
        self.assertEqual(version.text(), f"v{__version__}")
        self.assertIn(__version__, window.windowTitle())

    def test_settings_dialog_applies_policy_and_theme(self):
        window = self.make_window()
        dialog = mock.Mock()
        dialog.exec_.return_value = QtWidgets.QDialog.Accepted
        dialog.credential_policy.currentData.return_value = "session"
        dialog.theme.currentData.return_value = "dark"
        dialog.check_for_updates.isChecked.return_value = False
        with mock.patch("mountie.app.window.SettingsDialog", return_value=dialog), \
             mock.patch.object(
                 window, "set_credential_policy", return_value=True
             ) as set_policy, mock.patch.object(window, "set_theme") as set_theme, \
             mock.patch.object(window, "set_check_for_updates") as set_updates:
            window.show_settings()
        set_policy.assert_called_once_with("session")
        set_theme.assert_called_once_with("dark")
        set_updates.assert_called_once_with(False)

    def test_settings_button_replaces_separate_appearance_button(self):
        window = self.make_window()
        button = window.findChild(QtWidgets.QToolButton, "settingsButton")
        self.assertIsNotNone(button)
        self.assertEqual(button.toolTip(), "Settings")

    def test_add_share_offers_configured_shares_to_the_dialog(self):
        window = self.make_window()
        dialog = mock.Mock()
        dialog.exec_.return_value = QtWidgets.QDialog.Rejected
        with mock.patch("mountie.app.window.ShareDialog", return_value=dialog) as factory:
            window.add_share()
        _args, kwargs = factory.call_args
        self.assertEqual(kwargs["configured_shares"], window.cfg["shares"])

    def test_refresh_shows_external_mount_as_read_only(self):
        window = self.make_window()
        connection = {"name": "Other share", "uri": "smb://other/data"}
        with mock.patch(
            "mountie.app.window.external_network_mounts", return_value=[connection]
        ):
            window.refresh_all_status()
        self.assertEqual(len(window.external_cards), 1)
        card = window.external_cards[0]
        self.assertEqual(card.badge.text(), "external")
        self.assertEqual(card.target_lbl.text(), "smb://other/data")
        self.assertFalse(hasattr(card, "edit_btn"))

    def test_import_external_creates_managed_share(self):
        window = self.make_window()
        initial = {
            "protocol": "smb",
            "label": "Other",
            "host": "other.example",
            "share": "media",
            "domain": "",
            "username": "user",
        }
        dialog = mock.Mock()
        dialog.exec_.return_value = QtWidgets.QDialog.Accepted
        dialog.values.return_value = (initial.copy(), "password")
        with mock.patch("mountie.app.window.ShareDialog", return_value=dialog) as factory, \
             mock.patch.object(window, "_save_config", return_value=True), \
             mock.patch.object(window, "reload_list"), \
             mock.patch("mountie.app.window.set_password") as store:
            window.import_external(initial)
        self.assertEqual(len(window.cfg["shares"]), 2)
        imported = window.cfg["shares"][-1]
        self.assertEqual(imported["host"], "other.example")
        self.assertIn("id", imported)
        factory.assert_called_once()
        store.assert_called_once_with(imported["id"], "password", "permanent")

    def test_new_profile_is_reused_as_credential_key(self):
        window = self.make_window()
        values = {
            "protocol": "smb", "label": "Other", "host": "other.example",
            "share": "media", "domain": "ACME", "username": "alice",
            "credential_policy": "session", "credential_profile_id": "",
            "disconnect_after_minutes": 0, "_new_profile_name": "Work",
        }
        dialog = mock.Mock()
        dialog.exec_.return_value = QtWidgets.QDialog.Accepted
        dialog.values.return_value = (values, "password")
        with mock.patch("mountie.app.window.ShareDialog", return_value=dialog), \
             mock.patch.object(window, "_save_config", return_value=True), \
             mock.patch.object(window, "reload_list"), \
             mock.patch("mountie.app.window.set_password") as store:
            window.import_external({})
        profile = window.cfg["credential_profiles"][0]
        imported = window.cfg["shares"][-1]
        self.assertEqual(imported["credential_profile_id"], profile["id"])
        store.assert_called_once_with(profile["id"], "password", "session")

    def test_edit_and_delete_actions_exist(self):
        window = self.make_window()
        card = window.cards["share-id"]
        self.assertTrue(card.edit_btn.isEnabled())
        self.assertTrue(card.delete_btn.isEnabled())
        self.assertEqual(card.edit_btn.cursor().shape(), QtCore.Qt.ArrowCursor)
        self.assertEqual(card.delete_btn.cursor().shape(), QtCore.Qt.ArrowCursor)

    def test_failed_unmount_restores_actual_mounted_state(self):
        window = self.make_window()
        with mock.patch("mountie.app.window.is_mounted", return_value=True), \
             mock.patch("mountie.app.window.update_link", return_value=None), \
             mock.patch.object(QtWidgets.QMessageBox, "critical"):
            window._on_op_done(
                "share-id", False, "unmount failed", "Could not disconnect: busy"
            )
        self.assertTrue(window.cards["share-id"].toggle.isChecked())

    def test_delete_waits_for_successful_unmount(self):
        window = self.make_window()
        callback = None

        def capture_unmount(config, on_done):
            nonlocal callback
            callback = on_done

        with mock.patch("mountie.app.window.is_mounted", return_value=True), \
             mock.patch("mountie.app.window.unmount_share", side_effect=capture_unmount), \
             mock.patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.Yes), \
             mock.patch.object(QtWidgets.QMessageBox, "critical"):
            window.delete_share("share-id")
            self.assertEqual(len(window.cfg["shares"]), 1)
            callback(False, "unmount failed", "Could not disconnect: busy")
        self.assertEqual(len(window.cfg["shares"]), 1)

    def test_delete_removes_share_only_after_successful_unmount(self):
        window = self.make_window()
        callbacks = []
        with mock.patch("mountie.app.window.is_mounted", return_value=True), \
             mock.patch("mountie.app.window.unmount_share", side_effect=lambda cfg, cb: callbacks.append(cb)), \
             mock.patch("mountie.app.window.clear_password"), \
             mock.patch.object(window, "_save_config", return_value=True), \
             mock.patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.Yes):
            window.delete_share("share-id")
            self.assertEqual(len(window.cfg["shares"]), 1)
            callbacks[0](True, None, None)
        self.assertEqual(window.cfg["shares"], [])

    def test_update_check_runs_on_startup_when_enabled(self):
        config = {
            "shares": [], "credential_profiles": [], "theme": "system",
            "link_dir": "~/Shares", "links_enabled": False,
            "credential_policy": "permanent", "check_for_updates": True,
        }
        with mock.patch("mountie.app.window.load_config", return_value=config), \
             mock.patch("mountie.app.window.prune_links"), \
             mock.patch("mountie.app.window.is_mounted", return_value=False), \
             mock.patch("mountie.app.window.update_link", return_value=None), \
             mock.patch("mountie.app.window.external_network_mounts", return_value=[]), \
             mock.patch("mountie.app.window.UpdateChecker") as checker_cls:
            window = MainWindow(FakeTheme())
            self.addCleanup(window.close)
        checker_cls.return_value.check.assert_called_once_with(__version__)

    def test_update_check_skipped_on_startup_when_disabled(self):
        with mock.patch("mountie.app.window.UpdateChecker") as checker_cls:
            self.make_window()
        checker_cls.assert_not_called()

    def test_update_banner_shown_when_a_release_is_available(self):
        window = self.make_window()
        self.assertFalse(window.update_banner.isVisibleTo(window))
        window._on_update_check_done({"version": "9.9.9", "url": "https://example.com/r"})
        self.assertTrue(window.update_banner.isVisibleTo(window))
        self.assertIn("9.9.9", window.update_banner_label.text())

    def test_update_banner_stays_hidden_when_already_current(self):
        window = self.make_window()
        window._on_update_check_done(None)
        self.assertFalse(window.update_banner.isVisibleTo(window))

    def test_dismissing_the_update_banner_hides_it(self):
        window = self.make_window()
        window._on_update_check_done({"version": "9.9.9", "url": "https://example.com/r"})
        dismiss = next(
            button for button in window.update_banner.findChildren(QtWidgets.QToolButton)
            if button.toolTip() == "Dismiss"
        )
        dismiss.click()
        self.assertFalse(window.update_banner.isVisibleTo(window))

    def test_view_release_opens_the_release_url(self):
        window = self.make_window()
        window._on_update_check_done({"version": "9.9.9", "url": "https://example.com/r"})
        with mock.patch.object(QtGui.QDesktopServices, "openUrl") as open_url:
            window._open_update_release()
        self.assertEqual(open_url.call_args.args[0].toString(), "https://example.com/r")

    def test_settings_dialog_applies_check_for_updates_toggle(self):
        window = self.make_window()
        dialog = mock.Mock()
        dialog.exec_.return_value = QtWidgets.QDialog.Accepted
        dialog.credential_policy.currentData.return_value = window.cfg["credential_policy"]
        dialog.theme.currentData.return_value = window.cfg["theme"]
        dialog.check_for_updates.isChecked.return_value = True
        with mock.patch("mountie.app.window.SettingsDialog", return_value=dialog), \
             mock.patch.object(window, "set_check_for_updates") as set_updates:
            window.show_settings()
        set_updates.assert_called_once_with(True)

    def test_mounted_edit_waits_for_unmount(self):
        window = self.make_window()
        callbacks = []
        values = {**SHARE, "host": "new-server.example"}
        values.pop("id")
        dialog = mock.Mock()
        dialog.exec_.return_value = QtWidgets.QDialog.Accepted
        dialog.values.return_value = (values, "")
        with mock.patch("mountie.app.window.ShareDialog", return_value=dialog), \
             mock.patch("mountie.app.window.is_mounted", return_value=True), \
             mock.patch("mountie.app.window.unmount_share", side_effect=lambda cfg, cb: callbacks.append(cb)), \
             mock.patch.object(window, "_save_config", return_value=True), \
             mock.patch.object(window, "reload_list"):
            window.edit_share("share-id")
            self.assertEqual(window.cfg["shares"][0]["host"], "server.example")
            callbacks[0](True, None, None)
        self.assertEqual(window.cfg["shares"][0]["host"], "new-server.example")


if __name__ == "__main__":
    unittest.main()
