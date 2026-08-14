import os
import unittest
from unittest import mock

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt5 import QtWidgets

from mountie.ui.about import SbomDialog, SettingsDialog
from mountie.ui.components import ShareDialog


class SettingsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_settings_are_split_into_focused_pages(self):
        dialog = SettingsDialog(3, "session", "dark", 2)
        self.addCleanup(dialog.close)
        self.assertEqual(
            [dialog.navigation.item(row).text() for row in range(4)],
            ["General", "Credentials", "Diagnostics", "About"],
        )
        self.assertEqual(dialog.pages.count(), 4)
        self.assertEqual(dialog.theme.currentData(), "dark")
        self.assertEqual(dialog.credential_policy.currentData(), "session")

    def test_profile_manager_is_parented_to_settings_window(self):
        received = []
        dialog = SettingsDialog(0, manage_profiles=received.append)
        self.addCleanup(dialog.close)
        button = next(
            button for button in dialog.findChildren(QtWidgets.QPushButton)
            if button.text() == "Manage Profiles…"
        )
        button.click()
        self.assertEqual(received, [dialog])

    def test_check_for_updates_checkbox_reflects_current_setting(self):
        dialog = SettingsDialog(0, check_for_updates=False)
        self.addCleanup(dialog.close)
        self.assertFalse(dialog.check_for_updates.isChecked())

    def test_diagnostics_page_opens_sbom_dialog(self):
        dialog = SettingsDialog(0)
        self.addCleanup(dialog.close)
        button = next(
            button for button in dialog.findChildren(QtWidgets.QPushButton)
            if button.text() == "View Software Bill of Materials…"
        )
        with mock.patch.object(SbomDialog, "exec_", return_value=0) as exec_:
            button.click()
        exec_.assert_called_once()


class SbomDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_shows_every_bundled_component(self):
        dialog = SbomDialog()
        self.addCleanup(dialog.close)
        text = dialog.output.toPlainText()
        self.assertIn("libsecret", text)
        self.assertIn("gvfs-client", text)

    def test_missing_bundle_shows_a_message_instead_of_an_empty_view(self):
        with mock.patch("mountie.ui.about.load_sbom", return_value=None):
            dialog = SbomDialog()
        self.addCleanup(dialog.close)
        self.assertFalse(dialog.output.isVisible())


class ShareDialogLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_share_options_are_grouped_into_tabs(self):
        dialog = ShareDialog(global_credential_policy="ask")
        self.addCleanup(dialog.close)
        tabs = dialog.findChild(QtWidgets.QTabWidget, "shareSettingsTabs")
        self.assertIsNotNone(tabs)
        self.assertEqual(
            [tabs.tabText(index) for index in range(tabs.count())],
            ["Connection", "Credentials", "Disconnect"],
        )


if __name__ == "__main__":
    unittest.main()
