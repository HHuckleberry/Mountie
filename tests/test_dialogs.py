import os
import unittest
from unittest import mock

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt5 import QtCore, QtWidgets

from mountie.discovery import DiscoveryResult
from mountie.settings import BACKEND_GVFS, BACKEND_NATIVE, SHARE_PRESETS
from mountie.app.settings import SbomDialog, SettingsDialog
from mountie.app import settings as app_settings
from mountie.app.components import (
    DiscoveryCard,
    DiscoveryCredentialsDialog,
    DiscoveryPanel,
    ShareDialog,
)


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

    def test_native_mount_status_reflects_whether_the_helper_is_installed(self):
        with mock.patch.object(app_settings.native_mount, "is_helper_installed", return_value=True):
            installed_dialog = SettingsDialog(0)
        self.addCleanup(installed_dialog.close)
        self.assertIn("Installed", installed_dialog.native_status_lbl.text())
        self.assertTrue(installed_dialog.native_setup_btn.isVisibleTo(installed_dialog))
        self.assertIn("removal", installed_dialog.native_setup_btn.text())

        with mock.patch.object(app_settings.native_mount, "is_helper_installed", return_value=False):
            missing_dialog = SettingsDialog(0)
        self.addCleanup(missing_dialog.close)
        self.assertIn("Not set up", missing_dialog.native_status_lbl.text())
        self.assertTrue(missing_dialog.native_setup_btn.isVisibleTo(missing_dialog))

    def test_show_setup_command_exports_and_displays_the_sudo_command(self):
        with mock.patch.object(app_settings.native_mount, "is_helper_installed", return_value=False):
            dialog = SettingsDialog(0)
        self.addCleanup(dialog.close)
        with mock.patch.object(
            app_settings.native_mount, "export_installer_for_host",
            return_value=(mock.Mock(), "/home/alice/.var/app/io.github.HHuckleberry.Mountie/data/mountie/install-native-mount-helper.sh"),
        ) as export:
            dialog.native_setup_btn.click()
        export.assert_called_once()
        self.assertEqual(
            dialog.native_command_edit.text(),
            "sudo bash -- /home/alice/.var/app/io.github.HHuckleberry.Mountie/data/mountie/install-native-mount-helper.sh",
        )
        self.assertTrue(dialog.native_command_edit.isVisibleTo(dialog))
        self.assertTrue(dialog.native_copy_btn.isVisibleTo(dialog))
        self.assertFalse(dialog.native_setup_btn.isVisibleTo(dialog))

    def test_show_setup_command_reports_export_failure_without_crashing(self):
        with mock.patch.object(app_settings.native_mount, "is_helper_installed", return_value=False):
            dialog = SettingsDialog(0)
        self.addCleanup(dialog.close)
        with mock.patch.object(
            app_settings.native_mount, "export_installer_for_host",
            side_effect=OSError("permission denied"),
        ), mock.patch.object(QtWidgets.QMessageBox, "critical") as critical:
            dialog.native_setup_btn.click()
        critical.assert_called_once()
        self.assertFalse(dialog.native_command_edit.isVisibleTo(dialog))

    def test_copy_button_puts_the_command_on_the_clipboard(self):
        with mock.patch.object(app_settings.native_mount, "is_helper_installed", return_value=False):
            dialog = SettingsDialog(0)
        self.addCleanup(dialog.close)
        with mock.patch.object(
            app_settings.native_mount, "export_installer_for_host",
            return_value=(mock.Mock(), "/tmp/install-native-mount-helper.sh"),
        ):
            dialog.native_setup_btn.click()
        dialog.native_copy_btn.click()
        self.assertEqual(
            QtWidgets.QApplication.clipboard().text(),
            "sudo bash -- /tmp/install-native-mount-helper.sh",
        )

    def test_setup_command_shell_quotes_the_exported_path(self):
        with mock.patch.object(app_settings.native_mount, "is_helper_installed", return_value=False):
            dialog = SettingsDialog(0)
        self.addCleanup(dialog.close)
        with mock.patch.object(
            app_settings.native_mount, "export_installer_for_host",
            return_value=(mock.Mock(), "/tmp/data; touch owned/installer.sh"),
        ):
            dialog.native_setup_btn.click()
        self.assertEqual(
            dialog.native_command_edit.text(),
            "sudo bash -- '/tmp/data; touch owned/installer.sh'",
        )

    def test_installed_helper_offers_an_uninstall_command(self):
        with mock.patch.object(app_settings.native_mount, "is_helper_installed", return_value=True):
            dialog = SettingsDialog(0)
        self.addCleanup(dialog.close)
        with mock.patch.object(
            app_settings.native_mount, "export_installer_for_host",
            return_value=(mock.Mock(), "/tmp/install-native-mount-helper.sh"),
        ):
            dialog.native_setup_btn.click()
        self.assertEqual(
            dialog.native_command_edit.text(),
            "sudo bash -- /tmp/install-native-mount-helper.sh --uninstall",
        )


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
        with mock.patch("mountie.app.settings.load_sbom", return_value=None):
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
            ["Connection", "Discover", "Credentials", "Disconnect"],
        )

    def test_discover_tab_is_absent_when_editing_or_prefilled(self):
        for dialog in (
            ShareDialog(existing={"label": "Existing"}, global_credential_policy="ask"),
            ShareDialog(initial={"host": "nas.local"}, global_credential_policy="ask"),
        ):
            self.addCleanup(dialog.close)
            self.assertIsNone(dialog.discovery_panel)
            tabs = dialog.findChild(QtWidgets.QTabWidget, "shareSettingsTabs")
            self.assertNotIn("Discover", [tabs.tabText(i) for i in range(tabs.count())])

    def test_backend_combo_defaults_to_gvfs_and_is_enabled_for_smb(self):
        dialog = ShareDialog(global_credential_policy="ask")
        self.addCleanup(dialog.close)
        self.assertEqual(dialog.backend_combo.currentData(), BACKEND_GVFS)
        self.assertTrue(dialog.backend_combo.isEnabled())

    def test_backend_combo_is_disabled_and_reset_for_non_smb_protocol(self):
        dialog = ShareDialog(
            existing={
                "label": "Share", "host": "server", "share": "data",
                "protocol": "smb", "backend": BACKEND_NATIVE,
            },
            global_credential_policy="ask",
        )
        self.addCleanup(dialog.close)
        self.assertEqual(dialog.backend_combo.currentData(), BACKEND_NATIVE)
        dialog.protocol_combo.setCurrentIndex(dialog.protocol_combo.findData("nfs"))
        self.assertEqual(dialog.backend_combo.currentData(), BACKEND_GVFS)
        self.assertFalse(dialog.backend_combo.isEnabled())

    def test_values_includes_backend(self):
        dialog = ShareDialog(global_credential_policy="ask")
        self.addCleanup(dialog.close)
        dialog.backend_combo.setCurrentIndex(dialog.backend_combo.findData(BACKEND_NATIVE))
        values, _password = dialog.values()
        self.assertEqual(values["backend"], BACKEND_NATIVE)

    def test_discovery_password_is_transiently_prefilled(self):
        dialog = ShareDialog(
            initial={"_password": "temporary-secret"},
            global_credential_policy="permanent",
        )
        self.addCleanup(dialog.close)
        values, password = dialog.values()
        self.assertEqual(password, "temporary-secret")
        self.assertNotIn("_password", values)

    def test_template_picker_offers_every_preset_on_a_blank_dialog(self):
        dialog = ShareDialog(global_credential_policy="ask")
        self.addCleanup(dialog.close)
        self.assertIsNotNone(dialog.template_combo)
        self.assertEqual(
            [dialog.template_combo.itemText(i) for i in range(dialog.template_combo.count())],
            ["Custom"] + [preset["menu_label"] for preset in SHARE_PRESETS],
        )

    def test_template_picker_is_absent_when_editing(self):
        dialog = ShareDialog(existing={"label": "Existing"}, global_credential_policy="ask")
        self.addCleanup(dialog.close)
        self.assertIsNone(dialog.template_combo)

    def test_template_picker_is_absent_when_prefilled_from_discovery(self):
        dialog = ShareDialog(initial={"host": "nas.local"}, global_credential_policy="ask")
        self.addCleanup(dialog.close)
        self.assertIsNone(dialog.template_combo)

    def test_choosing_a_template_prefills_its_declared_fields(self):
        for preset in SHARE_PRESETS:
            with self.subTest(preset=preset["key"]):
                dialog = ShareDialog(global_credential_policy="ask")
                self.addCleanup(dialog.close)
                index = dialog.template_combo.findData(preset["key"])
                dialog.template_combo.setCurrentIndex(index)
                values, _password = dialog.values()
                for field, expected in preset["initial"].items():
                    self.assertEqual(values[field], expected)

    def test_switching_back_to_custom_does_not_revert_a_chosen_template(self):
        # "Custom" is a no-op entry, not a reset button — once a template has
        # filled in fields, picking "Custom" again leaves them as they are.
        dialog = ShareDialog(global_credential_policy="ask")
        self.addCleanup(dialog.close)
        dialog.template_combo.setCurrentIndex(
            dialog.template_combo.findData(SHARE_PRESETS[0]["key"])
        )
        dialog.template_combo.setCurrentIndex(dialog.template_combo.findData(None))
        self.assertEqual(dialog.label_edit.text(), SHARE_PRESETS[0]["initial"]["label"])

    def test_discover_tab_gets_the_configured_shares_list(self):
        configured = [{"id": "existing", "host": "nas.local"}]
        dialog = ShareDialog(global_credential_policy="ask", configured_shares=configured)
        self.addCleanup(dialog.close)
        self.assertIsInstance(dialog.discovery_panel, DiscoveryPanel)
        self.assertEqual(dialog.discovery_panel._configured_shares, configured)

    def test_a_discovered_share_fills_in_connection_fields_and_switches_tabs(self):
        found = {
            "protocol": "sftp", "label": "Found Share", "host": "nas.local",
            "share": "data", "domain": "WORK", "username": "alice",
            "_password": "secret",
        }
        dialog = ShareDialog(global_credential_policy="ask")
        self.addCleanup(dialog.close)
        dialog.tabs.setCurrentIndex(dialog.tabs.indexOf(dialog.discovery_panel))

        dialog.discovery_panel.import_requested.emit(found)

        values, password = dialog.values()
        self.assertEqual(values["protocol"], "sftp")
        self.assertEqual(values["label"], "Found Share")
        self.assertEqual(values["host"], "nas.local")
        self.assertEqual(values["share"], "data")
        self.assertEqual(values["domain"], "WORK")
        self.assertEqual(values["username"], "alice")
        self.assertEqual(password, "secret")
        self.assertEqual(dialog.tabs.currentWidget(), dialog.tabs.widget(0))

    def test_closing_the_dialog_cancels_any_pending_discovery(self):
        dialog = ShareDialog(global_credential_policy="ask")
        self.addCleanup(dialog.close)
        with mock.patch.object(dialog.discovery_panel, "cancel_pending") as cancel:
            dialog.reject()
        cancel.assert_called_once()


class DiscoveryPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    @staticmethod
    def result(**overrides):
        values = {
            "key": "smb|nas.local|data",
            "name": "NAS Data",
            "uri": "smb://nas.local/data",
            "protocol": "smb",
            "host": "nas.local",
            "path": "data",
            "kind": "share",
            "icon_names": ("network-server-symbolic",),
            "initial": {
                "protocol": "smb", "label": "NAS Data", "host": "nas.local",
                "share": "data", "domain": "", "username": "",
            },
        }
        values.update(overrides)
        return DiscoveryResult(**values)

    def test_discovery_card_imports_prefilled_share(self):
        received = []
        card = DiscoveryCard(self.result())
        self.addCleanup(card.close)
        card.import_requested.connect(received.append)
        button = next(
            button for button in card.findChildren(QtWidgets.QPushButton)
            if button.text() == "Add to Mountie"
        )
        button.click()
        self.assertEqual(received[0]["host"], "nas.local")
        self.assertEqual(received[0]["share"], "data")

    def test_server_card_requires_browse_action(self):
        received = []
        card = DiscoveryCard(self.result(
            key="smb|nas.local|", uri="smb://nas.local/", path="",
            kind="server", initial={
                "protocol": "smb", "label": "NAS Data", "host": "nas.local",
                "share": "", "domain": "", "username": "",
            },
        ))
        self.addCleanup(card.close)
        card.browse_requested.connect(lambda uri, name: received.append((uri, name)))
        button = next(
            button for button in card.findChildren(QtWidgets.QPushButton)
            if button.text() == "Sign In & Browse"
        )
        button.click()
        self.assertEqual(received, [("smb://nas.local/", "NAS Data")])

    def test_server_card_can_prefill_manual_add(self):
        received = []
        card = DiscoveryCard(self.result(
            key="smb|nas.local|", uri="smb://nas.local/", path="",
            kind="server", initial={
                "protocol": "smb", "label": "NAS", "host": "nas.local",
                "share": "", "domain": "", "username": "",
            },
        ))
        self.addCleanup(card.close)
        card.import_requested.connect(received.append)
        button = next(
            button for button in card.findChildren(QtWidgets.QPushButton)
            if button.text() == "Add…"
        )
        button.click()
        self.assertEqual(received[0]["host"], "nas.local")
        self.assertEqual(received[0]["share"], "")

    def test_credentials_dialog_returns_domain_username_and_password(self):
        dialog = DiscoveryCredentialsDialog("NAS")
        self.addCleanup(dialog.close)
        dialog.domain_edit.setText(" WORKGROUP ")
        dialog.user_edit.setText(" alice ")
        dialog.pass_edit.setText(" secret ")
        self.assertEqual(dialog.pass_edit.echoMode(), QtWidgets.QLineEdit.Password)
        self.assertEqual(dialog.values(), {
            "domain": "WORKGROUP",
            "username": "alice",
            "password": " secret ",
        })

    def test_authenticated_server_browse_carries_credentials_to_selected_share(self):
        server = self.result(
            key="smb|nas.local|", name="NAS", uri="smb://nas.local/", path="",
            kind="server", initial={
                "protocol": "smb", "label": "NAS", "host": "nas.local",
                "share": "", "domain": "", "username": "",
            },
        )
        share = self.result()
        discovered_uris = []
        authenticated = []

        def discover(_shares, uri, _cancel, done):
            discovered_uris.append(uri)
            done([server] if uri == "network:///" else [share], "")

        def authenticate(uri, credentials, _cancel, done):
            authenticated.append((uri, credentials.copy()))
            done("")

        dialog = DiscoveryPanel(
            [], discover_fn=discover, authenticate_fn=authenticate
        )
        self.addCleanup(dialog.close)
        credential_dialog = mock.Mock()
        credential_dialog.exec_.return_value = QtWidgets.QDialog.Accepted
        credential_dialog.values.return_value = {
            "domain": "WORKGROUP", "username": "alice", "password": "secret",
        }
        with mock.patch(
            "mountie.app.components.discovery.DiscoveryCredentialsDialog",
            return_value=credential_dialog,
        ):
            server_card = dialog.results.itemWidget(dialog.results.item(0))
            browse = next(
                button for button in server_card.findChildren(QtWidgets.QPushButton)
                if button.text() == "Sign In & Browse"
            )
            browse.click()

        self.assertEqual(authenticated[0][0], "smb://nas.local/")
        self.assertEqual(discovered_uris[-1], "smb://nas.local/")
        received = []
        dialog.import_requested.connect(received.append)
        share_card = dialog.results.itemWidget(dialog.results.item(0))
        add = next(
            button for button in share_card.findChildren(QtWidgets.QPushButton)
            if button.text() == "Add to Mountie"
        )
        add.click()
        self.assertEqual(received[0]["share"], "data")
        self.assertEqual(received[0]["domain"], "WORKGROUP")
        self.assertEqual(received[0]["username"], "alice")
        self.assertEqual(received[0]["_password"], "secret")

    def test_configured_result_cannot_be_imported(self):
        card = DiscoveryCard(self.result(configured=True))
        self.addCleanup(card.close)
        button = next(
            button for button in card.findChildren(QtWidgets.QPushButton)
            if button.text() == "Already Added"
        )
        self.assertFalse(button.isEnabled())

    def test_unavailable_backend_has_a_recoverable_message(self):
        dialog = DiscoveryPanel(
            [], discover_fn=lambda _shares, _uri, _cancel, done: done([], "")
        )
        self.addCleanup(dialog.close)
        generation = dialog._generation
        dialog._on_finished(generation, [], "Operation not supported")
        self.assertIn("unavailable", dialog.message.text())
        self.assertTrue(dialog.refresh_btn.isEnabled())

    def test_closing_cancels_pending_discovery(self):
        dialog = DiscoveryPanel(
            [], discover_fn=lambda _shares, _uri, _cancel, done: done([], "")
        )
        cancellable = dialog._cancellable
        dialog.close()
        self.assertTrue(cancellable.is_cancelled())

    def test_stalled_backend_times_out_and_can_be_retried(self):
        dialog = DiscoveryPanel(
            [], discover_fn=lambda _shares, _uri, _cancel, _done: None
        )
        self.addCleanup(dialog.close)
        cancellable = dialog._cancellable
        dialog._on_timeout()
        self.assertTrue(cancellable.is_cancelled())
        self.assertIn("timed out", dialog.message.text())
        self.assertTrue(dialog.refresh_btn.isEnabled())


if __name__ == "__main__":
    unittest.main()
