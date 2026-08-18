import unittest
from unittest import mock

from gi.repository import Gio

from mountie.mounts import (
    CredMountOperation,
    external_network_mounts,
    is_mounted,
    link_name_collision,
    local_path,
    mount_share,
    share_uri,
    unmount_share,
    validate_share,
)


def mounted(name, uri):
    location = mock.Mock()
    location.get_uri.return_value = uri
    value = mock.Mock()
    value.get_name.return_value = name
    value.get_default_location.return_value = location
    return value


def share(**overrides):
    values = {
        "id": "one",
        "protocol": "smb",
        "label": "Team Share",
        "host": "files.example.com",
        "share": "Team Docs",
        "username": "",
    }
    values.update(overrides)
    return values


class ShareUriTests(unittest.TestCase):
    def test_encodes_path_segments(self):
        self.assertEqual(
            share_uri(share(share="Team Docs/#draft")),
            "smb://files.example.com/Team%20Docs/%23draft/",
        )

    def test_brackets_ipv6_host(self):
        self.assertEqual(
            share_uri(share(host="2001:db8::1", share="data")),
            "smb://[2001:db8::1]/data/",
        )

    def test_encodes_international_hostname(self):
        self.assertEqual(
            share_uri(share(host="münchen.example", share="data")),
            "smb://xn--mnchen-3ya.example/data/",
        )

    def test_rejects_host_with_uri_delimiter(self):
        candidate = share(host="server.example/path")
        self.assertIsNotNone(validate_share(candidate))
        with self.assertRaises(ValueError):
            share_uri(candidate)

    def test_rejects_invalid_bracketed_ipv6(self):
        self.assertIsNotNone(validate_share(share(host="[2001:db8::1")))

    def test_rejects_native_backend_with_non_smb_protocol(self):
        self.assertIsNotNone(
            validate_share(share(protocol="sftp", backend="native"))
        )

    def test_accepts_native_backend_with_smb_protocol(self):
        self.assertIsNone(validate_share(share(protocol="smb", backend="native")))

    def test_rejects_invalid_port(self):
        self.assertIsNotNone(validate_share(share(host="server.example:99999")))


class LinkCollisionTests(unittest.TestCase):
    def test_detects_labels_with_same_normalized_name(self):
        config = {"shares": [share(id="existing", label="Media Share")]}
        self.assertTrue(
            link_name_collision(config, share(id="new", label="Media/Share"))
        )

    def test_can_exclude_share_being_edited(self):
        candidate = share(id="existing", label="Media Share")
        config = {"shares": [candidate]}
        self.assertFalse(link_name_collision(config, candidate, "existing"))


class ExternalMountTests(unittest.TestCase):
    def test_finds_only_unconfigured_network_mounts(self):
        mounts = [
            mounted("Configured", "smb://files.example.com/Team%20Docs"),
            mounted("Other", "sftp://alice@other.example/home/alice"),
            mounted("Local disk", "file:///mnt/storage"),
        ]
        result = external_network_mounts([share()], mounts)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "Other")
        self.assertEqual(result[0]["uri"], "sftp://other.example/home/alice")
        self.assertEqual(result[0]["config"]["username"], "alice")
        self.assertEqual(result[0]["config"]["share"], "home/alice")

    def test_removes_password_query_and_fragment_from_display_uri(self):
        mounts = [mounted(
            "Private",
            "sftp://alice:secret@host.example/home?token=secret#section",
        )]
        result = external_network_mounts([], mounts)
        self.assertEqual(result[0]["uri"], "sftp://host.example/home")
        self.assertNotIn("secret", repr(result))

    def test_mount_root_without_share_path_is_view_only(self):
        result = external_network_mounts([], [mounted("Server", "ftp://server/")])
        self.assertNotIn("config", result[0])

    def test_deduplicates_equivalent_mounts(self):
        mounts = [
            mounted("One", "smb://server/data/"),
            mounted("Two", "smb://SERVER/data"),
        ]
        self.assertEqual(len(external_network_mounts([], mounts)), 1)


class CredentialPromptTests(unittest.TestCase):
    def test_configured_domain_is_sent_separately(self):
        mount_operation = CredMountOperation("user", "password", "EXAMPLE")
        operation = mock.Mock()
        flags = Gio.AskPasswordFlags.NEED_USERNAME | Gio.AskPasswordFlags.NEED_DOMAIN

        mount_operation._on_ask_password(
            operation, "", "suggested-workgroup", "ignored", flags
        )

        operation.set_username.assert_called_once_with("user")
        operation.set_domain.assert_called_once_with("EXAMPLE")

    def test_blank_domain_uses_backend_default(self):
        mount_operation = CredMountOperation("local-user", "password")
        operation = mock.Mock()

        mount_operation._on_ask_password(
            operation,
            "",
            "local-user",
            "WORKGROUP",
            Gio.AskPasswordFlags.NEED_DOMAIN,
        )

        operation.set_domain.assert_called_once_with("WORKGROUP")

    def test_repeated_prompt_aborts_instead_of_looping(self):
        mount_operation = CredMountOperation("user", "password")
        operation = mock.Mock()
        flags = Gio.AskPasswordFlags.NEED_USERNAME | Gio.AskPasswordFlags.NEED_PASSWORD

        mount_operation._on_ask_password(operation, "", "", "", flags)
        operation.reply.assert_called_once_with(Gio.MountOperationResult.HANDLED)

        operation.reset_mock()
        mount_operation._on_ask_password(operation, "", "", "", flags)
        operation.reply.assert_called_once_with(Gio.MountOperationResult.ABORTED)
        self.assertTrue(mount_operation.credentials_rejected)


class BackendDispatchTests(unittest.TestCase):
    def test_gvfs_share_never_touches_native_mount(self):
        with mock.patch("mountie.mounts.native_mount") as native:
            with mock.patch("mountie.mounts.Gio") as gio:
                is_mounted(share())
            native.is_mounted.assert_not_called()
            gio.File.new_for_uri.assert_called_once()

    def test_native_share_dispatches_is_mounted(self):
        with mock.patch("mountie.mounts.native_mount") as native:
            native.is_mounted.return_value = True
            cfg = share(backend="native")
            self.assertTrue(is_mounted(cfg))
            native.is_mounted.assert_called_once_with(cfg)

    def test_native_share_dispatches_local_path(self):
        with mock.patch("mountie.mounts.native_mount") as native:
            native.local_path.return_value = "/run/user/1000/mountie/one"
            cfg = share(backend="native")
            self.assertEqual(local_path(cfg), "/run/user/1000/mountie/one")
            native.local_path.assert_called_once_with(cfg)

    def test_native_share_dispatches_mount(self):
        with mock.patch("mountie.mounts.native_mount") as native:
            cfg = share(backend="native")
            on_done = mock.Mock()
            mount_share(cfg, "secret", on_done)
            native.mount_share.assert_called_once_with(cfg, "secret", on_done)
            on_done.assert_not_called()

    def test_native_share_dispatches_unmount(self):
        with mock.patch("mountie.mounts.native_mount") as native:
            cfg = share(backend="native")
            on_done = mock.Mock()
            unmount_share(cfg, on_done)
            native.unmount_share.assert_called_once_with(cfg, on_done)
            on_done.assert_not_called()


if __name__ == "__main__":
    unittest.main()
