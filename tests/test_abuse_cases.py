"""Abuse-case regressions for data crossing Mountie's trust boundaries.

These tests never contact a server or keyring. They exercise hostile local
configuration, path construction, credential prompts, and rapid UI actions.
"""

import json
import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from gi.repository import Gio
from PyQt5 import QtCore, QtWidgets

from mountie import settings
from mountie.mounts import CredMountOperation, link_path, share_uri
from mountie.ui.window import MainWindow


def valid_share(**overrides):
    share = {
        "id": "share-id",
        "protocol": "smb",
        "label": "Data",
        "host": "server.example",
        "share": "data",
        "domain": "",
        "username": "",
    }
    share.update(overrides)
    return share


def read_config_payload(payload):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "config.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with mock.patch.object(settings, "CONFIG_PATH", path), \
             mock.patch.object(settings, "BACKUP_PATH", path.with_suffix(".backup")):
            return settings.load_config()


class ConfigAbuseTests(unittest.TestCase):
    def test_duplicate_share_ids_are_rejected(self):
        payload = settings.default_config()
        payload["shares"] = [valid_share(), valid_share(label="Other")]
        with self.assertRaises(settings.ConfigError):
            read_config_payload(payload)

    def test_unknown_protocol_is_rejected(self):
        payload = settings.default_config()
        payload["shares"] = [valid_share(protocol="file")]
        with self.assertRaises(settings.ConfigError):
            read_config_payload(payload)

    def test_non_string_share_fields_are_rejected(self):
        for field in ("id", "protocol", "label", "host", "share", "domain", "username"):
            with self.subTest(field=field):
                payload = settings.default_config()
                payload["shares"] = [valid_share(**{field: ["not", "text"]})]
                with self.assertRaises(settings.ConfigError):
                    read_config_payload(payload)

    def test_invalid_theme_is_rejected(self):
        payload = settings.default_config()
        payload["theme"] = "../../unexpected"
        with self.assertRaises(settings.ConfigError):
            read_config_payload(payload)


class PathAndUriAbuseTests(unittest.TestCase):
    def test_fallback_id_cannot_escape_link_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            config = {"link_dir": directory}
            candidate = valid_share(label="..", id="../../outside")
            path = link_path(config, candidate)
            self.assertEqual(os.path.commonpath((directory, path)), directory)
            self.assertEqual(path.parent, Path(directory))

    def test_uri_control_delimiters_are_encoded_in_share_path(self):
        uri = share_uri(valid_share(share="docs?admin=true#fragment"))
        self.assertEqual(
            uri,
            "smb://server.example/docs%3Fadmin%3Dtrue%23fragment/",
        )


class CredentialAbuseTests(unittest.TestCase):
    def test_credentials_are_not_written_to_logs(self):
        operation = CredMountOperation(
            "private-user", "private-password", "private-domain"
        )
        responder = mock.Mock()
        flags = (
            Gio.AskPasswordFlags.NEED_USERNAME
            | Gio.AskPasswordFlags.NEED_DOMAIN
            | Gio.AskPasswordFlags.NEED_PASSWORD
        )
        with self.assertLogs("mountie.mounts", logging.INFO) as captured:
            operation._on_ask_password(responder, "", "", "", flags)
        output = "\n".join(captured.output)
        self.assertNotIn("private-user", output)
        self.assertNotIn("private-password", output)
        self.assertNotIn("private-domain", output)


class FakeTheme(QtCore.QObject):
    changed = QtCore.pyqtSignal()

    def set_mode(self, mode):
        self.mode = mode


class UiAbuseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_rapid_duplicate_toggle_survives_refresh_and_starts_one_mount(self):
        config = {
            **settings.default_config(),
            "shares": [valid_share()],
            "links_enabled": False,
        }
        with mock.patch("mountie.ui.window.load_config", return_value=config), \
             mock.patch("mountie.ui.window.prune_links"), \
             mock.patch("mountie.ui.window.is_mounted", return_value=False), \
             mock.patch("mountie.ui.window.update_link", return_value=None), \
             mock.patch("mountie.ui.window.external_network_mounts", return_value=[]), \
             mock.patch("mountie.ui.window.get_password", return_value="secret"), \
             mock.patch("mountie.ui.window.mount_share") as mount:
            window = MainWindow(FakeTheme())
            self.addCleanup(window.close)
            window.on_toggle("share-id", True)
            window.refresh_all_status()
            self.assertFalse(window.cards["share-id"].toggle.isEnabled())
            window.on_toggle("share-id", True)
        mount.assert_called_once()


if __name__ == "__main__":
    unittest.main()
