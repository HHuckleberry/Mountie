import unittest

from mountie.mounts import link_name_collision, share_uri, validate_share


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


if __name__ == "__main__":
    unittest.main()
