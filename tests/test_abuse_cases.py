"""Abuse-case regressions for data crossing Mountie's trust boundaries.

These tests never contact a server or keyring. They exercise hostile local
configuration, path construction, credential prompts, and rapid UI actions.
"""

import json
import importlib.machinery
import importlib.util
import logging
import os
import subprocess
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from gi.repository import Gio
from PyQt5 import QtCore, QtWidgets

from mountie import settings
from mountie import native_mount
from mountie.mounts import CredMountOperation, link_path, share_uri
from mountie.app.window import MainWindow


def load_native_helper():
    """Load the extensionless root helper without installing or executing it."""
    path = Path(__file__).resolve().parent.parent / (
        "data/native-mount-helper/mountie-mount-helper"
    )
    loader = importlib.machinery.SourceFileLoader("abuse_native_helper", str(path))
    spec = importlib.util.spec_from_file_location(
        "abuse_native_helper", path, loader=loader
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


native_helper = load_native_helper()


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


class NativeMountAbuseTests(unittest.TestCase):
    def test_native_backend_cannot_enable_privileged_non_smb_mounts(self):
        payload = settings.default_config()
        payload["shares"] = [valid_share(protocol="nfs", backend="native")]
        with self.assertRaises(settings.ConfigError):
            read_config_payload(payload)

    def test_privileged_helper_rejects_option_and_command_injection(self):
        hostile_sources = (
            "//host/share,uid=0",
            "//host/share;touch /tmp/owned",
            "//host/$(id)",
            "//host/`id`",
            "-o bind",
        )
        for source in hostile_sources:
            with self.subTest(source=source), \
                 mock.patch.object(native_helper.os, "execv") as execv:
                with self.assertRaises(SystemExit):
                    native_helper.do_mount(
                        [source, "/untrusted/mountpoint", "/untrusted/credentials"],
                        os.getuid(),
                    )
                execv.assert_not_called()

    def test_native_password_is_file_only_and_removed_after_failure(self):
        password = "private-native-password"
        observed = {}
        completed = []

        def run_fn(argv, **_kwargs):
            observed["argv"] = argv
            observed["credentials"] = argv[-1]
            self.assertIn(password, Path(argv[-1]).read_text())
            return subprocess.CompletedProcess(
                argv, 1, stdout="", stderr="mount error(13): Permission denied"
            )

        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(
                 native_mount, "base_runtime_dir", return_value=Path(directory)
             ), mock.patch.object(
                 native_mount,
                 "mountpoint_for",
                 return_value=Path(directory) / "share-id",
             ):
            native_mount.mount_share(
                valid_share(backend="native"),
                password,
                lambda *result: completed.append(result),
                run_fn=run_fn,
                sandboxed_fn=lambda: False,
                which_fn=lambda _name: "/usr/bin/pkexec",
            )
            deadline = time.monotonic() + 2
            while not completed and time.monotonic() < deadline:
                time.sleep(0.01)

            self.assertTrue(completed, "native mount callback was not invoked")
            self.assertFalse(completed[0][0])
            self.assertNotIn(password, observed["argv"])
            self.assertFalse(Path(observed["credentials"]).exists())

    def test_privileged_unmount_cannot_target_outside_callers_runtime_dir(self):
        with mock.patch.object(native_helper.subprocess, "run") as run:
            with self.assertRaises(SystemExit):
                native_helper.do_unmount(["/etc"], os.getuid())
            run.assert_not_called()

    def test_successful_privileged_mount_has_a_fixed_exec_contract(self):
        """Authorization must not become a caller-controlled root command."""
        uid = os.getuid()
        source = "//nas.local/data"
        mountpoint = "/run/user/1000/mountie/share-id"
        credentials = "/run/user/1000/mountie/creds-safe"
        with tempfile.TemporaryDirectory() as directory:
            mount_fd = os.open(directory, os.O_RDONLY)
            credentials_path = Path(directory) / "input-creds"
            credentials_path.write_text("username=alice\npassword=secret\n")
            credentials_fd = os.open(credentials_path, os.O_RDONLY)
            run = mock.Mock(return_value=subprocess.CompletedProcess([], 0))
            with mock.patch.object(
                native_helper, "require_unc_source", return_value=source
            ) as validate_source, mock.patch.object(
                native_helper, "require_safe_mountpoint",
                return_value=(mount_fd, mountpoint),
            ) as validate_mountpoint, mock.patch.object(
                native_helper, "require_safe_credentials_file",
                return_value=(credentials_fd, credentials),
            ) as validate_credentials, mock.patch.object(
                native_helper, "_caller_gid", return_value=4321
            ), mock.patch.object(
                native_helper, "ROOT_CREDENTIALS_DIR", directory
            ), mock.patch.object(native_helper.subprocess, "run", run):
                self.assertEqual(
                    native_helper.do_mount([source, mountpoint, credentials], uid), 0
                )

        validate_source.assert_called_once_with(source)
        validate_mountpoint.assert_called_once_with(mountpoint, uid)
        validate_credentials.assert_called_once_with(credentials, uid)
        argv = run.call_args.args[0]
        self.assertEqual(argv[:6], [
            native_helper.MOUNT_BIN,
            "-t",
            "cifs",
            source,
            f"/proc/self/fd/{mount_fd}",
            "-o",
        ])
        self.assertIn(
            f"uid={uid},gid=4321,file_mode=0600,dir_mode=0700", argv[6]
        )
        self.assertNotIn(credentials, argv[6])
        self.assertEqual(run.call_args.kwargs["pass_fds"], (mount_fd,))

    def test_successful_privileged_unmount_has_a_fixed_exec_contract(self):
        uid = os.getuid()
        mountpoint = "/run/user/1000/mountie/share-id"
        mount_fd = os.open("/tmp", os.O_RDONLY)
        run = mock.Mock(return_value=subprocess.CompletedProcess([], 0))
        with mock.patch.object(
            native_helper, "require_safe_mountpoint",
            return_value=(mount_fd, mountpoint),
        ) as validate_mountpoint, mock.patch.object(
            native_helper, "require_cifs_mount"
        ) as validate_cifs, mock.patch.object(
            native_helper.subprocess, "run", run
        ):
            self.assertEqual(native_helper.do_unmount([mountpoint], uid), 0)

        validate_mountpoint.assert_called_once_with(mountpoint, uid)
        validate_cifs.assert_called_once_with(mount_fd)
        run.assert_called_once_with(
            [native_helper.UMOUNT_BIN, "--", f"/proc/self/fd/{mount_fd}"],
            pass_fds=(mount_fd,), check=False,
        )

    def test_polkit_authorizes_only_the_fixed_installed_helper(self):
        policy_path = Path(__file__).resolve().parent.parent / (
            "data/native-mount-helper/io.github.HHuckleberry.Mountie.policy"
        )
        root = ET.parse(policy_path).getroot()
        actions = root.findall("action")
        self.assertEqual(len(actions), 1)
        self.assertEqual(
            actions[0].attrib["id"], "io.github.HHuckleberry.Mountie.mount-helper"
        )
        annotations = {
            node.attrib.get("key"): (node.text or "").strip()
            for node in actions[0].findall("annotate")
        }
        self.assertEqual(
            annotations.get("org.freedesktop.policykit.exec.path"),
            native_mount.WRAPPER_PATH,
        )
        defaults = actions[0].find("defaults")
        self.assertEqual(defaults.findtext("allow_any"), "no")
        self.assertEqual(defaults.findtext("allow_inactive"), "no")

    def test_privileged_helper_uses_absolute_system_binary_paths(self):
        self.assertEqual(native_helper.MOUNT_BIN, "/usr/bin/mount")
        self.assertEqual(native_helper.UMOUNT_BIN, "/usr/bin/umount")


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
        with mock.patch("mountie.app.window.load_config", return_value=config), \
             mock.patch("mountie.app.window.prune_links"), \
             mock.patch("mountie.app.window.is_mounted", return_value=False), \
             mock.patch("mountie.app.window.update_link", return_value=None), \
             mock.patch("mountie.app.window.external_network_mounts", return_value=[]), \
             mock.patch("mountie.app.window.get_password", return_value="secret"), \
             mock.patch("mountie.app.window.mount_share") as mount, \
             mock.patch("mountie.app.window.UpdateChecker"):
            window = MainWindow(FakeTheme())
            self.addCleanup(window.close)
            window.on_toggle("share-id", True)
            window.refresh_all_status()
            self.assertFalse(window.cards["share-id"].toggle.isEnabled())
            window.on_toggle("share-id", True)
        mount.assert_called_once()


if __name__ == "__main__":
    unittest.main()
