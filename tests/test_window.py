import os
import unittest
from unittest import mock

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt5 import QtCore, QtGui, QtWidgets

from mountie.ui.theme import initialize_icon_theme
from mountie.ui.window import MainWindow


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
            "theme": "system",
            "link_dir": "~/Shares",
            "links_enabled": False,
            "never_save_credentials": False,
        }
        patches = (
            mock.patch("mountie.ui.window.load_config", return_value=config),
            mock.patch("mountie.ui.window.prune_links"),
            mock.patch("mountie.ui.window.is_mounted", return_value=False),
            mock.patch("mountie.ui.window.update_link", return_value=None),
            mock.patch("mountie.ui.window.external_network_mounts", return_value=[]),
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
        with mock.patch("mountie.ui.window.get_password", return_value=None), \
             mock.patch("mountie.ui.window.mount_share") as mount:
            window.on_toggle("share-id", True)
        mount.assert_called_once()
        self.assertEqual(mount.call_args.args[1], "")

    def test_never_save_prompts_without_reading_keyring(self):
        window = self.make_window()
        window.cfg["never_save_credentials"] = True
        window.cfg["shares"][0]["username"] = "user"
        with mock.patch.object(
            QtWidgets.QInputDialog, "getText", return_value=("temporary", True)
        ), mock.patch("mountie.ui.window.get_password") as lookup, \
             mock.patch("mountie.ui.window.mount_share") as mount:
            window.on_toggle("share-id", True)
        lookup.assert_not_called()
        self.assertEqual(mount.call_args.args[1], "temporary")

    def test_never_save_keeps_passwordless_share_one_click(self):
        window = self.make_window()
        window.cfg["never_save_credentials"] = True
        with mock.patch.object(QtWidgets.QInputDialog, "getText") as prompt, \
             mock.patch("mountie.ui.window.mount_share") as mount:
            window.on_toggle("share-id", True)
        prompt.assert_not_called()
        self.assertEqual(mount.call_args.args[1], "")

    def test_enabling_never_save_clears_existing_passwords(self):
        window = self.make_window()
        with mock.patch.object(window, "_save_config", return_value=True), \
             mock.patch("mountie.ui.window.clear_password") as clear:
            window.set_never_save_credentials(True)
        self.assertTrue(window.cfg["never_save_credentials"])
        clear.assert_called_once_with("share-id")

    def test_version_is_visible_in_main_window(self):
        window = self.make_window()
        version = window.findChild(QtWidgets.QLabel, "versionLabel")
        self.assertIsNotNone(version)
        self.assertEqual(version.text(), "v0.1.3")
        self.assertIn("0.1.3", window.windowTitle())

    def test_refresh_shows_external_mount_as_read_only(self):
        window = self.make_window()
        connection = {"name": "Other share", "uri": "smb://other/data"}
        with mock.patch(
            "mountie.ui.window.external_network_mounts", return_value=[connection]
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
        with mock.patch("mountie.ui.window.ShareDialog", return_value=dialog) as factory, \
             mock.patch.object(window, "_save_config", return_value=True), \
             mock.patch.object(window, "reload_list"), \
             mock.patch("mountie.ui.window.set_password") as store:
            window.import_external(initial)
        self.assertEqual(len(window.cfg["shares"]), 2)
        imported = window.cfg["shares"][-1]
        self.assertEqual(imported["host"], "other.example")
        self.assertIn("id", imported)
        factory.assert_called_once()
        store.assert_called_once_with(imported["id"], "password")

    def test_edit_and_delete_actions_exist(self):
        window = self.make_window()
        card = window.cards["share-id"]
        self.assertTrue(card.edit_btn.isEnabled())
        self.assertTrue(card.delete_btn.isEnabled())
        self.assertEqual(card.edit_btn.cursor().shape(), QtCore.Qt.ArrowCursor)
        self.assertEqual(card.delete_btn.cursor().shape(), QtCore.Qt.ArrowCursor)

    def test_failed_unmount_restores_actual_mounted_state(self):
        window = self.make_window()
        with mock.patch("mountie.ui.window.is_mounted", return_value=True), \
             mock.patch("mountie.ui.window.update_link", return_value=None), \
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

        with mock.patch("mountie.ui.window.is_mounted", return_value=True), \
             mock.patch("mountie.ui.window.unmount_share", side_effect=capture_unmount), \
             mock.patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.Yes), \
             mock.patch.object(QtWidgets.QMessageBox, "critical"):
            window.delete_share("share-id")
            self.assertEqual(len(window.cfg["shares"]), 1)
            callback(False, "unmount failed", "Could not disconnect: busy")
        self.assertEqual(len(window.cfg["shares"]), 1)

    def test_delete_removes_share_only_after_successful_unmount(self):
        window = self.make_window()
        callbacks = []
        with mock.patch("mountie.ui.window.is_mounted", return_value=True), \
             mock.patch("mountie.ui.window.unmount_share", side_effect=lambda cfg, cb: callbacks.append(cb)), \
             mock.patch("mountie.ui.window.clear_password"), \
             mock.patch.object(window, "_save_config", return_value=True), \
             mock.patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.Yes):
            window.delete_share("share-id")
            self.assertEqual(len(window.cfg["shares"]), 1)
            callbacks[0](True, None, None)
        self.assertEqual(window.cfg["shares"], [])

    def test_mounted_edit_waits_for_unmount(self):
        window = self.make_window()
        callbacks = []
        values = {**SHARE, "host": "new-server.example"}
        values.pop("id")
        dialog = mock.Mock()
        dialog.exec_.return_value = QtWidgets.QDialog.Accepted
        dialog.values.return_value = (values, "")
        with mock.patch("mountie.ui.window.ShareDialog", return_value=dialog), \
             mock.patch("mountie.ui.window.is_mounted", return_value=True), \
             mock.patch("mountie.ui.window.unmount_share", side_effect=lambda cfg, cb: callbacks.append(cb)), \
             mock.patch.object(window, "_save_config", return_value=True), \
             mock.patch.object(window, "reload_list"):
            window.edit_share("share-id")
            self.assertEqual(window.cfg["shares"][0]["host"], "server.example")
            callbacks[0](True, None, None)
        self.assertEqual(window.cfg["shares"][0]["host"], "new-server.example")


if __name__ == "__main__":
    unittest.main()
