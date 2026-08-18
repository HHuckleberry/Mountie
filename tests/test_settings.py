import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mountie import settings


class ConfigTests(unittest.TestCase):
    def test_profile_supplies_identity_policy_and_key(self):
        config = settings.default_config()
        config["credential_policy"] = "ask"
        config["credential_profiles"] = [{
            "id": "work", "label": "Work", "username": "alice",
            "domain": "ACME", "credential_policy": "session",
        }]
        share = {
            "id": "share", "credential_profile_id": "work",
            "username": "ignored", "domain": "ignored",
        }
        resolved = settings.share_with_credentials(config, share)
        self.assertEqual((resolved["domain"], resolved["username"]), ("ACME", "alice"))
        self.assertEqual(settings.effective_credential_policy(config, share), "session")
        self.assertEqual(settings.credential_key(config, share), "work")

    def test_profile_global_policy_ignores_stale_share_override(self):
        config = settings.default_config()
        config["credential_policy"] = "ask"
        config["credential_profiles"] = [{
            "id": "work", "label": "Work", "username": "alice",
            "domain": "", "credential_policy": "global",
        }]
        share = {
            "id": "share", "credential_profile_id": "work",
            "credential_policy": "permanent",
        }
        self.assertEqual(settings.effective_credential_policy(config, share), "ask")

    def test_new_install_defaults_to_ask(self):
        self.assertEqual(settings.default_config()["credential_policy"], "ask")

    def test_rejects_invalid_credential_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            config = settings.default_config()
            config["credential_policy"] = "unsafe"
            path.write_text(json.dumps(config))
            with mock.patch.object(settings, "CONFIG_PATH", path), \
                 self.assertRaises(settings.ConfigError):
                settings.load_config()

    def test_new_install_checks_for_updates_by_default(self):
        self.assertTrue(settings.default_config()["check_for_updates"])

    def test_legacy_config_missing_the_field_defaults_to_enabled(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            legacy = settings.default_config()
            legacy.pop("check_for_updates")
            path.write_text(json.dumps(legacy))
            with mock.patch.object(settings, "CONFIG_PATH", path):
                config = settings.load_config()
        self.assertTrue(config["check_for_updates"])

    def test_rejects_non_boolean_check_for_updates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            config = settings.default_config()
            config["check_for_updates"] = "yes"
            path.write_text(json.dumps(config))
            with mock.patch.object(settings, "CONFIG_PATH", path), \
                 self.assertRaises(settings.ConfigError):
                settings.load_config()

    def test_legacy_default_preserves_permanent_storage_behavior(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            legacy = settings.default_config()
            legacy.pop("credential_policy")
            legacy.pop("config_version")
            path.write_text(json.dumps(legacy))
            with mock.patch.object(settings, "CONFIG_PATH", path):
                migrated = settings.load_config()
        self.assertEqual(migrated["credential_policy"], "permanent")

    def test_legacy_never_save_migrates_to_ask(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            legacy = settings.default_config()
            legacy.pop("credential_policy")
            legacy.pop("config_version")
            legacy["never_save_credentials"] = True
            path.write_text(json.dumps(legacy))
            with mock.patch.object(settings, "CONFIG_PATH", path):
                migrated = settings.load_config()
        self.assertEqual(migrated["credential_policy"], "ask")

    def test_rejects_malformed_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text("{broken")
            with mock.patch.object(settings, "CONFIG_PATH", path), \
                 self.assertRaises(settings.ConfigError):
                settings.load_config()

    def test_rejects_invalid_share_shape(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"shares": [{"label": "Incomplete"}]}))
            with mock.patch.object(settings, "CONFIG_PATH", path), \
                 self.assertRaises(settings.ConfigError):
                settings.load_config()

    def test_saved_config_is_private(self):
        with tempfile.TemporaryDirectory() as directory:
            config_dir = Path(directory) / "mountie"
            config_path = config_dir / "config.json"
            backup_path = config_dir / "config.json.backup"
            with mock.patch.object(settings, "CONFIG_DIR", config_dir), \
                 mock.patch.object(settings, "CONFIG_PATH", config_path), \
                 mock.patch.object(settings, "BACKUP_PATH", backup_path):
                settings.save_config(settings.default_config())
            self.assertEqual(config_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(backup_path.stat().st_mode & 0o777, 0o600)

    def test_export_is_private_and_round_trips_without_secret_field(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "export.json"
            config = settings.default_config()
            config["shares"].append({
                "id": "saved", "label": "Saved", "host": "server",
                "share": "data", "username": "alice",
            })
            settings.export_config(config, path)
            loaded = settings.load_config_file(path)
            self.assertEqual(loaded["shares"][0]["username"], "alice")
            self.assertNotIn("password", path.read_text().casefold())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_export_replaces_symlink_instead_of_overwriting_its_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "unrelated.txt"
            target.write_text("do not change")
            destination = root / "export.json"
            destination.symlink_to(target)
            settings.export_config(settings.default_config(), destination)
            self.assertEqual(target.read_text(), "do not change")
            self.assertFalse(destination.is_symlink())

    def test_recovers_from_backup_when_primary_is_invalid(self):
        with tempfile.TemporaryDirectory() as directory:
            config_dir = Path(directory)
            config_path = config_dir / "config.json"
            backup_path = config_dir / "config.json.backup"
            config_path.write_text("{broken")
            backup = settings.default_config()
            backup["shares"].append({
                "id": "saved", "label": "Saved", "host": "server", "share": "data"
            })
            backup_path.write_text(json.dumps(backup))
            with mock.patch.object(settings, "CONFIG_PATH", config_path), \
                 mock.patch.object(settings, "BACKUP_PATH", backup_path):
                recovered = settings.load_config()
            self.assertEqual(recovered["shares"][0]["id"], "saved")

    def test_migrates_legacy_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_dir = root / "new"
            config_path = config_dir / "config.json"
            backup_path = config_dir / "config.json.backup"
            legacy_path = root / "legacy.json"
            legacy = settings.default_config()
            legacy["shares"].append({
                "id": "legacy", "label": "Legacy", "host": "server", "share": "data"
            })
            legacy_path.write_text(json.dumps(legacy))
            with mock.patch.object(settings, "CONFIG_DIR", config_dir), \
                 mock.patch.object(settings, "CONFIG_PATH", config_path), \
                 mock.patch.object(settings, "BACKUP_PATH", backup_path), \
                 mock.patch.object(settings, "LEGACY_CONFIG_PATH", legacy_path):
                migrated = settings.load_config()
            self.assertEqual(migrated["shares"][0]["id"], "legacy")
            self.assertEqual(migrated["shares"][0]["domain"], "")
            self.assertEqual(migrated["shares"][0]["username"], "")
            self.assertTrue(config_path.exists())


class SharePresetsTests(unittest.TestCase):
    ALLOWED_INITIAL_KEYS = {
        "protocol", "label", "host", "share", "domain", "username",
        "disconnect_after_minutes", "disconnect_on_lock", "disconnect_on_suspend",
    }

    def test_presets_use_known_protocols_and_fields(self):
        protocol_keys = {key for key, _label in settings.PROTOCOLS}
        for preset in settings.SHARE_PRESETS:
            initial = preset["initial"]
            with self.subTest(preset=preset["key"]):
                self.assertIn(initial["protocol"], protocol_keys)
                self.assertLessEqual(set(initial.keys()), self.ALLOWED_INITIAL_KEYS)

    def test_preset_keys_are_unique(self):
        keys = [preset["key"] for preset in settings.SHARE_PRESETS]
        self.assertEqual(len(keys), len(set(keys)))


class BackendConfigTests(unittest.TestCase):
    def _load(self, shares):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"shares": shares}))
            with mock.patch.object(settings, "CONFIG_PATH", path):
                return settings.load_config()

    def test_legacy_share_without_backend_defaults_to_gvfs(self):
        loaded = self._load([{
            "id": "legacy", "label": "Legacy", "host": "server", "share": "data",
        }])
        self.assertEqual(loaded["shares"][0]["backend"], settings.BACKEND_GVFS)

    def test_rejects_unknown_backend(self):
        with self.assertRaises(settings.ConfigError):
            self._load([{
                "id": "share", "label": "Share", "host": "server", "share": "data",
                "backend": "sshfs",
            }])

    def test_rejects_native_backend_with_non_smb_protocol(self):
        with self.assertRaises(settings.ConfigError):
            self._load([{
                "id": "share", "label": "Share", "host": "server", "share": "data",
                "protocol": "nfs", "backend": settings.BACKEND_NATIVE,
            }])

    def test_native_backend_with_smb_round_trips(self):
        loaded = self._load([{
            "id": "share", "label": "Share", "host": "server", "share": "data",
            "protocol": "smb", "backend": settings.BACKEND_NATIVE,
        }])
        self.assertEqual(loaded["shares"][0]["backend"], settings.BACKEND_NATIVE)

    def test_iso_image_round_trips_without_network_fields(self):
        loaded = self._load([{
            "id": "disc", "kind": settings.SOURCE_ISO,
            "label": "Reference Data", "path": "/data/reference.iso",
        }])
        image = loaded["shares"][0]
        self.assertEqual(image["path"], "/data/reference.iso")
        self.assertNotIn("host", image)

    def test_iso_image_requires_a_path(self):
        with self.assertRaises(settings.ConfigError):
            self._load([{
                "id": "disc", "kind": settings.SOURCE_ISO, "label": "Broken",
            }])


if __name__ == "__main__":
    unittest.main()
