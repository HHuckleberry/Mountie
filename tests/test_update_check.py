import json
import os
import unittest
import urllib.error
from unittest import mock

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt5 import QtWidgets

from mountie.update_check import (
    UpdateChecker,
    _parse_version,
    fetch_latest_release,
    is_update_available,
)


class VersionParsingTests(unittest.TestCase):
    def test_parses_plain_version(self):
        self.assertEqual(_parse_version("0.2.1"), (0, 2, 1))

    def test_parses_tag_with_v_prefix(self):
        self.assertEqual(_parse_version("v0.3.0"), (0, 3, 0))

    def test_ignores_prerelease_suffix(self):
        self.assertEqual(_parse_version("1.0.0-beta"), (1, 0, 0))

    def test_rejects_garbage(self):
        self.assertIsNone(_parse_version("not-a-version"))
        self.assertIsNone(_parse_version(""))
        self.assertIsNone(_parse_version(None))


class UpdateAvailabilityTests(unittest.TestCase):
    def test_newer_remote_is_an_update(self):
        self.assertTrue(is_update_available("0.2.1", "v0.3.0"))

    def test_equal_versions_are_not_an_update(self):
        self.assertFalse(is_update_available("0.2.1", "v0.2.1"))

    def test_older_remote_is_not_an_update(self):
        self.assertFalse(is_update_available("0.3.0", "v0.2.1"))

    def test_unparseable_versions_never_claim_an_update(self):
        self.assertFalse(is_update_available("0.2.1", "not-a-version"))
        self.assertFalse(is_update_available("not-a-version", "v0.3.0"))


def _response(payload):
    body = json.dumps(payload).encode("utf-8")
    context = mock.MagicMock()
    context.__enter__.return_value.read.return_value = body
    return context


class FetchLatestReleaseTests(unittest.TestCase):
    def test_returns_version_and_url_from_a_successful_response(self):
        payload = {"tag_name": "v0.3.0", "html_url": "https://example.com/releases/v0.3.0"}
        with mock.patch("urllib.request.urlopen", return_value=_response(payload)):
            release = fetch_latest_release()
        self.assertEqual(release, {"version": "0.3.0", "url": payload["html_url"]})

    def test_network_failure_returns_none_instead_of_raising(self):
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("no route to host"),
        ):
            self.assertIsNone(fetch_latest_release())

    def test_malformed_json_returns_none_instead_of_raising(self):
        context = mock.MagicMock()
        context.__enter__.return_value.read.return_value = b"not json"
        with mock.patch("urllib.request.urlopen", return_value=context):
            self.assertIsNone(fetch_latest_release())

    def test_missing_fields_return_none(self):
        with mock.patch("urllib.request.urlopen", return_value=_response({})):
            self.assertIsNone(fetch_latest_release())


class UpdateCheckerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_emits_the_release_when_a_newer_version_exists(self):
        checker = UpdateChecker()
        received = []
        checker.finished.connect(received.append)
        release = {"version": "0.3.0", "url": "https://example.com"}
        with mock.patch(
            "mountie.update_check.fetch_latest_release", return_value=release
        ):
            checker._run("0.2.1")
        self.assertEqual(received, [release])

    def test_emits_none_when_already_current(self):
        checker = UpdateChecker()
        received = []
        checker.finished.connect(received.append)
        release = {"version": "0.2.1", "url": "https://example.com"}
        with mock.patch(
            "mountie.update_check.fetch_latest_release", return_value=release
        ):
            checker._run("0.2.1")
        self.assertEqual(received, [None])

    def test_emits_none_when_the_check_fails(self):
        checker = UpdateChecker()
        received = []
        checker.finished.connect(received.append)
        with mock.patch(
            "mountie.update_check.fetch_latest_release", return_value=None
        ):
            checker._run("0.2.1")
        self.assertEqual(received, [None])


if __name__ == "__main__":
    unittest.main()
