import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mountie import settings


class ConfigTests(unittest.TestCase):
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
            with mock.patch.object(settings, "CONFIG_DIR", config_dir), \
                 mock.patch.object(settings, "CONFIG_PATH", config_path):
                settings.save_config(settings.default_config())
            self.assertEqual(config_path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
