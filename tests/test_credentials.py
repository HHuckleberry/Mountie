import unittest
from unittest import mock

from gi.repository import Secret

from mountie.credentials import set_password


class CredentialStorageTests(unittest.TestCase):
    def test_session_policy_uses_session_collection(self):
        with mock.patch("mountie.credentials.Secret.password_clear_sync"), \
             mock.patch("mountie.credentials.Secret.password_store_sync") as store:
            set_password("share", "secret", "session")
        self.assertEqual(store.call_args.args[2], Secret.COLLECTION_SESSION)

    def test_permanent_policy_uses_default_collection(self):
        with mock.patch("mountie.credentials.Secret.password_clear_sync"), \
             mock.patch("mountie.credentials.Secret.password_store_sync") as store:
            set_password("share", "secret", "permanent")
        self.assertEqual(store.call_args.args[2], Secret.COLLECTION_DEFAULT)

    def test_ask_policy_cannot_store(self):
        with self.assertRaises(ValueError):
            set_password("share", "secret", "ask")


if __name__ == "__main__":
    unittest.main()
