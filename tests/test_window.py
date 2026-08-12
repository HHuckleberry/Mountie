import os
import unittest
from unittest import mock

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt5 import QtCore, QtWidgets

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
        }
        patches = (
            mock.patch("mountie.ui.window.load_config", return_value=config),
            mock.patch("mountie.ui.window.prune_links"),
            mock.patch("mountie.ui.window.is_mounted", return_value=False),
            mock.patch("mountie.ui.window.update_link", return_value=None),
        )
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        window = MainWindow(FakeTheme())
        self.addCleanup(window.close)
        return window

    def test_passwordless_share_is_passed_to_gvfs(self):
        window = self.make_window()
        with mock.patch("mountie.ui.window.get_password", return_value=None), \
             mock.patch("mountie.ui.window.mount_share") as mount:
            window.on_toggle("share-id", True)
        mount.assert_called_once()
        self.assertEqual(mount.call_args.args[1], "")

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
