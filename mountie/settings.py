import json
import logging
import os
import shutil
from pathlib import Path


APP_ID = "io.github.HHuckleberry.Mountie"

THEME_SYSTEM = "system"
THEME_LIGHT = "light"
THEME_DARK = "dark"
THEMES = [
    (THEME_SYSTEM, "System"),
    (THEME_LIGHT, "Light"),
    (THEME_DARK, "Dark"),
]

PROTOCOLS = [
    ("smb", "SMB / CIFS (Windows, Samba, Synology, etc.)"),
    ("afp", "AFP (older macOS file sharing)"),
    ("nfs", "NFS"),
    ("sftp", "SFTP / SSH"),
    ("ftp", "FTP"),
    ("ftps", "FTPS"),
    ("dav", "WebDAV"),
    ("davs", "WebDAV (secure)"),
]
DEFAULT_PROTOCOL = "smb"
DEFAULT_LINK_DIR = "~/Shares"

CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
CONFIG_DIR = CONFIG_HOME / "mountie"
CONFIG_PATH = CONFIG_DIR / "config.json"
BACKUP_PATH = CONFIG_DIR / "config.json.backup"
LEGACY_CONFIG_PATH = Path.home() / ".config" / "mountie" / "config.json"

logger = logging.getLogger(__name__)


class ConfigError(RuntimeError):
    pass


def default_config():
    return {
        "shares": [],
        "theme": THEME_SYSTEM,
        "link_dir": DEFAULT_LINK_DIR,
        "links_enabled": True,
        "never_save_credentials": False,
    }


def _read_config(path):
    try:
        with path.open() as config_file:
            config = json.load(config_file)
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigError(f"Could not read {path}: {error}") from error
    if not isinstance(config, dict):
        raise ConfigError(f"{path} must contain a JSON object.")
    config.setdefault("shares", [])
    config.setdefault("theme", THEME_SYSTEM)
    config.setdefault("link_dir", DEFAULT_LINK_DIR)
    config.setdefault("links_enabled", True)
    config.setdefault("never_save_credentials", False)
    if not isinstance(config["shares"], list):
        raise ConfigError(f"The shares field in {path} must be a list.")
    if config["theme"] not in dict(THEMES):
        raise ConfigError(f"The theme field in {path} is invalid.")
    required_share_fields = ("id", "label", "host", "share")
    share_ids = set()
    for index, share in enumerate(config["shares"], start=1):
        if not isinstance(share, dict) or any(field not in share for field in required_share_fields):
            raise ConfigError(f"Share {index} in {path} is incomplete or invalid.")
        # Credentials added after the original config format are optional;
        # normalizing them here keeps old saved shares fully compatible.
        share.setdefault("protocol", DEFAULT_PROTOCOL)
        share.setdefault("domain", "")
        share.setdefault("username", "")
        string_fields = ("id", "protocol", "label", "host", "share", "domain", "username")
        if any(not isinstance(share[field], str) for field in string_fields):
            raise ConfigError(f"Share {index} in {path} contains a non-text field.")
        if not share["id"]:
            raise ConfigError(f"Share {index} in {path} has an empty ID.")
        if share["id"] in share_ids:
            raise ConfigError(f"Share {index} in {path} reuses another share ID.")
        share_ids.add(share["id"])
        if share["protocol"] not in dict(PROTOCOLS):
            raise ConfigError(f"Share {index} in {path} uses an unsupported protocol.")
    if not isinstance(config["link_dir"], str):
        raise ConfigError(f"The link_dir field in {path} must be a string.")
    if not isinstance(config["links_enabled"], bool):
        raise ConfigError(f"The links_enabled field in {path} must be true or false.")
    if not isinstance(config["never_save_credentials"], bool):
        raise ConfigError(
            f"The never_save_credentials field in {path} must be true or false."
        )
    return config


def load_config():
    if CONFIG_PATH.exists():
        try:
            return _read_config(CONFIG_PATH)
        except ConfigError as primary_error:
            if BACKUP_PATH.exists():
                try:
                    config = _read_config(BACKUP_PATH)
                    logger.warning("Recovered configuration from %s", BACKUP_PATH)
                    return config
                except ConfigError:
                    pass
            raise primary_error

    # Native installs historically used ~/.config directly. Flatpak exposes
    # that directory read-only so the first sandboxed run can copy existing
    # shares into its persistent XDG_CONFIG_HOME.
    if LEGACY_CONFIG_PATH != CONFIG_PATH and LEGACY_CONFIG_PATH.exists():
        config = _read_config(LEGACY_CONFIG_PATH)
        save_config(config)
        logger.info("Migrated configuration from %s", LEGACY_CONFIG_PATH)
        return config
    return default_config()


def save_config(config):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        temporary_path = CONFIG_PATH.with_suffix(".tmp")
        with temporary_path.open("w") as config_file:
            json.dump(config, config_file, indent=2)
        temporary_path.replace(CONFIG_PATH)
        CONFIG_PATH.chmod(0o600)
        shutil.copy2(CONFIG_PATH, BACKUP_PATH)
        BACKUP_PATH.chmod(0o600)
    except (OSError, TypeError, ValueError) as error:
        raise ConfigError(f"Could not save {CONFIG_PATH}: {error}") from error
