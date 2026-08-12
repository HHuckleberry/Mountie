import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mountie import settings


class ConfigTests(unittest.TestCase):
    def test_never_save_credentials_defaults_to_false(self):
        self.assertFalse(settings.default_config()["never_save_credentials"])

    def test_rejects_non_boolean_never_save_setting(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            config = settings.default_config()
            config["never_save_credentials"] = "yes"
            path.write_text(json.dumps(config))
            with mock.patch.object(settings, "CONFIG_PATH", path), \
                 self.assertRaises(settings.ConfigError):
                settings.load_config()

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


if __name__ == "__main__":
    unittest.main()
