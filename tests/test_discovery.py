import unittest
from unittest import mock

from gi.repository import Gio

from mountie.discovery import (
    DiscoveryResult,
    _safe_discovery_uri,
    authenticate_network_uri_async,
    discover_network_async,
    result_from_info,
)


class FakeInfo:
    def __init__(self, name, target_uri=None, symbolic=None, regular=None):
        self.name = name
        self.target_uri = target_uri
        self.symbolic = symbolic
        self.regular = regular

    def get_attribute_string(self, attribute):
        return self.target_uri

    def get_display_name(self):
        return self.name

    def get_symbolic_icon(self):
        return self.symbolic

    def get_icon(self):
        return self.regular


class DiscoveryUriTests(unittest.TestCase):
    def test_removes_credentials_query_and_fragment(self):
        uri = _safe_discovery_uri(
            "sftp://alice:secret@host.example/home/alice?token=x#private"
        )
        self.assertEqual(uri, "sftp://host.example/home/alice")
        self.assertNotIn("secret", uri)
        self.assertNotIn("token", uri)

    def test_rejects_unsupported_schemes(self):
        self.assertIsNone(_safe_discovery_uri("https://host.example/share"))
        self.assertIsNone(_safe_discovery_uri("file:///private/path"))

    def test_allows_the_hostless_network_root(self):
        self.assertEqual(_safe_discovery_uri("network:///"), "network:/")


class DiscoveryResultTests(unittest.TestCase):
    def test_share_is_importable_and_uses_backend_icon(self):
        icon = Gio.ThemedIcon.new_from_names(
            ["drive-harddisk-network-symbolic", "network-server"]
        )
        result = result_from_info(
            FakeInfo("Team Docs", "smb://nas.local/Team%20Docs", icon),
            "network:///fallback",
        )
        self.assertEqual(result.protocol, "smb")
        self.assertEqual(result.kind, "share")
        self.assertTrue(result.importable)
        self.assertEqual(result.initial["host"], "nas.local")
        self.assertEqual(result.initial["share"], "Team Docs")
        self.assertIn("drive-harddisk-network-symbolic", result.icon_names)

    def test_server_requires_user_triggered_browsing(self):
        result = result_from_info(
            FakeInfo("NAS", "smb://nas.local/"), "network:///nas"
        )
        self.assertEqual(result.kind, "server")
        self.assertFalse(result.importable)
        self.assertTrue(result.can_prefill)
        self.assertEqual(result.initial["host"], "nas.local")
        self.assertEqual(result.initial["share"], "")
        self.assertIn("network-server-symbolic", result.icon_names)

    def test_container_is_browsable_but_not_importable(self):
        result = result_from_info(
            FakeInfo("Local Network", "dns-sd://local/"), "network:///local"
        )
        self.assertEqual(result.kind, "container")
        self.assertFalse(result.importable)

    def test_unsupported_advertisement_is_ignored(self):
        result = result_from_info(
            FakeInfo("Web page", "https://device.local/"), "network:///web"
        )
        self.assertIsNone(result)


class DiscoveryEnumerationTests(unittest.TestCase):
    def test_async_enumeration_deduplicates_and_marks_configured(self):
        infos = [
            FakeInfo("Data", "smb://nas.local/data/"),
            FakeInfo("Duplicate", "smb://NAS.local/data"),
            FakeInfo("Printer", "ipp://printer.local/"),
        ]
        child = mock.Mock()
        child.get_uri.return_value = "network:///data"
        enumerator = mock.Mock()
        enumerator.get_child.return_value = child
        enumerator.next_files_finish.side_effect = [infos, []]
        next_callbacks = []
        enumerator.next_files_async.side_effect = (
            lambda _count, _priority, _cancel, callback, _data:
            next_callbacks.append(callback)
        )
        root = mock.Mock()
        root.enumerate_children_finish.return_value = enumerator
        received = []
        configured = [{
            "protocol": "smb", "label": "Data", "host": "nas.local",
            "share": "data",
        }]
        with mock.patch("mountie.discovery.Gio.File.new_for_uri", return_value=root):
            discover_network_async(
                configured,
                on_done=lambda found, error: received.append((found, error)),
            )
        enumerate_callback = root.enumerate_children_async.call_args.args[4]
        enumerate_callback(root, object())
        next_callbacks.pop(0)(enumerator, object())
        next_callbacks.pop(0)(enumerator, object())
        self.assertEqual(received[0][1], "")
        self.assertEqual(len(received[0][0]), 1)
        self.assertEqual(received[0][0][0].uri, "smb://nas.local/data/")
        self.assertTrue(received[0][0][0].configured)
        self.assertFalse(received[0][0][0].importable)
        enumerator.close_async.assert_called_once()


class DiscoveryAuthenticationTests(unittest.TestCase):
    def test_mounts_server_with_credentials_without_saving_them(self):
        server = mock.Mock()
        operation = mock.Mock()
        received = []
        credentials = {
            "domain": "WORKGROUP", "username": "alice", "password": "secret",
        }
        with mock.patch(
            "mountie.discovery.Gio.File.new_for_uri", return_value=server
        ), mock.patch(
            "mountie.discovery.CredMountOperation", return_value=operation
        ) as operation_class:
            authenticate_network_uri_async(
                "smb://nas.local/", credentials, on_done=received.append
            )
        operation_class.assert_called_once_with("alice", "secret", "WORKGROUP")
        callback = server.mount_enclosing_volume.call_args.args[3]
        callback(server, object())
        server.mount_enclosing_volume_finish.assert_called_once()
        self.assertEqual(received, [""])

    def test_rejects_unsupported_uri_before_using_credentials(self):
        with mock.patch("mountie.discovery.Gio.File.new_for_uri") as new_file:
            received = []
            authenticate_network_uri_async(
                "https://example.com/", {"password": "secret"},
                on_done=received.append,
            )
        new_file.assert_not_called()
        self.assertEqual(received, ["The advertised server address is not supported."])


if __name__ == "__main__":
    unittest.main()
